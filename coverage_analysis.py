#!/usr/bin/env python3
"""
Coverage / dead-zone analysis: WHY one ESP isn't enough.

For every window we measure the CSI 'motion energy' (mean per-subcarrier
temporal variance) and, from the synchronized webcam, where the person was.
A single TX->RX link only perturbs strongly near its line-of-sight / Fresnel
zones, so a person who is genuinely MOVING (per the camera) but far from the
link produces little CSI change -> a coverage dead zone.

Outputs (data/coverage_analysis.png) + printed numbers:
  - CSI motion energy: still vs moving windows (sets a detectability threshold)
  - moving-detection recall vs distance bin (near/mid/far)
  - per-room 2D map of CSI motion energy over the person's screen position
    (hot = sensed, cold = dead zone)

Usage:  python coverage_analysis.py
"""

import csv
import glob
import json
import os
from collections import Counter, defaultdict

import numpy as np
import matplotlib.pyplot as plt

from phase1_av import load_csi, discover_takes
import csi_dataset as cd

WIN_S, HOP_S, MIN_PKTS, PURITY = 2.0, 1.0, 100, 0.7


def load_labels_full(path):
    """Return per-frame arrays: t, label, cx, cy, distance."""
    t, lab, cx, cy, dist = [], [], [], [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                t.append(float(row['pc_time']))
            except (ValueError, KeyError):
                continue
            lab.append(row.get('auto_label', ''))
            dist.append(row.get('distance', 'na'))
            bb = row.get('bbox', '').split()
            if len(bb) == 4:
                x1, y1, x2, y2 = map(float, bb)
                cx.append((x1 + x2) / 2)
                cy.append((y1 + y2) / 2)
            else:
                cx.append(np.nan)
                cy.append(np.nan)
    return (np.array(t), np.array(lab), np.array(cx),
            np.array(cy), np.array(dist))


def main():
    takes = discover_takes('data/av')
    rows = []   # one dict per window
    fw = fh = 1.0
    for d in takes:
        pc, amp = load_csi(os.path.join(d, 'csi.csv'))
        lt, lab, lcx, lcy, ldist = load_labels_full(os.path.join(d, 'labels.csv'))
        if amp.size == 0 or lt.size == 0:
            continue
        room = 'unknown'
        mp = os.path.join(d, 'meta.json')
        if os.path.exists(mp):
            try:
                room = json.load(open(mp)).get('room', 'unknown')
            except Exception:
                pass
        fw = max(fw, np.nanmax(lcx) * 2 if np.isfinite(np.nanmax(lcx)) else fw)
        fh = max(fh, np.nanmax(lcy) * 2 if np.isfinite(np.nanmax(lcy)) else fh)

        # global active mask per take is overkill; use this take's mask
        mean_amp = amp.mean(0)
        mask = mean_amp > 0.1 * np.median(mean_amp[mean_amp > 0])
        A = amp[:, mask]
        t = pc[0]
        while t + WIN_S <= pc[-1]:
            a, b = t, t + WIN_S
            t += HOP_S
            csel = (pc >= a) & (pc < b)
            if csel.sum() < MIN_PKTS:
                continue
            lsel = (lt >= a) & (lt < b)
            if lsel.sum() == 0:
                continue
            top, ntop = Counter(lab[lsel]).most_common(1)[0]
            if ntop / lsel.sum() < PURITY:
                continue
            energy = float(A[csel].var(axis=0).mean())   # CSI motion energy
            dd = Counter(ldist[lsel]).most_common(1)[0][0]
            rows.append(dict(room=room, label=top, energy=energy,
                             cx=np.nanmean(lcx[lsel]), cy=np.nanmean(lcy[lsel]),
                             dist=dd))

    if not rows:
        raise SystemExit("No windows.")
    print(f"Windows: {len(rows)}  per-class={dict(Counter(r['label'] for r in rows))}")

    still_e = np.array([r['energy'] for r in rows if r['label'] == 'still'])
    move_e = np.array([r['energy'] for r in rows if r['label'] == 'moving'])
    empty_e = np.array([r['energy'] for r in rows if r['label'] == 'empty'])
    # detectability threshold: above the 90th percentile of STILL energy
    thr = float(np.percentile(still_e, 90)) if still_e.size else 0.0

    print(f"\nCSI motion energy (mean subcarrier variance):")
    print(f"  empty : median {np.median(empty_e):.3f}" if empty_e.size else "")
    print(f"  still : median {np.median(still_e):.3f}")
    print(f"  moving: median {np.median(move_e):.3f}")
    print(f"Detectability threshold (90th pct of still): {thr:.3f}")

    overall_recall = float(np.mean(move_e > thr)) if move_e.size else 0.0
    print(f"\nMoving windows detectable by CSI energy: {overall_recall*100:.0f}%  "
          f"=> {100-overall_recall*100:.0f}% are effectively in DEAD ZONES")

    print("\nMoving-detection recall by distance from link/camera:")
    by_dist = defaultdict(list)
    for r in rows:
        if r['label'] == 'moving':
            by_dist[r['dist']].append(r['energy'] > thr)
    for dname in ['near', 'mid', 'far']:
        v = by_dist.get(dname, [])
        if v:
            print(f"  {dname:4}: recall {np.mean(v)*100:3.0f}%  (n={len(v)})")

    # ---- plots ----
    rooms = sorted(set(r['room'] for r in rows))
    fig, axes = plt.subplots(1, 2 + len(rooms), figsize=(6 + 5 * len(rooms), 4.5))

    ax = axes[0]
    bins = np.linspace(0, np.percentile(move_e, 98) if move_e.size else 1, 40)
    if empty_e.size:
        ax.hist(empty_e, bins=bins, alpha=0.5, label='empty', color='gray')
    ax.hist(still_e, bins=bins, alpha=0.5, label='still', color='orange')
    ax.hist(move_e, bins=bins, alpha=0.5, label='moving', color='red')
    ax.axvline(thr, color='k', ls='--', label='detect thr')
    ax.set_title('CSI motion energy by class'); ax.set_xlabel('energy'); ax.legend()

    ax = axes[1]
    dn = ['near', 'mid', 'far']
    recs = [np.mean(by_dist[d]) * 100 if by_dist.get(d) else 0 for d in dn]
    ax.bar(dn, recs, color='steelblue')
    ax.set_ylim(0, 100); ax.set_title('Moving recall vs distance')
    ax.set_ylabel('% moving windows detected')

    # per-room 2D coverage maps (person position vs CSI motion energy when moving)
    for k, room in enumerate(rooms):
        ax = axes[2 + k]
        mr = [r for r in rows if r['room'] == room and r['label'] == 'moving'
              and np.isfinite(r['cx']) and np.isfinite(r['cy'])]
        if mr:
            cxs = np.array([r['cx'] for r in mr]) / fw
            cys = np.array([r['cy'] for r in mr]) / fh
            es = np.array([r['energy'] for r in mr])
            nb = 6
            xi = np.clip((cxs * nb).astype(int), 0, nb - 1)
            yi = np.clip((cys * nb).astype(int), 0, nb - 1)
            grid = np.full((nb, nb), np.nan)
            for gx in range(nb):
                for gy in range(nb):
                    m = (xi == gx) & (yi == gy)
                    if m.sum() >= 2:
                        grid[gy, gx] = es[m].mean()
            im = ax.imshow(grid, origin='upper', cmap='jet', aspect='auto')
            fig.colorbar(im, ax=ax, label='CSI motion energy')
        ax.set_title(f'Coverage map: {room}\n(person screen position when moving)')
        ax.set_xlabel('left  <-- cx -->  right'); ax.set_ylabel('top <-- cy --> bottom')

    plt.tight_layout()
    out = 'data/coverage_analysis.png'
    plt.savefig(out, dpi=120)
    print(f"\nSaved {out}")
    plt.show()


if __name__ == '__main__':
    main()
