#!/usr/bin/env python3
"""
Guided on-screen CSI collection (Tkinter GUI) — the GUI does NOT touch serial.

You run the proven logger yourself in a SEPARATE terminal:
    python stream_logger.py COM17 data/study/_live_stream.tsv
It streams every CSI line (with PC timestamp) to that file. This GUI only shows
the instruction + countdowns and records each segment's start/stop PC timestamp,
then slices the stream file into per-segment CSVs at the end. (A serial reader
launched *by* tkinter silently gets no data on this setup; a separately-launched
process works — hence this split.)

Usage:
  1) Terminal A:  python stream_logger.py COM17 data/study/_live_stream.tsv
  2) Terminal B:  python guided_collect.py --ports COM17 --orientation flat \
                      --tx 120,0 --rx 30,0
"""
import argparse
import csv
import datetime
import json
import os
import time
import tkinter as tk
import tkinter.font as tkfont

from log_csi import parse_line
from collect_scripted import spot_to_xy

READY_SECS = 6
DEFAULT_STREAM = os.path.join('data', 'study', '_live_stream.tsv')
FIELDS = ['pc_time', 'idx', 'rssi', 'rate', 'sig_mode', 'mcs', 'bw',
          'noise_floor', 'channel', 'local_us', 'n', 'csi']

# Rebalanced 2026-07 toward the data-starved failing classes (sit, run).
# Prior script over-sampled stand/walk; sit->stand and run->walk were the
# confusions and run/sit had the fewest windows. Positions use real taped grid
# spots (C3 prompts previously mislabeled -> now C2 to match the tape).
SEGMENTS = [
    ('empty', ''),
    ('stand', 'C2'), ('stand', 'E4'),
    ('sit', 'C2'), ('sit', 'E4'), ('sit', 'B2'),
    ('walk', 'A1->F1'),
    ('run', 'perimeter'), ('run', 'C1->C5'), ('run', 'in place @C3'),
    ('empty', ''),
]
COLORS = {'empty': '#607d8b', 'stand': '#e08a1e', 'sit': '#e08a1e',
          'walk': '#c0392b', 'run': '#8e44ad'}


def instruction(label, pos):
    lab = label.upper()
    if lab == 'EMPTY':
        return 'EMPTY\nleave the room'
    if lab in ('STAND', 'SIT'):
        return f'{lab}\nspot {pos}'
    if lab in ('WALK', 'RUN'):
        return f'{lab}\n{pos}'
    return lab


def _read_stream(path):
    """Return list of (pc_time, raw_line) from the stream file."""
    out = []
    try:
        with open(path) as f:
            for ln in f:
                tab = ln.find('\t')
                if tab < 0:
                    continue
                try:
                    out.append((float(ln[:tab]), ln[tab + 1:].strip()))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out


