#!/usr/bin/env python3
"""
Analyze a CSI CSV produced by log_csi.py.

Prints Phase-0 health stats (rate, jitter, drops, vector-length consistency)
and saves a 4-panel figure:
  1) inter-packet interval histogram   (rig timing health)
  2) CSI amplitude spectrogram         (the "waterfall")
  3) per-subcarrier amplitude mean+/-std
  4) per-subcarrier variance over time (motion sensitivity)

Usage
-----
  python analyze_csi.py data/raw/empty_20260624_120000.csv
  python analyze_csi.py data/raw/empty_20260624_120000.csv --fs 50
"""

import argparse
import csv
import sys

import numpy as np
import matplotlib.pyplot as plt


def load(path):
    pc, idx, rssi, local_us, n = [], [], [], [], []
    csi = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                vals = [int(x) for x in row['csi'].split()]
            except (ValueError, KeyError):
                continue
            if not vals:
                continue
            csi.append(vals)
            pc.append(float(row['pc_time']))
            idx.append(int(row['idx']))
            rssi.append(int(row['rssi']))
            local_us.append(int(row['local_us']))
            n.append(int(row['n']))
    return (np.array(pc), np.array(idx), np.array(rssi),
            np.array(local_us), np.array(n), csi)


def to_amplitude(csi_list):
    """Return an [packets x subcarriers] amplitude matrix using the most
    common vector length (ignores malformed packets)."""
    if not csi_list:
        return np.empty((0, 0))
    lens = [len(v) for v in csi_list]
    L = max(set(lens), key=lens.count)
    rows = []
    for v in csi_list:
        if len(v) != L:
            continue
        a = np.asarray(v, dtype=float)
        real, imag = a[0::2], a[1::2]
        m = min(len(real), len(imag))
        rows.append(np.sqrt(real[:m] ** 2 + imag[:m] ** 2))
    return np.asarray(rows)


def main():
    ap = argparse.ArgumentParser(description="Analyze a logged CSI CSV.")
    ap.add_argument('csv')
    ap.add_argument('--fs', type=float, default=100.0,
                    help='expected sample rate in Hz (default 100)')
    args = ap.parse_args()

    pc, idx, rssi, local_us, n, csi = load(args.csv)
    if len(pc) == 0:
        print("No data rows loaded.")
        sys.exit(1)

    dur = pc[-1] - pc[0]
    rate = len(pc) / dur if dur > 0 else 0.0

    dt_pc = np.diff(pc) * 1000.0                    # ms between serial reads (PC clock)
    # True over-the-air cadence from the ESP's own microsecond clock; robust to
    # PC-side USB/serial buffering. Drop wrap-arounds and large gaps.
    d_esp = np.diff(local_us.astype(np.int64))
    d_esp = d_esp[(d_esp > 0) & (d_esp < 10_000_000)] / 1000.0
    dt_esp = d_esp if d_esp.size else dt_pc

    didx = np.diff(idx)
    drops = int(np.sum(didx[didx > 1] - 1))
    expected = (idx[-1] - idx[0] + 1) if len(idx) > 1 else len(idx)
    drop_pct = 100.0 * drops / expected if expected > 0 else 0.0
    L = max(set(n.tolist()), key=n.tolist().count)
    len_ok = 100.0 * np.mean(n == L)

    target_ms = 1000.0 / args.fs
    rate_ok = abs(rate - args.fs) / args.fs < 0.10
    jitter_ok = np.std(dt_esp) < 0.5 * target_ms   # judged on the ESP clock
    drops_ok = drop_pct < 5.0
    verdict = rate_ok and jitter_ok and drops_ok and len_ok > 99.0

    print("=" * 56)
    print(f"File:         {args.csv}")
    print(f"Packets:      {len(pc)}")
    print(f"Duration:     {dur:.2f} s")
    print(f"Mean rate:    {rate:.2f} Hz   (target {args.fs:.0f})       "
          f"{'OK' if rate_ok else 'CHECK'}")
    print(f"Cadence ms (ESP):    median {np.median(dt_esp):.2f}  std {np.std(dt_esp):.2f}   "
          f"{'OK' if jitter_ok else 'CHECK'}")
    print(f"Read jitter ms (PC): std {np.std(dt_pc):.2f}  (serial buffering; informational)")
    print(f"Drops (idx):  {drops}  ({drop_pct:.2f}%)                  "
          f"{'OK' if drops_ok else 'CHECK'}")
    print(f"Vector len:   mode {L}, consistent {len_ok:.1f}%          "
          f"{'OK' if len_ok > 99 else 'CHECK'}")
    print(f"RSSI dBm:     mean {np.mean(rssi):.1f}  std {np.std(rssi):.1f}")
    print(f"PHASE 0 VERDICT: {'PASS' if verdict else 'NEEDS WORK'}")
    print("=" * 56)

    amp = to_amplitude(csi)

    fig, axes = plt.subplots(2, 2, figsize=(13, 8))

    ax = axes[0, 0]
    clip = dt_esp[dt_esp < np.percentile(dt_esp, 99.5)] if len(dt_esp) else dt_esp
    ax.hist(clip, bins=80, color='steelblue')
    ax.axvline(target_ms, color='red', ls='--', label=f'target {target_ms:.1f} ms')
    ax.set_title('Inter-packet interval (ESP clock)'); ax.set_xlabel('ms')
    ax.set_ylabel('count'); ax.legend()

    ax = axes[0, 1]
    if amp.size:
        im = ax.imshow(amp.T, aspect='auto', origin='lower', cmap='jet',
                       vmin=np.percentile(amp, 2), vmax=np.percentile(amp, 98))
        fig.colorbar(im, ax=ax, label='amplitude')
    ax.set_title('CSI amplitude spectrogram')
    ax.set_xlabel('packet'); ax.set_ylabel('subcarrier')

    ax = axes[1, 0]
    if amp.size:
        mean_a, std_a = amp.mean(0), amp.std(0)
        x = np.arange(len(mean_a))
        ax.plot(x, mean_a, color='navy')
        ax.fill_between(x, mean_a - std_a, mean_a + std_a, alpha=0.3)
    ax.set_title('Per-subcarrier amplitude (mean +/- std)')
    ax.set_xlabel('subcarrier'); ax.set_ylabel('amplitude')

    ax = axes[1, 1]
    if amp.size:
        ax.plot(amp.var(0), color='darkred')
    ax.set_title('Per-subcarrier variance over time (motion sensitivity)')
    ax.set_xlabel('subcarrier'); ax.set_ylabel('variance')

    plt.tight_layout()
    out = args.csv.rsplit('.', 1)[0] + '_analysis.png'
    plt.savefig(out, dpi=110)
    print(f"Saved plot: {out}")
    plt.show()


if __name__ == '__main__':
    main()
