#!/usr/bin/env python3
"""
Synchronized CSI + webcam auto-labeling capture  (the "data engine").

Records, on ONE PC clock:
  * csi.csv     - the CSI stream (same schema as log_csi.py)
  * labels.csv  - per-frame vision labels from YOLO-pose
  * meta.json   - take metadata + summary

Each continuous "take" is one session (group) for honest Leave-One-Session-Out
evaluation later. Just run it and behave naturally (walk, sit, stand, leave the
room); the camera labels every frame.

A live preview shows the boxes + auto-label so you can confirm the vision is
sane. Press 'q' in the preview window (or Ctrl+C) to stop.

Per-frame auto_label (the reliable part):
  no person            -> empty
  person + moving      -> moving
  person + not moving  -> still
posture (sit/stand), zone (left/center/right) and distance (near/mid/far) are
best-effort extras; they need a one-time room calibration to map to RX/TX side
and metres, so treat them as raw for now.

Setup:  pip install ultralytics opencv-python pyserial numpy
The YOLO model auto-downloads on first run.

Examples:
  python collect_av.py --label mixed --duration 0           # until 'q'
  python collect_av.py --no-csi                             # test vision only
"""

import argparse
import csv
import datetime
import json
import os
import threading
import time
from collections import deque

import numpy as np

try:
    import cv2
except ImportError:
    raise SystemExit("opencv not installed. Run:  pip install opencv-python")
try:
    from ultralytics import YOLO
except ImportError:
    raise SystemExit("ultralytics not installed. Run:  pip install ultralytics")

import serial
from log_csi import parse_line

# COCO keypoint indices
L_SH, R_SH, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK = 5, 6, 11, 12, 13, 14, 15, 16

# motion auto-calibration
ACT_WIN = 1.2            # seconds of position history used for the activity measure
AUTO_K = 4.0             # auto threshold = still_floor * K + base
AUTO_BASE = 0.6
ACT_MIN_SAMPLES = 40     # frames collected before the auto threshold engages


