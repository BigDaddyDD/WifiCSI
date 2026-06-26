#!/usr/bin/env python3
"""
Guided CSI data collection.

Walks you through recording labeled sessions with CONSISTENT class names and
rich metadata, so the dataset stays clean. Reuses the recorder in log_csi.py.

Run it and follow the prompts:
    python collect.py

Canonical classes (Phase 1 - presence):
    empty   no one in the room
    still   occupied, person motionless (sitting or standing)
    moving  occupied, person walking / moving

Posture / location / person go into metadata, NOT the label, so we can slice
the data later without inventing new label strings.
"""

import datetime
import os
import time

import serial

from log_csi import record_to_csv, write_session_metadata

OUT = 'data/raw'
BAUD = 921600

LABELS = {
    '1': ('empty',  'no one in the room'),
    '2': ('still',  'occupied, person motionless (sit/stand)'),
    '3': ('moving', 'occupied, person walking/moving'),
}


def ask(prompt, default=''):
    suffix = f" [{default}]" if default else ""
    s = input(f"{prompt}{suffix}: ").strip()
    return s or default


def countdown(n=3):
    for i in range(n, 0, -1):
        print(f"  starting in {i}...", end='\r')
        time.sleep(1)
    print("  RECORDING - hold the scenario!        ")


def main():
    print("=== CSI guided collection ===")
    port = ask("Serial port", "COM17")
    room = ask("Room id", "confA")
    person = ask("Person id", "p1")
    distance = ask("TX-RX distance (inches)", "88")

    os.makedirs(OUT, exist_ok=True)
    tally = {}

    while True:
        print("\nClass:  [1] empty   [2] still   [3] moving   [q] quit")
        choice = input("choose: ").strip().lower()
        if choice == 'q':
            break
        if choice not in LABELS:
            print("  (pick 1, 2, 3, or q)")
            continue

        label, desc = LABELS[choice]
        posture = location = note = ''
        if label != 'empty':
            posture = ask("Posture (sit/stand/walk)",
                          'walk' if label == 'moving' else 'sit')
            location = ask("Location (dist-from-RX in inches, or spot name)", "mid")
        note = ask("Note (optional)", "")
        try:
            dur = float(ask("Duration s", "60"))
        except ValueError:
            dur = 60.0

        input(f"\nSet up '{label}' ({desc}). Press Enter when ready...")
        countdown(3)

        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        base = f"{label}_{ts}"
        csv_path = os.path.join(OUT, base + '.csv')
        meta_path = os.path.join(OUT, base + '.json')

        try:
            stats = record_to_csv(port, BAUD, dur, csv_path)
        except serial.SerialException as ex:
            print(f"  serial error: {ex}  (close the Serial Monitor / check port)")
            continue

        meta = {
            'label': label, 'room': room, 'person': person, 'posture': posture,
            'location': location, 'distance_in': distance, 'note': note,
            'port': port, 'baud': BAUD, 'started': ts, **stats,
        }
        write_session_metadata(meta_path, meta)

        tally[label] = tally.get(label, 0) + 1
        print(f"  saved {base}  ({stats['packets']} pkts, "
              f"~{stats['mean_rate_hz']} Hz, drops {stats['idx_drops']})")
        if stats['packets'] == 0:
            print("  WARNING: 0 packets - is the TX connected and CSI flowing?")

    total = sum(tally.values())
    print(f"\nDone. {total} sessions this run: "
          + ", ".join(f"{k}={v}" for k, v in tally.items()))
    print(f"Saved to {OUT}/")


if __name__ == '__main__':
    main()
