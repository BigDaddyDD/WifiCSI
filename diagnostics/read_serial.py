#!/usr/bin/env python3
"""
Read one or more ESP serial ports for a few seconds and print a clean summary.
Non-CSI lines (banners, [diag], ERR) are printed verbatim; CSI_DATA is sampled
+ counted so it doesn't flood.

Usage:  python read_serial.py --ports COM17,COM18 --secs 12
NOTE: close the Arduino IDE Serial Monitor first (one program per COM port).
"""
import argparse
import threading
import time

import serial

_lock = threading.Lock()


def reader(port, baud, dur):
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except Exception as e:
        with _lock:
            print(f"[{port}] OPEN FAILED: {e}")
        return
    csi = 0
    t0 = time.time()
    while time.time() - t0 < dur:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
        except Exception as e:
            with _lock:
                print(f"[{port}] read error: {e}")
            break
        if not line:
            continue
        if line.startswith('CSI_DATA'):
            csi += 1
            if csi <= 2:
                with _lock:
                    print(f"[{port}] SAMPLE {line[:110]}...")
        else:
            with _lock:
                print(f"[{port}] {line}")
    ser.close()
    with _lock:
        print(f"[{port}] === CSI_DATA lines: {csi} in {dur:.0f}s "
              f"(~{csi/dur:.0f}/s) ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ports', default='COM17,COM18')
    ap.add_argument('--baud', type=int, default=921600)
    ap.add_argument('--secs', type=float, default=12)
    args = ap.parse_args()
    ports = [p.strip() for p in args.ports.split(',') if p.strip()]
    ts = [threading.Thread(target=reader, args=(p, args.baud, args.secs), daemon=True)
          for p in ports]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("--- done ---")


if __name__ == '__main__':
    main()
