#!/usr/bin/env python3
"""
Phase A analysis (home_L scripted sessions).

Tasks: 5-class activity {empty,stand,sit,walk,run} and presence {empty vs
occupied}.

Rigor / generalization design
------------------------------
Each SESSION is one unique configuration (placement x node-orientation). The
honest question the external team will ask is "does the model work on a
configuration it never saw?", so the primary metric is LEAVE-ONE-SESSION-OUT
cross-validation (train on N-1 configs, test on the held-out one), NOT a random
split (which leaks the geometry). We also print a leaky random-CV number purely
as an optimistic ceiling for contrast, and an RSSI-only baseline to show what
CSI adds.

Calibration (the deployment recipe)
-----------------------------------
Raw CSI amplitude overfits the exact geometry, so we also evaluate CALIBRATED
features: per-session deviation from that session's own EMPTY baseline, then a
per-session z-score. This mirrors real deployment: at install you record a short
empty-room baseline, then classify against it. To keep this honest we split each
session's empty windows -- half are the calibration reference (used only to
build the baseline / z-stats, never scored) and the other half are scored like
any other window, so empty-recall is not flattered by testing on the exact
windows used to calibrate.

Usage:  python phase_a_analysis.py
Requires: numpy, scikit-learn, matplotlib.
"""
import csv
import glob
import json
import os
from collections import Counter

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             classification_report, confusion_matrix)

import csi_dataset as cd

WIN, HOP = 200, 100
RESAMPLE_FS = 100          # uniform grid (Hz) -> WIN/HOP and cd.FS stay valid
CLASSES = ['empty', 'stand', 'sit', 'walk', 'run']
ROOT = 'data/study/home_L'


def _resample(amp, rssi, tus, fs):
    """Resample onto a uniform `fs` grid using the RX micro-second timestamps.

    The received CSI samples are ~10 ms apart (100 Hz) in every environment;
    what differs is how many packets are DROPPED (gaps). Interpolating onto a
    uniform grid from `local_us` fills those gaps so the sampling is uniform and
    the FFT frequency axis (assumed 100 Hz) is correct and comparable across
    rooms. Near-identity for a clean stream; only reshapes lossy ones. Falls
    back to the raw series if timestamps are unusable.
    """
    if len(tus) < 8 or np.any(tus < 0):
        return amp, rssi
    d = np.diff(tus)
    if np.any(d < 0):                       # unwrap 32-bit micros() rollover
        tus = tus.copy()
        tus[1:] += np.cumsum(np.where(d < 0, 2.0 ** 32, 0.0))
    t = (tus - tus[0]) / 1e6
    inc = np.concatenate([[True], np.diff(t) > 0])
    t, amp, rssi = t[inc], amp[inc], rssi[inc]
    if len(t) < 8 or t[-1] <= 0:
        return amp, rssi
    grid = np.arange(0.0, t[-1], 1.0 / fs)
    if len(grid) < 4:
        return amp, rssi
    ampU = np.empty((len(grid), amp.shape[1]))
    for s in range(amp.shape[1]):
        ampU[:, s] = np.interp(grid, t, amp[:, s])
    return ampU, np.interp(grid, t, rssi)


