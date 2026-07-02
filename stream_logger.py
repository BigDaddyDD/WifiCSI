#!/usr/bin/env python3
"""
Continuous CSI stream logger (runs as its own process; no tkinter).
Opens the port once and appends every CSI_DATA line with a PC timestamp:
    <pc_time>\t<raw CSI_DATA line>
Line-buffered so nothing is lost when the GUI terminates it. Parsing happens
later in the split step. This is the same proven read path as read_serial.py.

Usage:  python stream_logger.py COM17 out_raw.tsv
"""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else 'COM17'
out = sys.argv[2] if len(sys.argv) > 2 else 'stream.tsv'
baud = int(sys.argv[3]) if len(sys.argv) > 3 else 921600

try:
    ser = serial.Serial(port, baud, timeout=1)
except Exception as e:
    sys.stderr.write(f"OPEN FAIL {port}: {e}\n")
    sys.stderr.flush()
    sys.exit(1)
sys.stderr.write(f"OPENED {port}\n")
sys.stderr.flush()

n = 0
hb = time.time()
with open(out, 'w', buffering=1, newline='') as f:   # line-buffered
    while True:
        try:
            line = ser.readline().decode('utf-8', 'ignore').strip()
        except Exception as e:
            sys.stderr.write(f"READ ERR: {e}\n"); sys.stderr.flush()
            break
        if line.startswith('CSI_DATA'):
            f.write(f"{time.time():.6f}\t{line}\n")
            n += 1
        if time.time() - hb > 2:
            hb = time.time()
            sys.stderr.write(f"HB read={n}\n")
            sys.stderr.flush()
