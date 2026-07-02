#!/usr/bin/env python3
"""Mirror the GUI's main-thread poll read (timeout=0 + in_waiting + read) to
check if THAT approach captures. If this reads ~2800/30s, the approach is fine
and the GUI bug is elsewhere; if 0, the timeout=0/in_waiting path is broken."""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else 'COM17'
dur = int(sys.argv[2]) if len(sys.argv) > 2 else 30
s = serial.Serial(port, 921600, timeout=0)
buf = b''
n = 0
t0 = time.time()
while time.time() - t0 < dur:
    w = s.in_waiting
    if w:
        buf += s.read(w)
    if b'\n' in buf:
        parts = buf.split(b'\n')
        buf = parts[-1]
        for raw in parts[:-1]:
            line = raw.decode('utf-8', 'ignore').strip()
            if line.startswith('CSI_DATA'):
                n += 1
    time.sleep(0.002)
s.close()
print(f'{n} in {dur}s (~{n // dur}/s)')
