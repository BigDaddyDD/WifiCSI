#!/usr/bin/env python3
"""
Shared guided-collection GUI + stream slicer. NO serial in this process.

Same proven design as guided_collect.py (the tkinter process must never touch
the serial port on this machine), generalized so different protocols can drive
it: each segment is a dict with its own duration / get-ready / direction, so a
5-minute empty baseline and a directional-activity script reuse one code path.

Workflow (unchanged):
  Terminal A:  python stream_logger.py COM17 data/study/_live_stream.tsv
  Terminal B:  python collect_baseline.py ...   OR   python collect_activity.py ...

The GUI only shows prompts + countdowns and records each segment's start/stop PC
timestamp, then slices _live_stream.tsv into per-segment CSV + JSON at the end.

Segment dict keys:
  label      one of empty/stand/sit/walk/run
  dur        recording seconds (required)
  ready      get-ready seconds before recording (optional, default DEFAULT_READY)
  pos        free-text position for stand/sit (e.g. 'center', 'left', 'C2')
  direction  R2L / L2R / CW / CCW for walk/run (optional)
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

try:
    from collect_scripted import spot_to_xy
except Exception:                       # spot_to_xy only used for grid spots
    spot_to_xy = None

DEFAULT_STREAM = os.path.join('data', 'study', '_live_stream.tsv')
DEFAULT_READY = 5
FIELDS = ['pc_time', 'idx', 'rssi', 'rate', 'sig_mode', 'mcs', 'bw',
          'noise_floor', 'channel', 'local_us', 'n', 'csi']
COLORS = {'empty': '#455a64', 'stand': '#e08a1e', 'sit': '#c98a20',
          'walk': '#c0392b', 'run': '#8e44ad'}
DIRTEXT = {'R2L': 'right → left', 'L2R': 'left → right',
           'CW': 'clockwise loop', 'CCW': 'counter-clockwise loop', '': ''}

# --- live link-health monitor (tails the stream file; never touches serial) ---
HEALTH_POLL_MS = 700       # how often to re-assess the link
HEALTH_WINDOW = 2.5        # seconds of recent stream used for each assessment
TAIL_BYTES = 262144        # only read the last chunk of the (large) stream file
RATE_OK, RATE_BAD = 85, 60         # packets/s: >=OK green, <BAD red
LOSS_WARN, LOSS_BAD = 5.0, 15.0    # percent dropped: >WARN yellow, >BAD red
RSSI_WARN = -57                    # dBm: weaker than this is flagged
HEALTH_BG = {'ok': '#2e7d32', 'warn': '#f9a825', 'alarm': '#b71c1c',
             'idle': '#37474f'}


def instruction(seg):
    if seg.get('prompt'):                    # custom multi-line text (e.g. 2-person)
        return seg['prompt']
    lab = seg['label'].upper()
    if lab == 'EMPTY':
        return 'EMPTY\nleave the room / stay hidden'
    if lab in ('STAND', 'SIT'):
        return f"{lab}\n{seg.get('pos', '')}".rstrip()
    if lab in ('WALK', 'RUN'):
        sub = DIRTEXT.get(seg.get('direction', ''), seg.get('direction', '')) \
            or seg.get('pos', '')
        return f'{lab}\n{sub}'.rstrip()
    return lab


def _grid_xy(pos):
    if not pos or spot_to_xy is None:
        return None
    try:
        return spot_to_xy(pos)
    except Exception:
        return None


def _read_stream(path):
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


def _tail_stream(path, nbytes=TAIL_BYTES):
    """Read only the last `nbytes` of the (potentially large, still-growing)
    stream file and return the recent [(pc_time, raw), ...]. Cheap enough to
    poll a few times per second even while the logger appends to it."""
    try:
        sz = os.path.getsize(path)
        with open(path, 'rb') as f:
            if sz > nbytes:
                f.seek(sz - nbytes)
                f.readline()                 # drop the partial first line
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return []
    out = []
    for ln in data.splitlines():
        tab = ln.find('\t')
        if tab < 0:
            continue
        try:
            pc = float(ln[:tab])
        except ValueError:
            continue
        out.append((pc, ln[tab + 1:]))
    return out


def assess_link(path, now=None):
    """Assess the last ~HEALTH_WINDOW seconds of the stream: packet rate, packet
    loss (from gaps in the RX idx counter) and mean RSSI. Returns a dict with a
    'level' in {idle, ok, warn, alarm} and a human message."""
    now = time.time() if now is None else now
    recent = [(pc, raw) for pc, raw in _tail_stream(path)
              if now - pc <= HEALTH_WINDOW]
    if len(recent) < 3:
        return {'level': 'alarm',
                'msg': '⚠ NO CSI DATA — is stream_logger running?  '
                       'if yes, power-cycle BOTH ESPs',
                'rate': 0.0, 'loss': 0.0, 'rssi': None}
    span = max(recent[-1][0] - recent[0][0], 1e-3)
    rate = (len(recent) - 1) / span
    idxs, rssis = [], []
    for _pc, raw in recent:
        r = parse_line(raw)
        if r is None:
            continue
        idxs.append(r['idx']); rssis.append(r['rssi'])
    drops = expected = 0
    for a, b in zip(idxs, idxs[1:]):
        if b > a:                            # ignore counter resets/reconnects
            drops += b - a - 1
            expected += b - a
    loss = 100.0 * drops / expected if expected else 0.0
    rssi = sum(rssis) / len(rssis) if rssis else None
    rss = f'{rssi:.0f} dBm' if rssi is not None else '—'
    stat = f'{rate:.0f} Hz · loss {loss:.0f}% · RSSI {rss}'
    if rate < RATE_BAD or loss > LOSS_BAD:
        return {'level': 'alarm', 'rate': rate, 'loss': loss, 'rssi': rssi,
                'msg': f'⚠ BAD LINK — {stat}.  Move nodes closer / re-aim, or '
                       'power-cycle BOTH ESPs, then RE-RECORD'}
    if rate < RATE_OK or loss > LOSS_WARN or (rssi is not None and rssi < RSSI_WARN):
        return {'level': 'warn', 'rate': rate, 'loss': loss, 'rssi': rssi,
                'msg': f'weak link — {stat}.  Consider moving nodes closer'}
    return {'level': 'ok', 'rate': rate, 'loss': loss, 'rssi': rssi,
            'msg': f'link OK — {stat}'}


class App:
    def __init__(self, root, args, segments, session_type):
        self.root = root
        self.args = args
        self.segs_spec = segments
        self.session_type = session_type
        self.port = args.ports.split(',')[0].strip()
        self.stream = args.stream
        self.idx = 0
        self.phase = 'idle'
        self.remaining = 0
        self.after_id = None
        self.tally = {}
        self.segments = []

        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.sdir = os.path.join('data', 'study', args.room,
                                 f"{args.subject}_{args.placement}_{session_type}_{ts}")
        os.makedirs(self.sdir, exist_ok=True)
        self.rx = {self.port: args.rx}
        with open(os.path.join(self.sdir, 'session.json'), 'w') as f:
            json.dump({'room': args.room, 'subject': args.subject,
                       'placement': args.placement, 'tx_xy_in': args.tx,
                       'rx_xy_in': self.rx, 'node_orientation': args.orientation,
                       'config': f'{args.placement}/{args.orientation}',
                       'session_type': session_type,
                       'port': self.port, 'stream': self.stream, 'started': ts,
                       'firmware': 'AP-RX + STA-TX, core 2.0.17'}, f, indent=2)

        total = sum(s['dur'] for s in segments)
        root.title(f'CSI Collection — {session_type}')
        root.configure(bg='#222'); root.geometry('860x600')
        root.attributes('-topmost', True)
        self.f_prog = tkfont.Font(family='Helvetica', size=20)
        self.f_instr = tkfont.Font(family='Helvetica', size=50, weight='bold')
        self.f_phase = tkfont.Font(family='Helvetica', size=24, weight='bold')
        self.f_timer = tkfont.Font(family='Helvetica', size=140, weight='bold')
        self.f_health = tkfont.Font(family='Helvetica', size=15, weight='bold')
        # live link-health banner (top strip, always visible)
        self._last_level = None
        self.health_after = None
        self.health_var = tk.StringVar(value='checking link…')
        self.health_lbl = tk.Label(root, textvariable=self.health_var,
                                   font=self.f_health, fg='white',
                                   bg=HEALTH_BG['idle'], anchor='center')
        self.health_lbl.pack(side='top', fill='x', ipady=6)
        self.progress_var = tk.StringVar(
            value=f'{len(segments)} segments · {total}s recording total')
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
        root.bind('<Configure>', self._on_resize)
        self._health_loop()                  # start monitoring the link now

    def _health_loop(self):
        h = assess_link(self.stream)
        self.health_var.set(h['msg'])
        self.health_lbl.configure(bg=HEALTH_BG[h['level']])
        if h['level'] == 'alarm' and self._last_level != 'alarm':
            self.root.bell()                 # audible cue only on entering alarm
        self._last_level = h['level']
        self.health_after = self.root.after(HEALTH_POLL_MS, self._health_loop)

    def _on_resize(self, event):
        """Scale fonts + wrap the instruction to the current window size so the
        directions stay fully readable even when the window is small."""
        if event.widget is not self.root:
            return
        w, h = event.width, event.height
        self.f_prog.configure(size=max(10, int(h * 0.033)))
        self.f_instr.configure(size=max(14, int(h * 0.085)))
        self.f_phase.configure(size=max(12, int(h * 0.040)))
        self.f_timer.configure(size=max(28, int(h * 0.23)))
        self.f_health.configure(size=max(11, int(h * 0.028)))
        self.instr_lbl.configure(wraplength=max(200, w - 40))
        self.health_lbl.configure(wraplength=max(200, w - 20))

    def _stream_size(self):
        try:
            return os.path.getsize(self.stream)
        except OSError:
            return -1

    def start(self):
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
        if self.idx >= len(self.segs_spec):
            return self.done()
        seg = self.segs_spec[self.idx]
        self.root.configure(bg='#222'); self.instr_lbl.configure(bg='#222')
        self.progress_var.set(f'Step {self.idx + 1} / {len(self.segs_spec)}')
        self.instr_var.set(instruction(seg))
        self.phase_var.set('GET READY'); self.phase_lbl.config(fg='#4fc3f7')
        self.phase = 'ready'
        self.remaining = int(seg.get('ready', DEFAULT_READY))
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
        seg = self.segs_spec[self.idx]
        self.seg_ts = datetime.datetime.now().strftime('%H%M%S')
        self.seg_start = time.time()
        self.root.bell()
        col = seg.get('color') or COLORS.get(seg['label'], '#c0392b')
        self.root.configure(bg=col); self.instr_lbl.configure(bg=col)
        self.phase_var.set('● RECORDING'); self.phase_lbl.config(fg='white')
        self.phase = 'recording'
        self.remaining = int(seg['dur'])
        self._schedule()

    def _finish_record(self):
        seg = self.segs_spec[self.idx]
        end = time.time()
        rec = dict(seg)
        rec.update({'idx': self.idx + 1, 'start': self.seg_start, 'end': end,
                    'ts': self.seg_ts})
        self.segments.append(rec)
        cnt = sum(1 for pc, _ in _read_stream(self.stream)
                  if self.seg_start <= pc < end)
        self.tally[seg['label']] = self.tally.get(seg['label'], 0) + 1
        self.root.bell()
        if cnt < int(seg['dur']) * 50:
            self.phase_var.set(f'WARNING low ({cnt}) - stream running?')
        self.idx += 1
        self.root.after(700, self.run_segment)

    def _finalize(self):
        rows = _read_stream(self.stream)
        self.bad_segs = []
        for seg in self.segments:
            label = seg['label']
            prefix = os.path.join(self.sdir, f"seg{seg['idx']:02d}_{label}_{seg['ts']}")
            cnt = drops = 0; last = None
            with open(f"{prefix}__{self.port}.csv", 'w', newline='') as f:
                w = csv.writer(f); w.writerow(FIELDS)
                for pc, raw in rows:
                    if seg['start'] <= pc < seg['end']:
                        r = parse_line(raw)
                        if r is None:
                            continue
                        w.writerow([f"{pc:.6f}", r['idx'], r['rssi'], r['rate'],
                                    r['sig_mode'], r['mcs'], r['bw'], r['noise_floor'],
                                    r['channel'], r['local_us'], r['n'],
                                    ' '.join(map(str, r['csi']))])
                        cnt += 1
                        if last is not None and r['idx'] > last + 1:
                            drops += r['idx'] - last - 1
                        last = r['idx']
            loss = 100.0 * drops / (cnt + drops) if (cnt + drops) else 100.0
            if cnt < int(seg['dur']) * RATE_BAD or loss > LOSS_BAD:
                self.bad_segs.append(
                    f"seg{seg['idx']:02d} {label}: {cnt} pkts, {loss:.0f}% loss")
            pos = seg.get('pos', '')
            meta = {'segment': seg['idx'], 'label': label,
                    'count': seg.get('count'), 'people': seg.get('people', []),
                    'position': pos if label in ('stand', 'sit') else '',
                    'xy_in': _grid_xy(pos) if label in ('stand', 'sit') else None,
                    'path': seg.get('pos', '') if label in ('walk', 'run') else '',
                    'direction': seg.get('direction', ''),
                    'duration_s': int(seg['dur']), 'subject': self.args.subject,
                    'room': self.args.room, 'placement': self.args.placement,
                    'node_orientation': self.args.orientation,
                    'config': f'{self.args.placement}/{self.args.orientation}',
                    'session_type': self.session_type,
                    'tx_xy_in': self.args.tx, 'rx_xy_in': self.rx, 'ts': seg['ts'],
                    'per_port': {self.port: {'packets': cnt, 'drops': drops}}}
            with open(prefix + '.json', 'w') as f:
                json.dump(meta, f, indent=2)

    def done(self):
        self.instr_var.set('Saving...'); self.timer_var.set(''); self.root.update()
        self._finalize()
        bad = getattr(self, 'bad_segs', [])
        col = '#b71c1c' if bad else '#1b5e20'
        self.root.configure(bg=col); self.instr_lbl.configure(bg=col)
        self.progress_var.set('Session complete')
        if bad:
            self.instr_var.set('DONE — BAD SEGMENTS, RE-RECORD:\n' + '\n'.join(bad))
            print('BAD SEGMENTS in', self.sdir, '->', bad)
        else:
            self.instr_var.set('DONE ✔')
            self.phase_var.set('  '.join(f'{k}:{v}' for k, v in self.tally.items()))
        print('Saved session to', self.sdir)

    def quit(self):
        if self.health_after:
            self.root.after_cancel(self.health_after)
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self.segments:
            self._finalize()
        self.root.destroy()


def base_argparser(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument('--ports', default='COM17')
    ap.add_argument('--stream', default=DEFAULT_STREAM)
    ap.add_argument('--room', default='env2', help='new-environment name (folder under data/study/)')
    ap.add_argument('--subject', default='s1')
    ap.add_argument('--placement', default='p1')
    ap.add_argument('--orientation', default='flat')
    ap.add_argument('--tx', default='')
    ap.add_argument('--rx', default='')
    ap.add_argument('--dry-run', action='store_true', help='print the segment plan and exit')
    return ap


def run(args, segments, session_type):
    total = sum(s['dur'] for s in segments)
    overhead = sum(int(s.get('ready', DEFAULT_READY)) for s in segments)
    print(f"[{session_type}] room={args.room} config={args.placement}/{args.orientation}")
    print(f"{len(segments)} segments | {total}s recording + ~{overhead}s get-ready "
          f"= ~{(total + overhead) / 60:.1f} min")
    for i, s in enumerate(segments, 1):
        extra = s.get('direction', '') or s.get('pos', '')
        print(f"  {i:2d}. {s['label']:5s} {extra:22s} rec {s['dur']:>3}s  ready {int(s.get('ready', DEFAULT_READY))}s")
    if getattr(args, 'dry_run', False):
        print("(dry run — nothing recorded)")
        return
    root = tk.Tk()
    App(root, args, segments, session_type)
    root.mainloop()
