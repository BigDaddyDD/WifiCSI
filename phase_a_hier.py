#!/usr/bin/env python3
"""
Phase A -- hierarchical activity model + richer motion features.

Motivation (from phase_a_analysis.py results): the flat 5-class model reaches
~0.50 accuracy leave-one-session-out, and its errors are STRUCTURED -- sit<->stand
(both static) and run<->walk (both moving). Two upgrades attack that, using only
the data we already have:

  1. Richer features. The flat model has one aggregate 0.5-5 Hz motion number, so
     walking and running look alike. Here we add:
       - sub-band motion fractions (0.5-2, 2-5, 5-10 Hz), mean+max over subcarriers
       - spectral centroid of the fluctuation (a "tempo" axis: run > walk)
       - low-frequency fraction (0.15-0.5 Hz) over a 10 s context (sit vs stand /
         micro-motion, which a 2 s window can't see)

  2. Hierarchical classifier. Stage 1 = {empty, static, moving} (easy, robust).
     Stage 2a = static -> {sit, stand}; Stage 2b = moving -> {walk, run}. Each
     specialist trains on a cleaner 2-class problem.

Everything keeps the same rigor as phase_a_analysis: per-session empty-baseline
calibration, calibration-empty windows held out of scoring, LEAVE-ONE-SESSION-OUT.
We print, for a fair ablation:
  - flat 5-class on the SAME richer features   (isolates the feature effect)
  - the hierarchical model                      (isolates the hierarchy effect)
  - stage-1 {empty/static/moving} accuracy      (the "see the errors" breakdown)

Usage:  python phase_a_hier.py
"""
from collections import Counter

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             classification_report, confusion_matrix)

import phase_a_analysis as pa
import csi_dataset as cd

WIN, HOP = 200, 100          # 2 s windows, 1 s hop (base features)
LONG = 1000                  # 10 s context for the low-frequency block
CLASSES = ['empty', 'stand', 'sit', 'walk', 'run']
SUPER = {'empty': 'empty', 'stand': 'static', 'sit': 'static',
         'walk': 'moving', 'run': 'moving'}
SUPER_LABELS = ['empty', 'static', 'moving']
BANDS = [(0.5, 2.0), (2.0, 5.0), (5.0, 10.0)]
LOWBANDS = [(0.15, 0.5)]


def _rf():
    return RandomForestClassifier(n_estimators=400, random_state=0,
                                  n_jobs=-1, class_weight='balanced')


def band_fracs(W, bands):
    """Per-band share of fluctuation power, aggregated over subcarriers."""
    Wd = W - W.mean(0)
    P = np.abs(np.fft.rfft(Wd, axis=0)) ** 2
    fr = np.fft.rfftfreq(W.shape[0], d=1.0 / cd.FS)
    tot = P.sum(0) + 1e-9
    out = []
    for lo, hi in bands:
        m = (fr >= lo) & (fr < hi)
        frac = P[m].sum(0) / tot          # per subcarrier
        out += [float(frac.mean()), float(frac.max())]
    return out


def spectral_centroid(W):
    """Power-weighted mean fluctuation frequency in 0.5-10 Hz (tempo proxy)."""
    Wd = W - W.mean(0)
    P = np.abs(np.fft.rfft(Wd, axis=0)) ** 2
    fr = np.fft.rfftfreq(W.shape[0], d=1.0 / cd.FS)
    band = (fr >= 0.5) & (fr <= 10.0)
    p = P[band].mean(1)
    f = fr[band]
    return float((f * p).sum() / (p.sum() + 1e-9))