# --------------------------------------------------------------------------- #
#  CSI capture thread
# --------------------------------------------------------------------------- #
class CSIWriter(threading.Thread):
    def __init__(self, port, baud, csv_path):
        super().__init__(daemon=True)
        self.port, self.baud, self.csv_path = port, baud, csv_path
        self.stop_evt = threading.Event()
        self.count = self.drops = 0
        self.last_idx = None
        self.err = None

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baud, timeout=1)
        except serial.SerialException as e:
            self.err = str(e)
            return
        # Big RX buffer so YOLO CPU stalls don't overflow the port (Windows).
        # Default is ~4 KB (~66 ms at 100 Hz); 1 MB tolerates multi-second stalls.
        try:
            ser.set_buffer_size(rx_size=1 << 20)
        except Exception:
            pass
        fields = ['pc_time', 'idx', 'rssi', 'rate', 'sig_mode', 'mcs', 'bw',
                  'noise_floor', 'channel', 'local_us', 'n', 'csi']
        with open(self.csv_path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(fields)
            while not self.stop_evt.is_set():
                raw = ser.readline().decode('utf-8', errors='ignore').strip()
                if not raw:
                    continue
                rec = parse_line(raw)
                if rec is None:
                    continue
                pc = time.time()
                w.writerow([f"{pc:.6f}", rec['idx'], rec['rssi'], rec['rate'],
                            rec['sig_mode'], rec['mcs'], rec['bw'],
                            rec['noise_floor'], rec['channel'], rec['local_us'],
                            rec['n'], ' '.join(map(str, rec['csi']))])
                self.count += 1
                if self.last_idx is not None and rec['idx'] > self.last_idx + 1:
                    self.drops += rec['idx'] - self.last_idx - 1
                self.last_idx = rec['idx']
        ser.close()


# --------------------------------------------------------------------------- #
#  Vision helpers
# --------------------------------------------------------------------------- #
def classify_posture(xy, kc, min_conf=0.25):
    """xy: (17,2), kc: (17,) keypoint confidences.
    Returns (label, reason) where label is 'sit'/'stand'/'unknown'."""
    def ok(*idx):
        return all(kc[i] >= min_conf for i in idx)
    if not ok(L_SH, R_SH, L_HIP, R_HIP):
        return 'unknown', 'torso/hips not visible'
    sh_y = (xy[L_SH, 1] + xy[R_SH, 1]) / 2
    hip_y = (xy[L_HIP, 1] + xy[R_HIP, 1]) / 2
    torso = hip_y - sh_y
    if torso <= 1:
        return 'unknown', 'degenerate torso'
    if ok(L_ANK, R_ANK):
        ank_y = (xy[L_ANK, 1] + xy[R_ANK, 1]) / 2
        ratio = (ank_y - hip_y) / torso          # standing ~ legs extended
    elif ok(L_KNEE, R_KNEE):
        knee_y = (xy[L_KNEE, 1] + xy[R_KNEE, 1]) / 2
        ratio = (knee_y - hip_y) / torso * 2.0
    else:
        return 'unknown', 'legs not visible (table?)'
    if ratio >= 1.5:
        return 'stand', f'r={ratio:.2f}'
    if ratio <= 0.9:
        return 'sit', f'r={ratio:.2f}'
    return 'unknown', f'r={ratio:.2f} ambiguous'


def zone_of(cx, w):
    if cx < w / 3:
        return 'left'
    if cx > 2 * w / 3:
        return 'right'
    return 'center'


def distance_of(bbox_h, frame_h):
    f = bbox_h / frame_h
    if f > 0.6:
        return 'near'
    if f > 0.3:
        return 'mid'
    return 'far'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default='COM17')
    ap.add_argument('--baud', type=int, default=921600)
    ap.add_argument('--cam', type=int, default=0, help='webcam index')
    ap.add_argument('--model', default='yolo11n-pose.pt')
    ap.add_argument('--conf', type=float, default=0.4, help='detection confidence')
    ap.add_argument('--move-thresh', type=float, default=40.0,
                    help='centroid speed (px/s); logged only')
    ap.add_argument('--motion-thresh', type=float, default=0.0,
                    help='positional-activity threshold for moving. 0 = auto-calibrate '
                         'to your own stillness (recommended); >0 = fixed override')
    ap.add_argument('--imgsz', type=int, default=480,
                    help='YOLO inference size; lower (e.g. 320) = faster, fewer CSI drops')
    ap.add_argument('--duration', type=float, default=0, help='seconds; 0 = until q')
    ap.add_argument('--out', default='data/av')
    ap.add_argument('--label', default='take', help='take name')
    ap.add_argument('--room', default='confA')
    ap.add_argument('--person', default='p1')
    ap.add_argument('--no-csi', action='store_true', help='vision only (no serial)')
    ap.add_argument('--no-preview', action='store_true')
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    take_dir = os.path.join(args.out, f"{args.label}_{ts}")
    os.makedirs(take_dir, exist_ok=True)
    csi_path = os.path.join(take_dir, 'csi.csv')
    lab_path = os.path.join(take_dir, 'labels.csv')
    meta_path = os.path.join(take_dir, 'meta.json')

    print(f"Loading model {args.model} ...")
    model = YOLO(args.model)

    cap = cv2.VideoCapture(args.cam, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise SystemExit(f"Could not open webcam index {args.cam}")

    csi = None
    if not args.no_csi:
        csi = CSIWriter(args.port, args.baud, csi_path)
        csi.start()
        time.sleep(0.5)
        if csi.err:
            print(f"WARNING: CSI thread failed to open {args.port}: {csi.err}")
            print("Continuing with vision only.")
            csi = None

    lab_fields = ['pc_time', 'num_people', 'presence', 'auto_label', 'posture',
                  'zone', 'distance', 'moving', 'speed_px_s', 'motion_score', 'bbox']
    centroids = deque(maxlen=8)        # (t, cx, cy) for speed estimate (logged)
    ema_speed = 0.0
    pos_hist = deque()                 # (t, cxn, cyn, bhn) within ACT_WIN seconds
    act_hist = deque(maxlen=600)       # recent activity values for auto-calibration
    frames = 0
    counts = {}
    t0 = time.time()

    print(f"Recording -> {take_dir}")
    print("Behave naturally (walk / sit / stand / leave). q=quit, f=fullscreen.\n")

    WIN = 'CSI auto-label (q=quit, f=fullscreen)'
    fullscreen = False
    if not args.no_preview:
        # WINDOW_NORMAL = resizable; the frame scales to fill the window.
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WIN, 960, 540)

    with open(lab_path, 'w', newline='') as lf:
        lw = csv.writer(lf)
        lw.writerow(lab_fields)
        try:
            while True:
                ok, frame = cap.read()
                pc = time.time()
                if not ok:
                    continue
                if args.duration and (pc - t0) >= args.duration:
                    break

                h, w = frame.shape[:2]
                res = model(frame, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]

                motion_score = 0.0          # windowed positional activity, set below

                n_people = 0
                posture = zone = distance = 'na'
                posture_reason = ''
                moving = 0
                speed = 0.0
                bbox_str = ''

                if res.boxes is not None and len(res.boxes) > 0:
                    xyxy = res.boxes.xyxy.cpu().numpy()
                    areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
                    n_people = len(xyxy)
                    p = int(np.argmax(areas))             # primary = largest box
                    x1, y1, x2, y2 = xyxy[p]
                    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                    bbox_str = f"{x1:.0f} {y1:.0f} {x2:.0f} {y2:.0f}"
                    zone = zone_of(cx, w)
                    distance = distance_of(y2 - y1, h)

                    # Motion = how much your position wanders on screen over a short
                    # window: cx (left/right), cy (up/down) and box height (toward/away).
                    bh = y2 - y1
                    pos_hist.append((pc, cx / w, cy / h, bh / h))
                    while pos_hist and pc - pos_hist[0][0] > ACT_WIN:
                        pos_hist.popleft()
                    if len(pos_hist) >= 4:
                        arr = np.asarray(pos_hist)
                        motion_score = float((arr[:, 1].std() + arr[:, 2].std()
                                              + arr[:, 3].std()) * 100.0)

                    if res.keypoints is not None and len(res.keypoints) > p:
                        kxy = res.keypoints.xy.cpu().numpy()[p]
                        kc = (res.keypoints.conf.cpu().numpy()[p]
                              if res.keypoints.conf is not None
                              else np.ones(len(kxy)))
                        posture, posture_reason = classify_posture(kxy, kc)

                    centroids.append((pc, cx, cy))
                    if len(centroids) >= 2:
                        t_old, x_old, y_old = centroids[0]
                        dt = pc - t_old
                        if dt > 0:
                            inst = np.hypot(cx - x_old, cy - y_old) / dt
                            ema_speed = 0.5 * inst + 0.5 * ema_speed
                    speed = ema_speed
                else:
                    centroids.clear()
                    ema_speed = 0.0
                    pos_hist.clear()

                presence = int(n_people > 0)
                if presence and motion_score > 0:
                    act_hist.append(motion_score)
                if args.motion_thresh > 0:
                    auto_thr = args.motion_thresh
                elif len(act_hist) >= ACT_MIN_SAMPLES:
                    auto_thr = float(np.percentile(act_hist, 25)) * AUTO_K + AUTO_BASE
                else:
                    auto_thr = 2.0           # conservative until calibrated
                moving = int(presence and motion_score > auto_thr)
                if not presence:
                    auto = 'empty'
                elif moving:
                    auto = 'moving'
                else:
                    auto = 'still'
                counts[auto] = counts.get(auto, 0) + 1

                lw.writerow([f"{pc:.6f}", n_people, presence, auto, posture,
                             zone, distance, moving, f"{speed:.1f}",
                             f"{motion_score:.2f}", bbox_str])
                frames += 1

                if not args.no_preview:
                    annotated = res.plot()
                    color = {'empty': (200, 200, 200), 'still': (0, 165, 255),
                             'moving': (0, 0, 255)}.get(auto, (255, 255, 255))
                    cv2.putText(annotated,
                                f"{auto.upper()}  activity={motion_score:.1f} "
                                f"thr={auto_thr:.1f}  ppl={n_people}",
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    cv2.putText(annotated, f"posture={posture} ({posture_reason})",
                                (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                (180, 180, 180), 1)
                    if csi:
                        cv2.putText(annotated, f"CSI pkts={csi.count} drops={csi.drops}",
                                    (10, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                    (0, 255, 0), 1)
                    cv2.imshow(WIN, annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        break
                    elif key == ord('f'):
                        fullscreen = not fullscreen
                        cv2.setWindowProperty(
                            WIN, cv2.WND_PROP_FULLSCREEN,
                            cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            if csi:
                csi.stop_evt.set()
                csi.join(timeout=2)
            cap.release()
            cv2.destroyAllWindows()

    dur = time.time() - t0
    meta = {
        'label': args.label, 'room': args.room, 'person': args.person,
        'started': ts, 'duration_s': round(dur, 2),
        'frames': frames, 'frame_fps': round(frames / dur, 1) if dur else 0,
        'auto_label_frames': counts,
        'csi_packets': csi.count if csi else 0,
        'csi_drops': csi.drops if csi else 0,
        'model': args.model, 'move_thresh_px_s': args.move_thresh,
        'port': args.port, 'cam': args.cam,
    }
    with open(meta_path, 'w') as mf:
        json.dump(meta, mf, indent=2)

    print(f"\nDone in {dur:.1f}s.  frames={frames} (~{meta['frame_fps']} fps)")
    print(f"  auto-label frame counts: {counts}")
    if csi:
        print(f"  CSI: {csi.count} packets, {csi.drops} drops")
    print(f"  saved -> {take_dir}")


if __name__ == '__main__':
    main()
