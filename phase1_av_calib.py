#!/usr/bin/env python3
"""
Phase 1 with per-environment EMPTY-BASELINE calibration.

Instead of absolute CSI values, every feature is expressed as a DEVIATION FROM
that environment's own empty baseline (simulating a short install-time "record
empty" calibration). "How far is the channel from empty *here*" should transfer
across geometries far better than the absolute fingerprint.

Compares the same evaluations as phase1_av.py:
  - pooled leave-one-take-out
  - CROSS-ENVIRONMENT leave-one-room-out  (the real test)

Usage:  python phase1_av_calib.py
"""

import argparse
import json
import os
from collections import Counter

import numpy as np

from phase1_av import (load_csi, load_labels, discover_takes,
                       binary_loso, multiclass_loso)
import csi_dataset as cd

WIN_S, HOP_S, MIN_PKTS, PURITY = 2.0, 1.0, 100, 0.7


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--av', default='data/av')
    ap.add_argument('--win', type=float, default=WIN_S)
    ap.add_argument('--hop', type=float, default=HOP_S)
    args = ap.parse_args()

    takes = discover_takes(args.av)
    loaded, total, count = [], None, 0
    for d in takes:
        pc, amp = load_csi(os.path.join(d, 'csi.csv'))
        lt, lab = load_labels(os.path.join(d, 'labels.csv'))
        if amp.size == 0 or lt.size == 0:
            continue
        room = 'unknown'
        mp = os.path.join(d, 'meta.json')
        if os.path.exists(mp):
            try:
                room = json.load(open(mp)).get('room', 'unknown')
            except Exception:
                pass
        loaded.append((os.path.basename(d), room, pc, amp, lt, lab))
        total = amp.sum(0) if total is None else total + amp.sum(0)
        count += len(amp)

    mean_amp = total / count
    pos = mean_amp[mean_amp > 0]
    mask = mean_amp > 0.1 * np.median(pos)
    print(f"Active subcarriers: {int(mask.sum())}/{len(mask)}; takes={len(loaded)}")

    # ---- Pass 1: per-window mean/std/motion vectors + metadata ----
    recs = []
    for take_id, room, pc, amp, lt, lab in loaded:
        A = amp[:, mask]
        t = pc[0]
        while t + args.win <= pc[-1]:
            a, b = t, t + args.win
            t += args.hop
            csel = (pc >= a) & (pc < b)
            if csel.sum() < MIN_PKTS:
                continue
            lsel = (lt >= a) & (lt < b)
            if lsel.sum() == 0:
                continue
            top, ntop = Counter(lab[lsel]).most_common(1)[0]
            if ntop / lsel.sum() < PURITY:
                continue
            W = A[csel]
            mean_vec, std_vec = W.mean(0), W.std(0)
            power = np.abs(np.fft.rfft(W - mean_vec, axis=0)) ** 2
            freqs = np.fft.rfftfreq(W.shape[0], d=1.0 / cd.FS)
            band = (freqs >= 0.5) & (freqs <= 5.0)
            motion_vec = power[band].sum(0) / (power.sum(0) + 1e-9)
            recs.append(dict(take=take_id, room=room, label=top,
                             mean=mean_vec, std=std_vec, motion=motion_vec))
    print(f"Windows: {len(recs)}; per-class={dict(Counter(r['label'] for r in recs))}")

    # ---- per-environment EMPTY baseline (install-time calibration) ----
    baselines = {}
    for room in set(r['room'] for r in recs):
        emp = [r['mean'] for r in recs if r['room'] == room and r['label'] == 'empty']
        src = emp if emp else [r['mean'] for r in recs if r['room'] == room]
        baselines[room] = np.mean(src, axis=0)

    # ---- Pass 2: deviation-from-baseline features ----
    X, y, groups, rooms = [], [], [], []
    for r in recs:
        base = baselines[r['room']]
        rel = (r['mean'] - base) / (base + 1e-6)        # relative static deviation
        cos = float(np.dot(r['mean'], base)
                    / (np.linalg.norm(r['mean']) * np.linalg.norm(base) + 1e-9))
        summ = np.array([np.abs(rel).mean(), np.abs(rel).max(), np.linalg.norm(rel),
                         1 - cos, r['std'].mean(), r['std'].max(),
                         r['motion'].mean(), r['motion'].max()])
        X.append(np.concatenate([rel, r['std'], r['motion'], summ]))
        y.append(r['label'])
        groups.append(r['take'])
        rooms.append(r['room'])
    X, y = np.asarray(X), np.asarray(y)
    groups, rooms = np.asarray(groups), np.asarray(rooms)
    labels = [c for c in cd.CLASSES if c in set(y)]

    print("\n### CALIBRATED features (deviation from each env's empty baseline) ###")
    if len(set(groups)) >= 2:
        print("\n-- Pooled (leave-one-take-out) --")
        binary_loso(X, y, groups, ['still', 'moving'], 'occupied', 'empty')
    if len(set(rooms)) >= 2:
        print(f"\n-- CROSS-ENVIRONMENT (leave-one-room-out) over {sorted(set(rooms))} --")
        binary_loso(X, y, rooms, ['still', 'moving'], 'occupied', 'empty')
        multiclass_loso(X, y, rooms, labels, 'cross-room 3-class (calibrated)')


if __name__ == '__main__':
    main()
