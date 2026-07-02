#!/usr/bin/env python3
"""
Study-grade SCRIPTED collector (Phase A onward).

Ground truth = what you script, not what a model guesses:
  - you cue an activity (empty / stand / sit / walk / run),
  - you enter the position (a taped grid spot like "C3", or a walk path),
  - the tool records CSI from ALL receivers for a fixed interval and saves it
    with those gold labels + full metadata.

Supports 1..N receivers (the broadcast-TX + sniffer-RX firmware): pass all RX
serial ports; each is logged on this one PC so their streams share a clock.

Grid convention (home_L): origin bottom-left; columns A-F at x=0,30,..,150 in;
rows 1-5 at y=0,30,..,120 in. "C3" -> (60, 60).

Usage:
  python collect_scripted.py --ports COM17
  python collect_scripted.py --ports COM17,COM18,COM19 --room home_L --subject s1

Output: data/study/<room>/<subject>_<placement>_<ts>/
          session.json, segNN_<activity>.json, segNN_<activity>__<PORT>.csv
"""

import argparse
import csv
import datetime
import json
import os
import threading
import time

import serial
from log_csi import parse_line

FIELDS = ['pc_time', 'idx', 'rssi', 'rate', 'sig_mode', 'mcs', 'bw',
          'noise_floor', 'channel', 'local_us', 'n', 'csi']
ACTIVITIES = {'1': 'empty', '2': 'stand', '3': 'sit', '4': 'walk', '5': 'run'}
STATIONARY = {'stand', 'sit'}


def ask(prompt, default=''):
    s = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return s or default


def spot_to_xy(spot):
    """'C3' -> (60, 60) on the 30-in grid; returns None if not a grid spot."""
    spot = spot.strip().upper()
    if len(spot) >= 2 and spot[0].isalpha() and spot[1:].isdigit():
        col = ord(spot[0]) - ord('A')
        row = int(spot[1:]) - 1
        if 0 <= col <= 5 and 0 <= row <= 4:
            return [col * 30, row * 30]
    return None