def build_windows(segs, mask):
    MV, SV, EX, y, groups = [], [], [], [], []
    for s in segs:
        amp, _ = pa.load_seg(s['csv'])
        if amp.size == 0:
            continue
        A = amp[:, mask]
        n = len(A)
        for a, b in cd.window_indices(n, WIN, HOP):
            W = A[a:b]
            center = (a + b) // 2
            c0 = max(0, center - LONG // 2)
            c1 = min(n, c0 + LONG)
            c0 = max(0, c1 - LONG)
            Wl = A[c0:c1]
            MV.append(W.mean(0))
            SV.append(W.std(0))
            EX.append(band_fracs(W, BANDS) + [spectral_centroid(W)]
                      + band_fracs(Wl, LOWBANDS))
            y.append(s['label'])
            groups.append(s['session'])
    return (np.asarray(MV), np.asarray(SV), np.asarray(EX),
            np.asarray(y), np.asarray(groups))


def build_calibrated(MV, SV, EX, groups, y, calib_mask):
    """rel(dev from empty) + per-subcarrier std + extra motion feats + summaries,
    per-session z-scored against that session's calibration-empty windows."""
    nsc = MV.shape[1]
    X = np.zeros((len(y), nsc + nsc + EX.shape[1] + 4))
    for sid in set(groups):
        m = groups == sid
        cal = m & calib_mask
        base = MV[cal].mean(0) if cal.sum() else MV[m].mean(0)
        for i in np.where(m)[0]:
            rel = (MV[i] - base) / (base + 1e-6)
            summ = [np.abs(rel).mean(), np.abs(rel).max(),
                    np.linalg.norm(rel), SV[i].mean()]
            X[i] = np.concatenate([rel, SV[i], EX[i], summ])
        ref = X[cal] if cal.sum() >= 3 else X[m]
        mu, sd = ref.mean(0), ref.std(0) + 1e-6
        X[m] = (X[m] - mu) / sd
    return X


def report(yt, yp, labels, name):
    fa = []
    yt, yp = np.asarray(yt), np.asarray(yp)
    print(f"\n[{name}]")
    print(f"  accuracy {accuracy_score(yt, yp):.3f}   "
          f"balanced {balanced_accuracy_score(yt, yp):.3f}")
    print(classification_report(yt, yp, labels=labels, zero_division=0))
    cm = confusion_matrix(yt, yp, labels=labels)
    print("Confusion (rows=true, cols=pred):")
    print("        " + "  ".join(f"{l:>7}" for l in labels))
    for i, l in enumerate(labels):
        print(f"{l:>7} " + "  ".join(f"{cm[i, j]:7d}" for j in range(len(labels))))


def smooth(pred, k=5):
    """Rolling majority vote over k consecutive windows (temporal smoothing).
    Applied within one held-out session's window sequence (recording order)."""
    out = np.array(pred, dtype=object)
    h = k // 2
    for i in range(len(pred)):
        seg = pred[max(0, i - h):i + h + 1]
        out[i] = Counter(seg).most_common(1)[0][0]
    return out


def hier_loso(X, y, groups, calib_mask, configs):
    score = ~calib_mask
    ysuper = np.array([SUPER[c] for c in y])
    yt, yp, ys, s1t, s1p, fold = [], [], [], [], [], []
    for sid in sorted(set(groups)):
        tr = score & (groups != sid)
        te = score & (groups == sid)
        c1 = _rf(); c1.fit(X[tr], ysuper[tr])
        sta = tr & np.isin(y, ['sit', 'stand'])
        mov = tr & np.isin(y, ['walk', 'run'])
        c2a = _rf(); c2a.fit(X[sta], y[sta])
        c2b = _rf(); c2b.fit(X[mov], y[mov])

        Xte = X[te]
        s1 = c1.predict(Xte)
        pred = np.array(['empty'] * len(s1), dtype=object)
        ms, mm = s1 == 'static', s1 == 'moving'
        if ms.any():
            pred[ms] = c2a.predict(Xte[ms])
        if mm.any():
            pred[mm] = c2b.predict(Xte[mm])
        pred_s = smooth(pred, k=5)
        fold.append((configs.get(sid, sid), accuracy_score(y[te], pred),
                     accuracy_score(y[te], pred_s)))
        yt.extend(y[te]); yp.extend(pred); ys.extend(pred_s)
        s1t.extend(ysuper[te]); s1p.extend(s1)
    print("\nPer held-out config (hierarchical 5-class acc  raw | smoothed):")
    for cfg, a, asm in fold:
        print(f"    {cfg:<16} {a:.3f} | {asm:.3f}")
    report(s1t, s1p, SUPER_LABELS, "STAGE 1  empty / static / moving")
    labels = [c for c in CLASSES if c in set(y)]
    report(yt, yp, labels, "HIERARCHICAL  final 5-class (per-window)")
    report(yt, ys, labels, "HIERARCHICAL  final 5-class (+ temporal smoothing, k=5)")


def main():
    segs, configs = pa.discover()
    sids = sorted(set(s['session'] for s in segs))
    print("Configs:", [configs[s] for s in sids])
    print(f"Segments per-label: {dict(Counter(s['label'] for s in segs))}")

    # active-subcarrier mask (same rule as phase_a_analysis)
    total, count = None, 0
    for s in segs:
        amp, _ = pa.load_seg(s['csv'])
        if amp.size:
            total = amp.sum(0) if total is None else total + amp.sum(0)
            count += len(amp)
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])

    MV, SV, EX, y, groups = build_windows(segs, mask)
    print(f"Windows: {len(y)}  per-class: {dict(Counter(y))}")
    print(f"Feature dim: {2 * MV.shape[1] + EX.shape[1] + 4} "
          f"(rel {MV.shape[1]} + std {MV.shape[1]} + motion {EX.shape[1]} + summ 4)")

    calib_mask = np.zeros(len(y), bool)
    for sid in sids:
        emp = np.where((groups == sid) & (y == 'empty'))[0]
        calib_mask[emp[::2]] = True

    X = build_calibrated(MV, SV, EX, groups, y, calib_mask)
    labels = [c for c in CLASSES if c in set(y)]

    # ablation A: flat 5-class on the SAME richer features
    pa.loso(X, y, groups, calib_mask, labels, "FLAT 5-class (richer features)", configs)
    # ablation B: hierarchical
    hier_loso(X, y, groups, calib_mask, configs)


if __name__ == '__main__':
    main()
