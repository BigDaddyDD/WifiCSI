#!/usr/bin/env python3
"""
Wireless live presence/activity monitor.

Connects over Wi-Fi (TCP, NOT USB) to firmware/csi_rx_wifi. RX joins your home
Wi-Fi directly (AP+STA -- see CLAUDE.md 4f), so your laptop stays on its own
normal Wi-Fi connection -- no dongle, no joining "csi_link" required. No cable
to the RX board, so you can walk around with the laptop while the two ESP32s
stay fixed and running on wall/battery power.

Workflow:
  1. Connect            -- opens the TCP socket to the RX (default
                            csi-rx.local:3333) and starts buffering CSI.
  2. Capture baseline    -- ~60s with the room EMPTY. This is the per-install
                            calibration step every result in this project
                            depends on -- do not skip it, and do not walk
                            through frame during it.
  3. Start monitoring    -- every ~1s, classifies the last 2s of CSI with the
                            pre-trained model (model_store/live_model.joblib,
                            built by train_live_model.py) calibrated against
                            the baseline just captured, and displays presence
                            + activity + per-class confidence live.

This is a DIFFERENT tool from collect_gui.py -- that one records labeled
sessions for training; this one runs the already-trained model live for
demoing/spot-checking, wirelessly.

Usage:
  python live_monitor_gui.py [--host csi-rx.local] [--port 3333]
                              [--baseline-secs 60] [--model model_store/live_model.joblib]
  # pair 2 (stock antenna): --host csi-rx2.local
"""
import argparse
import collections
import socket
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

import joblib
import numpy as np

import csi_dataset as cd
import phase_a_analysis as pa
from log_csi import parse_line

CLASS_COLORS = {'empty': '#455a64', 'stand': '#e08a1e', 'sit': '#c98a20',
                'walk': '#c0392b', 'run': '#8e44ad'}
CLASS_ORDER = ['empty', 'stand', 'sit', 'walk', 'run']

HEALTH_BG = {'ok': '#2e7d32', 'warn': '#f9a825', 'alarm': '#b71c1c',
            'idle': '#37474f'}
# RX relays at the full native CSI rate now (ring-buffer redesign moved the
# TCP write out of the CSI callback and into loop() -- see CLAUDE.md 4f), so
# these match the same expectations as the wired path.
RATE_OK, RATE_BAD = 85, 60
LIVE_HOP_MS = 1000            # re-classify once per second