class PortLogger(threading.Thread):
    def __init__(self, port, baud, csv_path, stop_evt):
        super().__init__(daemon=True)
        self.port, self.baud, self.csv_path, self.stop = port, baud, csv_path, stop_evt
        self.count = self.drops = 0
        self.last_idx = None
        self.err = None

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baud, timeout=1)
            try:
                ser.set_buffer_size(rx_size=1 << 20)   # tolerate CPU stalls (Windows)
            except Exception:
                pass
        except serial.SerialException as e:
            self.err = str(e)
            return
        with open(self.csv_path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            while not self.stop.is_set():
                raw = ser.readline().decode('utf-8', errors='ignore').strip()
                if not raw:
                    continue
                rec = parse_line(raw)
                if rec is None:
                    continue
                pc = time.time()
                w.writerow([f"{pc:.6f}", rec['idx'], rec['rssi'], rec['rate'],
                            rec['sig_mode'], rec['mcs'], rec['bw'], rec['noise_floor'],
                            rec['channel'], rec['local_us'], rec['n'],
                            ' '.join(map(str, rec['csi']))])
                self.count += 1
                if self.last_idx is not None and rec['idx'] > self.last_idx + 1:
                    self.drops += rec['idx'] - self.last_idx - 1
                self.last_idx = rec['idx']
        ser.close()


def countdown(n=3):
    for i in range(n, 0, -1):
        print(f"  starting in {i}...", end='\r')
        time.sleep(1)
    print("  RECORDING - hold the scenario!        ")


def record_segment(ports, baud, duration, out_prefix):
    stop = threading.Event()
    loggers = [PortLogger(p, baud, f"{out_prefix}__{p}.csv", stop) for p in ports]
    for lg in loggers:
        lg.start()
    time.sleep(0.4)
    for lg in loggers:
        if lg.err:
            print(f"  WARN {lg.port}: {lg.err}")
    t0 = time.time()
    while time.time() - t0 < duration:
        time.sleep(0.1)
    stop.set()
    for lg in loggers:
        lg.join(timeout=2)
    return {lg.port: {'packets': lg.count, 'drops': lg.drops} for lg in loggers}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ports', default='COM17', help='comma-separated RX serial ports')
    ap.add_argument('--baud', type=int, default=921600)
    ap.add_argument('--out', default='data/study')
    ap.add_argument('--room', default='home_L')
    ap.add_argument('--subject', default='s1')
    ap.add_argument('--duration', type=float, default=30.0, help='seconds per segment')
    args = ap.parse_args()
    ports = [p.strip() for p in args.ports.split(',') if p.strip()]

    print("=== Scripted CSI collection ===")
    print(f"Receivers: {ports}")
    placement = ask("Placement id (e.g. p1)", "p1")
    tx_xy = ask("TX position 'x,y' inches", "120,0")
    rx_xy = {p: ask(f"RX {p} position 'x,y' inches",
                    "30,0" if i == 0 else "") for i, p in enumerate(ports)}
    node_orientation = ask("Node orientation (e.g. flat / vertical / rot90)", "flat")
    channel = ask("Wi-Fi channel", "6")
    session_note = ask("Session note", "")

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    sess = f"{args.subject}_{placement}_{ts}"
    sdir = os.path.join(args.out, args.room, sess)
    os.makedirs(sdir, exist_ok=True)
    session_meta = {
        'room': args.room, 'subject': args.subject, 'placement': placement,
        'tx_xy_in': tx_xy, 'rx_xy_in': rx_xy, 'node_orientation': node_orientation,
        'channel': channel, 'ports': ports, 'baud': args.baud,
        'firmware': 'broadcast-TX + sniffer-RX', 'started': ts, 'note': session_note,
    }
    with open(os.path.join(sdir, 'session.json'), 'w') as f:
        json.dump(session_meta, f, indent=2)
    print(f"\nSession -> {sdir}")
    print("Tip: start with a ~30 s EMPTY segment (per-site calibration baseline).\n")

    tally, seg = {}, 0
    while True:
        print("\nActivity:  [1] empty  [2] stand  [3] sit  [4] walk  [5] run   [q] quit")
        c = input("choose: ").strip().lower()
        if c == 'q':
            break
        if c not in ACTIVITIES:
            print("  (1-5 or q)")
            continue
        activity = ACTIVITIES[c]

        position, path, direction, xy = '', '', '', None
        if activity in STATIONARY:
            position = ask("Grid spot (e.g. C3) or 'x,y'", "C3")
            xy = spot_to_xy(position)
        elif activity in ('walk', 'run'):
            path = ask("Path (e.g. A1->F1, perimeter, toward-RX)", "A1->F1")
            direction = ask("Direction (toward/away/lateral/mixed)", "lateral")
        note = ask("Segment note", "")
        try:
            dur = float(ask("Duration s", str(args.duration)))
        except ValueError:
            dur = args.duration

        input(f"\nSet up '{activity}'"
              + (f" at {position}" if position else (f" [{path}]" if path else ""))
              + ". Press Enter when ready...")
        countdown(3)

        seg += 1
        seg_ts = datetime.datetime.now().strftime('%H%M%S')
        prefix = os.path.join(sdir, f"seg{seg:02d}_{activity}_{seg_ts}")
        stats = record_segment(ports, args.baud, dur, prefix)

        seg_meta = {
            'segment': seg, 'label': activity, 'position': position, 'xy_in': xy,
            'path': path, 'direction': direction, 'duration_s': dur,
            'subject': args.subject, 'room': args.room, 'placement': placement,
            'node_orientation': node_orientation, 'tx_xy_in': tx_xy,
            'rx_xy_in': rx_xy, 'note': note, 'ts': seg_ts, 'per_port': stats,
        }
        with open(prefix + '.json', 'w') as f:
            json.dump(seg_meta, f, indent=2)

        tally[activity] = tally.get(activity, 0) + 1
        summary = "  ".join(f"{p}:{s['packets']}pk/{s['drops']}drop"
                            for p, s in stats.items())
        print(f"  saved seg{seg:02d} [{activity}] {summary}")
        if any(s['packets'] == 0 for s in stats.values()):
            print("  WARNING: a receiver logged 0 packets — check TX is broadcasting / same channel.")

    print(f"\nDone. {sum(tally.values())} segments: "
          + ", ".join(f"{k}={v}" for k, v in tally.items()))
    print(f"Saved to {sdir}")


if __name__ == '__main__':
    main()
