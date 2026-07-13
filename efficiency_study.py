#!/usr/bin/env python3
"""
Efficiency study (boss request 2026-06-26: "model efficiency matters").

Three axes of model/feature complexity vs. leave-one-SESSION-out accuracy on
the calibrated 5-class task, all using the existing 5-session dataset (no new
recording needed):

  1. Trees        RandomForest n_estimators           -- inference compute
  2. Features     top-K features by TRAINING-fold RF importance
                  (re-ranked per held-out fold, so selection never sees the
                  test session) -- feature/compute cost of the final model
  3. Window       re-windowed from raw amplitude at several lengths
                  -- update latency / rate

Reuses discover/load_seg/motion_frac/subband_feats/build_calibrated from
phase_a_analysis.py so the feature definitions stay identical to the main
pipeline; only the sweep/scoring loops are new.

Usage:  python efficiency_study.py
Requires: numpy, scikit-learn, matplotlib.
"""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score

import csi_dataset as cd
import phase_a_analysis as pa

OUT = 'data'


def build_dataset(win, hop):
    """Rebuild calibrated features at a given (win, hop) sample length."""
    segs, configs = pa.discover()
    total, count = None, 0
    for s in segs:
        amp, _ = pa.load_seg(s['csv'])
        if amp.size:
            total = amp.sum(0) if total is None else total + amp.sum(0)
            count += len(amp)
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])

    y, groups = [], []
    MV, SV, MO, SB = [], [], [], []
    for s in segs:
        amp, rssi = pa.load_seg(s['csv'])
        if amp.size == 0:
            continue
        A = amp[:, mask]
        for a, b in cd.window_indices(len(A), win, hop):
            W = A[a:b]
            MV.append(W.mean(0)); SV.append(W.std(0)); MO.append(pa.motion_frac(W))
            SB.append(pa.subband_feats(W))
            y.append(s['label']); groups.append(s['session'])
    MV, SV, MO, SB = (np.asarray(x) for x in (MV, SV, MO, SB))
    y, groups = np.asarray(y), np.asarray(groups)

    calib_mask = np.zeros(len(y), bool)
    for sid in sorted(set(groups)):
        emp = np.where((groups == sid) & (y == 'empty'))[0]
        calib_mask[emp[::2]] = True

    Xcal = pa.build_calibrated(MV, SV, MO, SB, groups, y, calib_mask)
    return Xcal, y, groups, calib_mask


def _loso_scores(X, y, groups, calib_mask, clf_factory):
    """Quiet leave-one-session-out: pooled accuracy/balanced-accuracy only
    (no per-fold printing) -- this script reports one summary line per sweep
    point instead of a full report per point."""
    score = ~calib_mask
    yt, yp = [], []
    for sid in sorted(set(groups)):
        tr = score & (groups != sid)
        te = score & (groups == sid)
        clf = clf_factory()
        clf.fit(X[tr], y[tr])
        pred = clf.predict(X[te])
        yt.extend(y[te]); yp.extend(pred)
    yt, yp = np.asarray(yt), np.asarray(yp)
    return accuracy_score(yt, yp), balanced_accuracy_score(yt, yp)


def trees_sweep(Xcal, y, groups, calib_mask, n_values):
    print("\n=== Efficiency axis 1: RandomForest tree count ===")
    rows = []
    for n in n_values:
        acc, bal = _loso_scores(Xcal, y, groups, calib_mask,
                                lambda n=n: RandomForestClassifier(
                                    n_estimators=n, random_state=0, n_jobs=-1,
                                    class_weight='balanced'))
        print(f"  trees={n:<4d}  acc={acc:.3f}  balanced={bal:.3f}")
        rows.append((n, acc, bal))
    return rows


