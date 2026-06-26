#!/usr/bin/env python3
"""
Log CSI from the csi_rx firmware to a CSV file (+ a JSON metadata sidecar).

Each row = one received packet. The CSI is stored as space-separated
interleaved I,Q integers so the whole complex channel is preserved for later
phases (amplitude AND phase). Amplitude is recomputed in analyze_csi.py.

For guided, consistently-labeled data collection use collect.py (which reuses
the functions here). Use this script for one-off captures and the Phase 0 test.

Example
-------
  python log_csi.py --port COM17 --duration 60 --label empty --room confA
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time

import serial   # pip install pyserial


def parse_line(line):
    """Parse one 'CSI_DATA,...,"[...]"' line into a dict, or None."""
    if not line.startswith("CSI_DATA"):
        return None
    b = line.find('[')
    e = line.rfind(']')
    if b == -1 or e == -1 or e <= b:
        return None
    try:
        vals = [int(x) for x in line[b + 1:e].split(',') if x.strip()]
    except ValueError:
        return None
    if not vals:
        return None

    pre = line[:b].split(',')   # CSI_DATA, idx, mac, rssi, rate, sig_mode,
                                # mcs, bw, noise_floor, channel, local_us, len, "

    def gi(i, default=-1):
        try:
            return int(pre[i])
        except (IndexError, ValueError):
            return default

    return {
        'idx':        gi(1),
        'rssi':       gi(3),
        'rate':       gi(4),
        'sig_mode':   gi(5),
        'mcs':        gi(6),
        'bw':         gi(7),
        'noise_floor': gi(8),
        'channel':    gi(9),
        'local_us':   gi(10),
        'n':          len(vals),
        'csi':        vals,
    }


def record_to_csv(port, baud, duration, csv_path, live=True):
    """Record CSI to csv_path. duration<=0 means record until Ctrl+C.

    Returns a stats dict. Raises serial.SerialException if the port won't open.
    """
    ser = serial.Serial(port, baud, timeout=1)

    fields = ['pc_time', 'idx', 'rssi', 'rate', 'sig_mode', 'mcs', 'bw',
              'noise_floor', 'channel', 'local_us', 'n', 'csi']
    count, drops = 0, 0
    last_idx, last_rssi = None, None
    t0 = time.time()
    t_report = t0

    try:
        with open(csv_path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(fields)
            while True:
                if duration and duration > 0 and (time.time() - t0) >= duration:
                    break
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
                count += 1

                if last_idx is not None and rec['idx'] > last_idx + 1:
                    drops += rec['idx'] - last_idx - 1
                last_idx, last_rssi = rec['idx'], rec['rssi']

                if live and pc - t_report >= 1.0:
                    rate = count / (pc - t0) if pc > t0 else 0
                    print(f"\r  {count:6d} pkts | {rate:6.1f} Hz | drops {drops:4d} "
                          f"| rssi {rec['rssi']:4d} | len {rec['n']:3d}   ", end='')
                    t_report = pc
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()

    dur = time.time() - t0
    return {
        'packets': count,
        'duration_s': round(dur, 3),
        'mean_rate_hz': round(count / dur, 2) if dur > 0 else 0,
        'idx_drops': drops,
        'last_rssi': last_rssi,
    }


def write_session_metadata(meta_path, meta):
    with open(meta_path, 'w') as mf:
        json.dump(meta, mf, indent=2)


def main():
    ap = argparse.ArgumentParser(description="Log ESP32-C3 CSI to CSV.")
    ap.add_argument('--port', default='COM17')
    ap.add_argument('--baud', type=int, default=921600)
    ap.add_argument('--duration', type=float, default=60,
                    help='seconds to record; 0 = until Ctrl+C')
    ap.add_argument('--out', default='data/raw')
    ap.add_argument('--label', default='session', help='class/scenario label')
    ap.add_argument('--room', default='')
    ap.add_argument('--person', default='')
    ap.add_argument('--posture', default='', help='sit / stand / walk / ...')
    ap.add_argument('--location', default='', help='where in the room')
    ap.add_argument('--distance', default='', help='TX-RX distance (inches)')
    ap.add_argument('--note', default='')
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(args.out, exist_ok=True)
    base = f"{args.label}_{ts}"
    csv_path = os.path.join(args.out, base + '.csv')
    meta_path = os.path.join(args.out, base + '.json')

    print(f"Logging {args.port} @ {args.baud} -> {csv_path}")
    print("Ctrl+C to stop." if args.duration == 0
          else f"Recording for {args.duration:.0f}s...")

    try:
        stats = record_to_csv(args.port, args.baud, args.duration, csv_path)
    except serial.SerialException as ex:
        print(f"ERROR: could not open {args.port}: {ex}")
        print("Close the Arduino Serial Monitor and verify the COM port.")
        sys.exit(1)

    meta = {
        'label': args.label, 'room': args.room, 'person': args.person,
        'posture': args.posture, 'location': args.location,
        'distance_in': args.distance, 'note': args.note,
        'port': args.port, 'baud': args.baud, 'started': ts, **stats,
    }
    write_session_metadata(meta_path, meta)

    print(f"\nSaved {stats['packets']} packets in {stats['duration_s']}s "
          f"(~{stats['mean_rate_hz']} Hz), drops={stats['idx_drops']}")
    print(f"  data: {csv_path}")
    print(f"  meta: {meta_path}")
    if stats['packets'] == 0:
        print("  WARNING: no CSI captured. Is the TX connected?")


if __name__ == '__main__':
    main()
