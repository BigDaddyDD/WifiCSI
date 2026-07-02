#!/usr/bin/env python3
"""A/B: does per-line parse+write slow the reader enough to stall the link?
Same open, main thread, back to back."""
import csv
import sys
import time

import serial
from log_csi import parse_line

port = sys.argv[1] if len(sys.argv) > 1 else 'COM17'
ser = serial.Serial(port, 921600, timeout=1)
time.sleep(1)


def phase(name, dur, mode):
    t0 = time.time(); n = 0
    fh = w = None
    if mode == 'write':
        fh = open('data/_ab_test.csv', 'w', newline=''); w = csv.writer(fh)
    while time.time() - t0 < dur:
        line = ser.readline().decode('utf-8', 'ignore').strip()
        if not line:
            continue
        if mode == 'count':
            if line.startswith('CSI_DATA'):
                n += 1
        else:
            rec = parse_line(line)
            if rec is None:
                continue
            w.writerow([f"{time.time():.6f}", rec['idx'], rec['n'],
                        ' '.join(map(str, rec['csi']))])
            n += 1
    if fh:
        fh.close()
    print(f'{name}: {n} in {dur}s (~{n/dur:.0f}/s)')


phase('A read-fast (startswith)', 6, 'count')
phase('B parse+write (logger)', 6, 'write')
ser.close()
