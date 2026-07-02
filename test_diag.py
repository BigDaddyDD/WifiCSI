#!/usr/bin/env python3
"""Main-thread: count raw CSI_DATA lines vs successfully parsed, to isolate
whether the read or parse_line is the problem."""
import sys
import time

import serial
from log_csi import parse_line

port = sys.argv[1] if len(sys.argv) > 1 else 'COM17'
dur = int(sys.argv[2]) if len(sys.argv) > 2 else 30
ser = serial.Serial(port, 921600, timeout=1)
t0 = time.time()
raw = parsed = other = 0
sample_fail = None
while time.time() - t0 < dur:
    line = ser.readline().decode('utf-8', 'ignore').strip()
    if not line:
        continue
    if line.startswith('CSI_DATA'):
        raw += 1
        if parse_line(line) is not None:
            parsed += 1
        elif sample_fail is None:
            sample_fail = line[:160]
    else:
        other += 1
ser.close()
print(f"raw CSI_DATA lines: {raw}  parsed OK: {parsed}  other lines: {other}")
if sample_fail:
    print("a line that FAILED parse:", sample_fail)