# ---------------------------------------------------------------------------
# Networking + streaming buffer (runs on a background thread; the Tk main
# thread only ever reads snapshots under the lock -- no socket I/O in Tk).
# ---------------------------------------------------------------------------
class CSIStream:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.lock = threading.Lock()
        self.buf = collections.deque(maxlen=1200)     # ~12s at ~97 Hz
        self.recent_pc = collections.deque(maxlen=400)  # (pc_time,) for rate/health
        self.connected = False
        self.last_rssi = None
        self.last_error = ''
        self._stop = False
        self._sock = None
        self._us_offset = 0.0
        self._last_raw_us = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def _unwrap(self, raw_us):
        if self._last_raw_us is not None and raw_us < self._last_raw_us - 2_000_000_000:
            self._us_offset += 2.0 ** 32
        self._last_raw_us = raw_us
        return raw_us + self._us_offset

    def _run(self):
        buf = b''
        while not self._stop:
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=5)
                self._sock.settimeout(1.0)
                with self.lock:
                    self.connected = True
                    self.last_error = ''
                buf = b''
                while not self._stop:
                    try:
                        chunk = self._sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        self._handle_line(line.decode('utf-8', 'replace').strip())
            except OSError as e:
                with self.lock:
                    self.last_error = str(e)
            with self.lock:
                self.connected = False
            if not self._stop:
                time.sleep(2.0)          # reconnect backoff (e.g. walked out of range)

    def _handle_line(self, line):
        r = parse_line(line)
        if r is None:
            return
        vals = r['csi']
        if len(vals) < 4:
            return
        a = np.asarray(vals, float)
        re_, im_ = a[0::2], a[1::2]
        m = min(len(re_), len(im_))
        if m < 32:                 # malformed/truncated packet
            return
        amp = np.sqrt(re_[:m] ** 2 + im_[:m] ** 2)
        us = self._unwrap(float(r['local_us']))
        now = time.time()
        with self.lock:
            self.buf.append((us, amp))
            self.recent_pc.append(now)
            self.last_rssi = r['rssi']

    def snapshot_health(self, window_s=2.5):
        now = time.time()
        with self.lock:
            connected = self.connected
            err = self.last_error
            rssi = self.last_rssi
            recent = [t for t in self.recent_pc if now - t <= window_s]
        rate = len(recent) / window_s if recent else 0.0
        if not connected:
            return {'level': 'alarm', 'rate': 0.0, 'rssi': rssi,
                    'msg': f'not connected{": " + err if err else ""} -- retrying...'}
        if rate < RATE_BAD:
            return {'level': 'alarm', 'rate': rate, 'rssi': rssi,
                    'msg': f'connected but no/weak CSI ({rate:.0f} Hz) -- check TX + range'}
        if rate < RATE_OK:
            return {'level': 'warn', 'rate': rate, 'rssi': rssi,
                    'msg': f'weak stream -- {rate:.0f} Hz, RSSI {rssi} dBm'}
        return {'level': 'ok', 'rate': rate, 'rssi': rssi,
                'msg': f'link OK -- {rate:.0f} Hz, RSSI {rssi} dBm'}

    def snapshot_all(self):
        with self.lock:
            return list(self.buf)

    def snapshot_since(self, mark_len):
        """Everything appended after a previous snapshot of length `mark_len`."""
        with self.lock:
            return list(self.buf)[mark_len:], len(self.buf)


# ---------------------------------------------------------------------------
# Feature pipeline -- mirrors analyze_liv.calibrated()/build() exactly so the
# persisted model sees the same features it was trained on.
# ---------------------------------------------------------------------------
def window_features(A, mask):
    """A: [T, 64] raw amplitude. Returns MV, SV, MO over the masked subcarriers."""
    W = A[:, mask]
    MV = W.mean(0); SV = W.std(0); MO = pa.motion_frac(W)
    return MV, SV, MO


def raw_feature_vec(MV, SV, MO, base):
    rel = (MV - base) / (base + 1e-6)
    summ = [np.abs(rel).mean(), np.abs(rel).max(), np.linalg.norm(rel), SV.mean()]
    return np.concatenate([rel, SV, MO, summ])


def compute_baseline(samples, mask, win, hop):
    """samples: [(us, amp[64]), ...] from the empty-room capture. Returns
    (base, mu, sd) -- the SAME two-stage calibration as training (subtract
    empty baseline, then z-score against the calibration set's own spread)."""
    tus = np.array([s[0] for s in samples], float)
    amp = np.array([s[1] for s in samples])
    rssi = np.zeros(len(tus))
    ampU, _ = pa._resample(amp, rssi, tus, pa.RESAMPLE_FS)
    if len(ampU) < win:
        return None
    windows = [window_features(ampU[lo:hi], mask)
              for lo, hi in cd.window_indices(len(ampU), win, hop)]
    base = np.mean([MV for MV, SV, MO in windows], axis=0)
    Xcal = np.array([raw_feature_vec(MV, SV, MO, base) for MV, SV, MO in windows])
    mu = Xcal.mean(0); sd = Xcal.std(0) + 1e-6
    return base, mu, sd