class App:
    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.port = args.ports.split(',')[0].strip()
        self.stream = args.stream
        self.dur = int(args.duration)
        self.idx = 0
        self.phase = 'idle'
        self.remaining = 0
        self.after_id = None
        self.tally = {}
        self.segments = []

        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.sdir = os.path.join('data', 'study', args.room,
                                 f"{args.subject}_{args.placement}_{ts}")
        os.makedirs(self.sdir, exist_ok=True)
        self.rx = {self.port: args.rx}
        with open(os.path.join(self.sdir, 'session.json'), 'w') as f:
            json.dump({'room': args.room, 'subject': args.subject,
                       'placement': args.placement, 'tx_xy_in': args.tx,
                       'rx_xy_in': self.rx, 'node_orientation': args.orientation,
                       'port': self.port, 'stream': self.stream, 'started': ts,
                       'firmware': 'AP-RX + STA-TX, core 2.0.17'}, f, indent=2)

        root.title('CSI Guided Collection')
        root.configure(bg='#222'); root.geometry('820x560')
        root.attributes('-topmost', True)
        self.f_prog = tkfont.Font(family='Helvetica', size=20)
        self.f_instr = tkfont.Font(family='Helvetica', size=52, weight='bold')
        self.f_phase = tkfont.Font(family='Helvetica', size=24, weight='bold')
        self.f_timer = tkfont.Font(family='Helvetica', size=140, weight='bold')
        self.progress_var = tk.StringVar(value=f'{len(SEGMENTS)} segments · {self.dur}s each')
        self.instr_var = tk.StringVar(value='Start stream_logger, then click START')
        self.phase_var = tk.StringVar(value='')
        self.timer_var = tk.StringVar(value='')
        tk.Label(root, textvariable=self.progress_var, font=self.f_prog,
                 fg='#bbb', bg='#222').pack(pady=(16, 4))
        self.instr_lbl = tk.Label(root, textvariable=self.instr_var, font=self.f_instr,
                                  fg='white', bg='#222', justify='center')
        self.instr_lbl.pack(pady=6)
        self.phase_lbl = tk.Label(root, textvariable=self.phase_var, font=self.f_phase,
                                  fg='#8fd', bg='#222')
        self.phase_lbl.pack()
        tk.Label(root, textvariable=self.timer_var, font=self.f_timer,
                 fg='white', bg='#222').pack()
        btns = tk.Frame(root, bg='#222'); btns.pack(side='bottom', pady=14)
        self.start_btn = tk.Button(btns, text='START', font=self.f_phase, command=self.start,
                                   bg='#2e7d32', fg='white', width=10)
        self.start_btn.pack(side='left', padx=8)
        tk.Button(btns, text='Quit', font=self.f_phase, command=self.quit,
                  bg='#555', fg='white', width=6).pack(side='left', padx=8)
        root.protocol('WM_DELETE_WINDOW', self.quit)

    def _stream_size(self):
        try:
            return os.path.getsize(self.stream)
        except OSError:
            return -1

    def start(self):
        # verify the external stream_logger is actually writing
        s0 = self._stream_size()
        time.sleep(1.0)
        s1 = self._stream_size()
        if s1 < 0 or s1 == s0:
            self.instr_var.set('No stream! Start:\npython stream_logger.py '
                               + f'{self.port} {self.stream}')
            return
        self.start_btn.config(state='disabled')
        self.run_segment()

    def run_segment(self):
        if self.idx >= len(SEGMENTS):
            return self.done()
        label, pos = SEGMENTS[self.idx]
        self.root.configure(bg='#222'); self.instr_lbl.configure(bg='#222')
        self.progress_var.set(f'Step {self.idx + 1} / {len(SEGMENTS)}')
        self.instr_var.set(instruction(label, pos))
        self.phase_var.set('GET READY'); self.phase_lbl.config(fg='#4fc3f7')
        self.phase = 'ready'; self.remaining = READY_SECS
        self._schedule()

    def _schedule(self):
        self.timer_var.set(str(self.remaining))
        self.after_id = self.root.after(1000, self._tick)

    def _tick(self):
        self.remaining -= 1
        if self.remaining > 0:
            self.timer_var.set(str(self.remaining))
            self.after_id = self.root.after(1000, self._tick)
            return
        if self.phase == 'ready':
            self._begin_record()
        elif self.phase == 'recording':
            self._finish_record()

    def _begin_record(self):
        label, pos = SEGMENTS[self.idx]
        self.seg_ts = datetime.datetime.now().strftime('%H%M%S')
        self.seg_start = time.time()
        self.root.bell()
        col = COLORS.get(label, '#c0392b')
        self.root.configure(bg=col); self.instr_lbl.configure(bg=col)
        self.phase_var.set('● RECORDING'); self.phase_lbl.config(fg='white')
        self.phase = 'recording'; self.remaining = self.dur
        self._schedule()

    def _finish_record(self):
        label, pos = SEGMENTS[self.idx]
        end = time.time()
        self.segments.append({'idx': self.idx + 1, 'label': label, 'pos': pos,
                              'start': self.seg_start, 'end': end, 'ts': self.seg_ts})
        # feedback: how many stream lines fell in this window
        cnt = sum(1 for pc, _ in _read_stream(self.stream)
                  if self.seg_start <= pc < end)
        self.tally[label] = self.tally.get(label, 0) + 1
        self.root.bell()
        if cnt < self.dur * 50:
            self.phase_var.set(f'WARNING low ({cnt}) - stream running?')
        self.idx += 1
        self.root.after(700, self.run_segment)

    def _finalize(self):
        rows = _read_stream(self.stream)
        for seg in self.segments:
            prefix = os.path.join(self.sdir, f"seg{seg['idx']:02d}_{seg['label']}_{seg['ts']}")
            cnt = drops = 0; last = None
            with open(f"{prefix}__{self.port}.csv", 'w', newline='') as f:
                w = csv.writer(f); w.writerow(FIELDS)
                for pc, raw in rows:
                    if seg['start'] <= pc < seg['end']:
                        rec = parse_line(raw)
                        if rec is None:
                            continue
                        w.writerow([f"{pc:.6f}", rec['idx'], rec['rssi'], rec['rate'],
                                    rec['sig_mode'], rec['mcs'], rec['bw'], rec['noise_floor'],
                                    rec['channel'], rec['local_us'], rec['n'],
                                    ' '.join(map(str, rec['csi']))])
                        cnt += 1
                        if last is not None and rec['idx'] > last + 1:
                            drops += rec['idx'] - last - 1
                        last = rec['idx']
            xy = spot_to_xy(seg['pos']) if seg['label'] in ('stand', 'sit') else None
            meta = {'segment': seg['idx'], 'label': seg['label'],
                    'position': seg['pos'] if seg['label'] in ('stand', 'sit') else '',
                    'xy_in': xy, 'path': seg['pos'] if seg['label'] in ('walk', 'run') else '',
                    'duration_s': self.dur, 'subject': self.args.subject,
                    'room': self.args.room, 'placement': self.args.placement,
                    'node_orientation': self.args.orientation, 'tx_xy_in': self.args.tx,
                    'rx_xy_in': self.rx, 'ts': seg['ts'],
                    'per_port': {self.port: {'packets': cnt, 'drops': drops}}}
            with open(prefix + '.json', 'w') as f:
                json.dump(meta, f, indent=2)

    def done(self):
        self.instr_var.set('Saving...'); self.timer_var.set(''); self.root.update()
        self._finalize()
        self.root.configure(bg='#1b5e20'); self.instr_lbl.configure(bg='#1b5e20')
        self.progress_var.set('Session complete')
        self.instr_var.set('DONE ✔')
        self.phase_var.set('  '.join(f'{k}:{v}' for k, v in self.tally.items()))
        print('Saved session to', self.sdir)

    def quit(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self.segments:
            self._finalize()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ports', default='COM17')
    ap.add_argument('--stream', default=DEFAULT_STREAM)
    ap.add_argument('--room', default='home_L')
    ap.add_argument('--subject', default='s1')
    ap.add_argument('--placement', default='p1')
    ap.add_argument('--orientation', default='flat')
    ap.add_argument('--tx', default='120,0')
    ap.add_argument('--rx', default='30,0')
    ap.add_argument('--duration', type=int, default=30)
    args = ap.parse_args()
    root = tk.Tk()
    App(root, args)
    root.mainloop()


if __name__ == '__main__':
    main()