def feature_count_sweep(Xcal, y, groups, calib_mask, k_values):
    """For each K, per held-out session: rank features on the TRAINING folds
    only, keep the top K, retrain restricted to those, then score the held-out
    session. Selection never sees test-session data."""
    print("\n=== Efficiency axis 2: feature count (top-K by training-fold importance) ===")
    print(f"  (full feature vector has {Xcal.shape[1]} columns)")
    score = ~calib_mask
    sids = sorted(set(groups))
    rows = []
    for k in k_values:
        yt, yp = [], []
        for sid in sids:
            tr = score & (groups != sid)
            te = score & (groups == sid)
            ranker = RandomForestClassifier(n_estimators=200, random_state=0,
                                            n_jobs=-1, class_weight='balanced')
            ranker.fit(Xcal[tr], y[tr])
            top = np.argsort(ranker.feature_importances_)[::-1][:k]
            clf = RandomForestClassifier(n_estimators=400, random_state=0,
                                         n_jobs=-1, class_weight='balanced')
            clf.fit(Xcal[tr][:, top], y[tr])
            pred = clf.predict(Xcal[te][:, top])
            yt.extend(y[te]); yp.extend(pred)
        yt, yp = np.asarray(yt), np.asarray(yp)
        acc = accuracy_score(yt, yp)
        bal = balanced_accuracy_score(yt, yp)
        print(f"  top-{k:<4d} features  acc={acc:.3f}  balanced={bal:.3f}")
        rows.append((k, acc, bal))
    return rows


def window_sweep(win_values):
    print("\n=== Efficiency axis 3: window length ===")
    rows = []
    for win in win_values:
        hop = win // 2
        Xcal, y, groups, calib_mask = build_dataset(win, hop)
        acc, bal = _loso_scores(Xcal, y, groups, calib_mask,
                                lambda: RandomForestClassifier(
                                    n_estimators=400, random_state=0, n_jobs=-1,
                                    class_weight='balanced'))
        secs = win / cd.FS
        print(f"  window={win:<4d} ({secs:.1f}s, hop {hop})  "
              f"acc={acc:.3f}  balanced={bal:.3f}  n_windows={len(y)}")
        rows.append((win, secs, acc, bal, len(y)))
    return rows


def main():
    print("Building default-window dataset (win=200, hop=100, i.e. the "
          "phase_a_analysis.py default) for the trees/features sweeps...")
    Xcal, y, groups, calib_mask = build_dataset(pa.WIN, pa.HOP)
    print(f"Windows: {len(y)}  feature dims: {Xcal.shape[1]}")

    tree_rows = trees_sweep(Xcal, y, groups, calib_mask,
                            [10, 25, 50, 100, 200, 400, 800])
    feat_rows = feature_count_sweep(Xcal, y, groups, calib_mask,
                                    [10, 20, 40, 80, Xcal.shape[1]])
    # NOTE: feature count here is the size of the CLASSIFIER's input vector,
    # not the number of active subcarriers sampled -- shrinking it saves
    # compute/memory in the final RandomForest, but the upstream per-window
    # feature extraction still reads all 52 active subcarriers regardless.
    win_rows = window_sweep([100, 200, 300, 500, 800])

    fig, ax = plt.subplots(1, 3, figsize=(13, 4))
    ns = [r[0] for r in tree_rows]
    ax[0].plot(ns, [r[1] for r in tree_rows], 'o-', label='Accuracy')
    ax[0].plot(ns, [r[2] for r in tree_rows], 's-', label='Balanced acc.')
    ax[0].set_xscale('log'); ax[0].set_xlabel('Trees (n_estimators)')
    ax[0].set_ylabel('Score'); ax[0].set_ylim(0, 1); ax[0].legend(fontsize=8)

    ks = [r[0] for r in feat_rows]
    ax[1].plot(ks, [r[1] for r in feat_rows], 'o-', label='Accuracy')
    ax[1].plot(ks, [r[2] for r in feat_rows], 's-', label='Balanced acc.')
    ax[1].set_xlabel('Feature count (top-K)')
    ax[1].set_ylim(0, 1); ax[1].legend(fontsize=8)

    secs = [r[1] for r in win_rows]
    ax[2].plot(secs, [r[2] for r in win_rows], 'o-', label='Accuracy')
    ax[2].plot(secs, [r[3] for r in win_rows], 's-', label='Balanced acc.')
    ax[2].set_xlabel('Window length (s)')
    ax[2].set_ylim(0, 1); ax[2].legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(f'{OUT}/efficiency_study.png', dpi=120)
    print(f"\nSaved {OUT}/efficiency_study.png")


if __name__ == '__main__':
    main()