def resample_last_window(buf, win, fs=100.0):
    """buf: [(us, amp[64]), ...] ascending by time. Returns a [win, 64] array
    ending at the most recent sample, or None if there isn't enough history."""
    if len(buf) < win // 2:
        return None
    tus = np.array([b[0] for b in buf], float)
    amp = np.array([b[1] for b in buf])
    t = (tus - tus[-1]) / 1e6                 # seconds relative to "now" = 0
    span = win / fs
    if t[0] > -span * 0.9:                    # not enough history yet
        return None
    grid = np.linspace(-span + 1.0 / fs, 0.0, win)
    out = np.empty((win, amp.shape[1]))
    for s in range(amp.shape[1]):
        out[:, s] = np.interp(grid, t, amp[:, s])
    return out


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.model = joblib.load(args.model)
        self.clf = self.model['clf']
        self.mask = self.model['mask']
        self.win = self.model['win']
        self.classes = list(self.clf.classes_)
        self.stream = None
        self.baseline = None            # (base, mu, sd) once captured
        self.mode = 'idle'              # idle | connecting | baseline | live
        self._baseline_mark = 0
        self._baseline_deadline = 0

        root.title('CSI Live Monitor (wireless)')
        root.configure(bg='#222'); root.geometry('900x680')
        f_big = tkfont.Font(family='Helvetica', size=46, weight='bold')
        f_med = tkfont.Font(family='Helvetica', size=18, weight='bold')
        f_small = tkfont.Font(family='Helvetica', size=12)

        self.health_var = tk.StringVar(value='not connected')
        self.health_lbl = tk.Label(root, textvariable=self.health_var, font=f_small,
                                   fg='white', bg=HEALTH_BG['idle'])
        self.health_lbl.pack(side='top', fill='x', ipady=6)

        self.status_var = tk.StringVar(value='Click Connect to begin.')
        tk.Label(root, textvariable=self.status_var, font=f_med, fg='#ddd',
                bg='#222').pack(pady=(14, 4))

        self.big_var = tk.StringVar(value='--')
        self.big_lbl = tk.Label(root, textvariable=self.big_var, font=f_big,
                                fg='white', bg='#222')
        self.big_lbl.pack(pady=10)

        self.bars_frame = tk.Frame(root, bg='#222')
        self.bars_frame.pack(pady=6, fill='x', padx=40)
        self.bar_widgets = {}
        for cls in CLASS_ORDER:
            row = tk.Frame(self.bars_frame, bg='#222'); row.pack(fill='x', pady=2)
            tk.Label(row, text=cls, font=f_small, fg='#ccc', bg='#222', width=8,
                    anchor='w').pack(side='left')
            canvas = tk.Canvas(row, height=18, bg='#111', highlightthickness=0)
            canvas.pack(side='left', fill='x', expand=True, padx=(6, 6))
            pct = tk.Label(row, text='0%', font=f_small, fg='#ccc', bg='#222', width=5)
            pct.pack(side='left')
            self.bar_widgets[cls] = (canvas, pct)

        btns = tk.Frame(root, bg='#222'); btns.pack(pady=14)
        self.connect_btn = tk.Button(btns, text='Connect', font=f_med,
                                     command=self.on_connect, bg='#2e7d32', fg='white')
        self.connect_btn.pack(side='left', padx=6)
        self.baseline_btn = tk.Button(btns, text=f'Capture baseline ({args.baseline_secs}s)',
                                      font=f_med, command=self.on_baseline,
                                      bg='#455a64', fg='white', state='disabled')
        self.baseline_btn.pack(side='left', padx=6)
        self.live_btn = tk.Button(btns, text='Start monitoring', font=f_med,
                                  command=self.on_live, bg='#455a64', fg='white',
                                  state='disabled')
        self.live_btn.pack(side='left', padx=6)

        self.log = tk.Text(root, height=8, bg='#111', fg='#8fd', font=('Consolas', 10),
                           insertbackground='white')
        self.log.pack(fill='both', expand=True, padx=14, pady=(4, 14))

        root.protocol('WM_DELETE_WINDOW', self.quit)
        self._tick()

    def _log(self, msg):
        self.log.insert('end', f'{time.strftime("%H:%M:%S")}  {msg}\n')
        self.log.see('end')

    def on_connect(self):
        self.stream = CSIStream(self.args.host, self.args.port)
        self.mode = 'connecting'
        self.status_var.set(f'Connecting to {self.args.host}:{self.args.port} ...')
        self.connect_btn.config(state='disabled')

    def on_baseline(self):
        self.mode = 'baseline'
        self._baseline_mark = len(self.stream.snapshot_all())
        self._baseline_deadline = time.time() + self.args.baseline_secs
        self.baseline_btn.config(state='disabled')
        self.live_btn.config(state='disabled')
        self.status_var.set('Capturing baseline -- leave the room EMPTY now.')
        self._log(f'Baseline capture started ({self.args.baseline_secs}s, room must be empty)')

    def on_live(self):
        self.mode = 'live'
        self.status_var.set('Live monitoring')
        self._log('Live monitoring started')
        self._next_classify = time.time()

    def _finish_baseline(self):
        all_buf = self.stream.snapshot_all()
        samples = all_buf[self._baseline_mark:]
        if len(samples) < self.win:
            self._log(f'Baseline too short ({len(samples)} samples) -- retry, '
                     'check the link is up first.')
            self.mode = 'idle'
            self.baseline_btn.config(state='normal')
            return
        result = compute_baseline(samples, self.mask, self.win, self.model['hop'])
        if result is None:
            self._log('Baseline computation failed (not enough resampled data) -- retry.')
            self.mode = 'idle'
            self.baseline_btn.config(state='normal')
            return
        self.baseline = result
        self.mode = 'idle'
        self.baseline_btn.config(state='normal')
        self.live_btn.config(state='normal')
        self.status_var.set('Baseline ready. Click Start monitoring.')
        self._log(f'Baseline captured OK ({len(samples)} raw samples).')

    def _classify_once(self):
        buf = self.stream.snapshot_all()
        W = resample_last_window(buf, self.win)
        if W is None:
            return
        base, mu, sd = self.baseline
        MV, SV, MO = window_features(W, self.mask)
        x = raw_feature_vec(MV, SV, MO, base)
        x = (x - mu) / sd
        proba = self.clf.predict_proba(x.reshape(1, -1))[0]
        proba_by_class = dict(zip(self.classes, proba))
        pred = self.classes[int(np.argmax(proba))]
        self._update_display(pred, proba_by_class)

    def _update_display(self, pred, proba_by_class):
        presence = 'EMPTY' if pred == 'empty' else 'OCCUPIED'
        self.big_var.set(f'{presence}\n{pred.upper()}')
        self.big_lbl.config(fg='white', bg=CLASS_COLORS.get(pred, '#333'))
        self.root.configure(bg=CLASS_COLORS.get(pred, '#222'))
        for cls in CLASS_ORDER:
            canvas, pct = self.bar_widgets[cls]
            p = proba_by_class.get(cls, 0.0)
            canvas.delete('all')
            w = canvas.winfo_width() or 300
            canvas.create_rectangle(0, 0, w * p, 18, fill=CLASS_COLORS[cls], width=0)
            pct.config(text=f'{p*100:.0f}%')
        conf = proba_by_class.get(pred, 0.0)
        self._log(f'{presence:9s} {pred:6s}  conf={conf:.2f}')

    def _tick(self):
        if self.stream is not None:
            h = self.stream.snapshot_health()
            self.health_var.set(h['msg'])
            self.health_lbl.config(bg=HEALTH_BG[h['level']])
            if self.mode == 'connecting' and h['level'] != 'alarm':
                self.mode = 'idle'
                self.baseline_btn.config(state='normal')
                self.status_var.set('Connected. Capture an empty-room baseline next.')
                self._log('Connected to RX.')
            if self.mode == 'baseline' and time.time() >= self._baseline_deadline:
                self._finish_baseline()
            if self.mode == 'live' and time.time() >= getattr(self, '_next_classify', 0):
                self._next_classify = time.time() + LIVE_HOP_MS / 1000.0
                self._classify_once()
        self.root.after(300, self._tick)

    def quit(self):
        if self.stream:
            self.stream.stop()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='csi-rx.local')
    ap.add_argument('--port', type=int, default=3333)
    ap.add_argument('--baseline-secs', type=int, default=60)
    ap.add_argument('--model', default='model_store/live_model.joblib')
    args = ap.parse_args()

    root = tk.Tk()
    App(root, args)
    root.mainloop()


if __name__ == '__main__':
    main()
