#!/usr/bin/env python3
"""Isolated test of the guided_collect PersistentLogger (no GUI/tkinter)."""
import sys
import time

from guided_collect import PersistentLogger

port = sys.argv[1] if len(sys.argv) > 1 else 'COM17'
lg = PersistentLogger(port, 921600)
lg.start()
time.sleep(1.0)
if lg.err:
    print('OPEN FAILED:', lg.err)
    raise SystemExit
dur = int(sys.argv[2]) if len(sys.argv) > 2 else 10
lg.start_file('data/_plogger_test.csv')
print(f'recording {dur}s...')
time.sleep(dur)
st = lg.stop_file()
lg.close()
print(f"RESULT: {st['packets']} packets in {dur}s (~{st['packets']//dur}/s), drops={st['drops']}")