def load_seg(path, resample_fs=RESAMPLE_FS):
    amps, rssi, tus = [], [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                vals = [int(x) for x in row['csi'].split()]
            except (ValueError, KeyError):
                continue
            if not vals:
                continue
            a = np.asarray(vals, float)
            r, im = a[0::2], a[1::2]
            m = min(len(r), len(im))
            amps.append(np.sqrt(r[:m] ** 2 + im[:m] ** 2))
            try:
                rssi.append(int(row['rssi']))
            except (ValueError, KeyError):
                rssi.append(-100)
            try:
                tus.append(int(row['local_us']))
            except (ValueError, KeyError):
                tus.append(-1)
    if not amps:
        return np.empty((0, 0)), np.array([])
    L = max(set(len(x) for x in amps), key=[len(x) for x in amps].count)
    keep = [i for i, x in enumerate(amps) if len(x) == L]
    amp = np.array([amps[i] for i in keep])
    rs = np.array([rssi[i] for i in keep])
    tu = np.array([tus[i] for i in keep], float)
    if resample_fs:
        amp, rs = _resample(amp, rs, tu, resample_fs)
    return amp, rs


def motion_frac(W):
    Wd = W - W.mean(0)
    power = np.abs(np.fft.rfft(Wd, axis=0)) ** 2
    fr = np.fft.rfftfreq(W.shape[0], d=1.0 / cd.FS)
    band = (fr >= 0.5) & (fr <= 5.0)
    return power[band].sum(0) / (power.sum(0) + 1e-9)


def discover():
    """Return per-segment records; group == session id (one config each)."""
    segs, configs = [], {}
    for sj in sorted(glob.glob(os.path.join(ROOT, '*', 'session.json'))):
        d = os.path.dirname(sj)
        sid = os.path.basename(d)
        m = json.load(open(sj))
        configs[sid] = f"{m.get('placement', '?')}/{m.get('node_orientation', '?')}"
        for cf in sorted(glob.glob(os.path.join(d, 'seg*__*.csv'))):
            label = os.path.basename(cf).split('_')[1]
            if label in CLASSES:
                segs.append({'csv': cf, 'label': label, 'session': sid})
    return segs, configs


def build_calibrated(MV, SV, MO, groups, y, calib_mask):
    """Per-session empty-baseline deviation + per-session z-score.

    Baseline / z-stats come ONLY from each session's calibration-empty windows
    (calib_mask), so scored windows never define their own normalization.
    """
    Xcal = np.zeros((len(y), 2 * MV.shape[1] + MO.shape[1] + 4))
    for sid in set(groups):
        m = groups == sid
        cal = m & calib_mask                       # this session's empty reference
        base = MV[cal].mean(0) if cal.sum() else MV[m].mean(0)
        for i in np.where(m)[0]:
            rel = (MV[i] - base) / (base + 1e-6)
            summ = [np.abs(rel).mean(), np.abs(rel).max(),
                    np.linalg.norm(rel), SV[i].mean()]
            Xcal[i] = np.concatenate([rel, SV[i], MO[i], summ])
        ref = Xcal[cal] if cal.sum() >= 3 else Xcal[m]
        mu, sd = ref.mean(0), ref.std(0) + 1e-6
        Xcal[m] = (Xcal[m] - mu) / sd
    return Xcal


def loso(X, y, groups, calib_mask, labels, name, configs):
    """Leave-one-session-out. Calibration-empty windows are excluded from both
    train and test everywhere so every method sees the same partition."""
    score = ~calib_mask
    fold_acc, yt, yp = [], [], []
    for sid in sorted(set(groups)):
        tr = score & (groups != sid)
        te = score & (groups == sid)
        clf = RandomForestClassifier(n_estimators=400, random_state=0,
                                     n_jobs=-1, class_weight='balanced')
        clf.fit(X[tr], y[tr])
        pred = clf.predict(X[te])
        acc = accuracy_score(y[te], pred)
        fold_acc.append((configs.get(sid, sid), acc))
        yt.extend(y[te]); yp.extend(pred)
    yt, yp = np.asarray(yt), np.asarray(yp)
    fa = np.array([a for _, a in fold_acc])
    print(f"\n[{name}] leave-one-SESSION-out ({len(fa)} folds)")
    for cfg, a in fold_acc:
        print(f"    held-out {cfg:<16} acc {a:.3f}")
    print(f"  mean {fa.mean():.3f} +/- {fa.std():.3f}   "
          f"balanced {balanced_accuracy_score(yt, yp):.3f}")
    print(classification_report(yt, yp, labels=labels, zero_division=0))
    cm = confusion_matrix(yt, yp, labels=labels)
    print("Confusion (rows=true, cols=pred):")
    print("        " + "  ".join(f"{l:>8}" for l in labels))
    for i, l in enumerate(labels):
        print(f"{l:>7} " + "  ".join(f"{cm[i, j]:8d}" for j in range(len(labels))))
    return yt, yp, cm


def main():
    segs, configs = discover()
    sids = sorted(set(s['session'] for s in segs))
    print("Sessions (each = one placement/orientation config):")
    for sid in sids:
        print(f"  {sid}  ->  {configs[sid]}")
    print(f"Segments: {len(segs)}  per-label: {dict(Counter(s['label'] for s in segs))}")

    # global active-subcarrier mask
    total, count = None, 0
    for s in segs:
        amp, _ = load_seg(s['csv'])
        if amp.size:
            total = amp.sum(0) if total is None else total + amp.sum(0)
            count += len(amp)
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])
    print(f"Active subcarriers: {int(mask.sum())}/{len(mask)}")

    # build windows
    Xc, Xr, y, groups = [], [], [], []
    MV, SV, MO = [], [], []
    for s in segs:
        amp, rssi = load_seg(s['csv'])
        if amp.size == 0:
            continue
        A = amp[:, mask]
        for a, b in cd.window_indices(len(A), WIN, HOP):
            W = A[a:b]
            Xc.append(cd.window_features(W))
            MV.append(W.mean(0)); SV.append(W.std(0)); MO.append(motion_frac(W))
            rw = rssi[a:b]
            Xr.append([rw.mean(), rw.std(), rw.min(), rw.max()])
            y.append(s['label'])
            groups.append(s['session'])
    Xc, Xr = np.asarray(Xc), np.asarray(Xr)
    MV, SV, MO = np.asarray(MV), np.asarray(SV), np.asarray(MO)
    y, groups = np.asarray(y), np.asarray(groups)
    labels = [c for c in CLASSES if c in set(y)]
    print(f"Windows: {len(y)}  per-class: {dict(Counter(y))}")
    print(f"Majority-class baseline: {Counter(y).most_common(1)[0][1] / len(y):.3f}")

    # Designate half of each session's EMPTY windows as the calibration
    # reference (used only to build the baseline / z-stats, never scored).
    # We sample them SPREAD across the session (every other window) rather than
    # a contiguous front block: the empty multipath fingerprint is essentially
    # constant within a session (start-vs-end shape cosine >=0.999), but a short
    # contiguous block underestimates the natural window-to-window variance of
    # "empty" (consecutive windows are autocorrelated), which then flags later
    # empties as occupied. Spread sampling gives a representative variance
    # estimate -- the deployment equivalent is a longer / periodically-refreshed
    # empty baseline. (Sensitivity: a contiguous start-only reference drops
    # presence balanced acc ~0.63 / empty-recall ~0.32; spread -> ~0.89 / ~0.83.)
    calib_mask = np.zeros(len(y), bool)
    for sid in sids:
        emp = np.where((groups == sid) & (y == 'empty'))[0]
        calib_mask[emp[::2]] = True
    print(f"Calibration-empty windows held out of scoring: {int(calib_mask.sum())}")

    # optimistic ceiling: leaky random CV (scored windows only)
    sc = ~calib_mask
    skf = StratifiedKFold(5, shuffle=True, random_state=0)
    leaky = cross_val_score(RandomForestClassifier(n_estimators=400, random_state=0,
                            n_jobs=-1, class_weight='balanced'),
                            Xc[sc], y[sc], cv=skf).mean()
    print(f"\n[5-class CSI] LEAKY random-CV (optimistic ceiling): {leaky:.3f}")

    yb = np.where(y == 'empty', 'empty', 'occupied')

    # raw CSI (overfits geometry) + RSSI baseline
    loso(Xc, y, groups, calib_mask, labels, "5-class CSI raw", configs)
    loso(Xr, y, groups, calib_mask, labels, "5-class RSSI-only baseline", configs)
    loso(Xc, yb, groups, calib_mask, ['empty', 'occupied'], "PRESENCE CSI raw", configs)

    # calibrated (per-session empty-baseline deviation + z-score)
    Xcal = build_calibrated(MV, SV, MO, groups, y, calib_mask)
    _, _, cmc = loso(Xcal, y, groups, calib_mask, labels, "5-class CSI CALIBRATED", configs)
    loso(Xcal, yb, groups, calib_mask, ['empty', 'occupied'], "PRESENCE CSI CALIBRATED", configs)

    # confusion figure (calibrated 5-class)
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.imshow(cmc, cmap='Blues')
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, rotation=45)
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, cmc[i, j], ha='center', va='center',
                    color='white' if cmc[i, j] > cmc.max() / 2 else 'black', fontsize=8)
    ax.set_title('Calibrated 5-class, leave-one-session-out')
    ax.set_xlabel('pred'); ax.set_ylabel('true')
    plt.tight_layout(); plt.savefig('data/phase_a_confusion.png', dpi=120)
    print("\nSaved data/phase_a_confusion.png")


if __name__ == '__main__':
    main()
