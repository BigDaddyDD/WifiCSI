#!/usr/bin/env python3
"""
Best presence model for the current setup (this room, one TX-RX link, webcam).

Combines everything that worked:
  - calibrated features (deviation from each environment's empty baseline)
  - per-fold decision-threshold tuning (picked on TRAIN only -> honest)
  - temporal smoothing of window probabilities (presence is persistent in time)

Reports occupied-vs-empty at three operating points for both
  - pooled  (leave-one-take-out)
  - cross-orientation (leave-one-room-out)

Usage:  python presence_model.py
"""

import json
import os
from collections import Counter

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix)

from phase1_av import load_csi, load_labels, discover_takes
import csi_dataset as cd

WIN_S, HOP_S, MIN_PKTS, PURITY = 2.0, 1.0, 100, 0.7
SMOOTH_K = 5          # windows (~5 s at hop=1 s)


def breathing_feats(Wc, fs):
    """Slow periodic micro-motion (0.15-0.5 Hz) over a long context window.
    Returns [mean band-fraction, max band-fraction, mean peak-sharpness,
    max peak-sharpness]."""
    if Wc.shape[0] < int(fs * 8):           # need >= ~8 s for frequency resolution
        return np.zeros(4)
    power = np.abs(np.fft.rfft(Wc - Wc.mean(0), axis=0)) ** 2
    fr = np.fft.rfftfreq(Wc.shape[0], d=1.0 / fs)
    band = (fr >= 0.15) & (fr <= 0.5)
    if band.sum() == 0:
        return np.zeros(4)
    frac = power[band].sum(0) / (power.sum(0) + 1e-9)
    bandp = power[band]
    peak = bandp.max(0) / (bandp.mean(0) + 1e-9)
    return np.array([frac.mean(), frac.max(), peak.mean(), peak.max()])


def build(win=WIN_S, hop=HOP_S, breathing=True):
    loaded, total, count = [], None, 0
    for d in discover_takes('data/av'):
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
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])

    recs = []
    for take, room, pc, amp, lt, lab in loaded:
        A = amp[:, mask]
        t = pc[0]
        while t + win <= pc[-1]:
            a, b = t, t + win
            t += hop
            csel = (pc >= a) & (pc < b)
            if csel.sum() < MIN_PKTS:
                continue
            lsel = (lt >= a) & (lt < b)
            if lsel.sum() == 0:
                continue
            top, nt = Counter(lab[lsel]).most_common(1)[0]
            if nt / lsel.sum() < PURITY:
                continue
            W = A[csel]
            mv, sv = W.mean(0), W.std(0)
            power = np.abs(np.fft.rfft(W - mv, axis=0)) ** 2
            fr = np.fft.rfftfreq(W.shape[0], d=1.0 / cd.FS)
            bnd = (fr >= 0.5) & (fr <= 5.0)
            mo = power[bnd].sum(0) / (power.sum(0) + 1e-9)
            br = np.zeros(4)
            if breathing:
                cc = (pc >= a - 9) & (pc < b + 9)     # ~20 s context
                br = breathing_feats(A[cc], cd.FS)
            recs.append(dict(take=take, room=room, label=top, time=a,
                             mean=mv, std=sv, motion=mo, breath=br))

    base = {}
    for room in set(r['room'] for r in recs):
        emp = [r['mean'] for r in recs if r['room'] == room and r['label'] == 'empty']
        src = emp if emp else [r['mean'] for r in recs if r['room'] == room]
        base[room] = np.mean(src, axis=0)

    X, yb, y3, take_a, room_a, time_a = [], [], [], [], [], []
    for r in recs:
        bb = base[r['room']]
        rel = (r['mean'] - bb) / (bb + 1e-6)
        cos = float(np.dot(r['mean'], bb)
                    / (np.linalg.norm(r['mean']) * np.linalg.norm(bb) + 1e-9))
        summ = np.array([np.abs(rel).mean(), np.abs(rel).max(), np.linalg.norm(rel),
                         1 - cos, r['std'].mean(), r['std'].max(),
                         r['motion'].mean(), r['motion'].max()])
        X.append(np.concatenate([rel, r['std'], r['motion'], summ, r['breath']]))
        yb.append('occupied' if r['label'] in ('still', 'moving') else 'empty')
        y3.append(r['label'])
        take_a.append(r['take'])
        room_a.append(r['room'])
        time_a.append(r['time'])
    return (np.asarray(X), np.asarray(yb), np.asarray(y3),
            np.asarray(take_a), np.asarray(room_a), np.asarray(time_a))


def best_threshold(p, y):
    best_t, best_b = 0.5, -1.0
    for t in np.linspace(0.15, 0.85, 29):
        b = balanced_accuracy_score(y, (p > t).astype(int))
        if b > best_b:
            best_b, best_t = b, t
    return best_t


def smooth(proba, takes, times, k=SMOOTH_K):
    out = proba.copy()
    for tk in np.unique(takes):
        idx = np.where(takes == tk)[0]
        order = idx[np.argsort(times[idx])]
        out[order] = np.convolve(proba[order], np.ones(k) / k, mode='same')
    return out


def report(occ, pred, tag):
    cm = confusion_matrix(occ, pred, labels=[0, 1])
    er = cm[0, 0] / cm[0].sum() if cm[0].sum() else 0
    orr = cm[1, 1] / cm[1].sum() if cm[1].sum() else 0
    print(f"  {tag:20} acc {accuracy_score(occ, pred):.3f}  "
          f"balanced {balanced_accuracy_score(occ, pred):.3f}  "
          f"empty-recall {er:.2f}  occ-recall {orr:.2f}")


def evaluate(X, yb, groups, takes, times, name, rooms=None):
    occ = (yb == 'occupied').astype(int)
    logo = LeaveOneGroupOut()
    proba = np.zeros(len(occ))
    proba_sm = np.zeros(len(occ))
    pred_tuned = np.zeros(len(occ), int)
    for tr, te in logo.split(X, occ, groups):
        clf = RandomForestClassifier(n_estimators=400, random_state=0,
                                     n_jobs=-1, class_weight='balanced')
        clf.fit(X[tr], occ[tr])
        thr = best_threshold(clf.predict_proba(X[tr])[:, 1], occ[tr])
        pte = clf.predict_proba(X[te])[:, 1]
        proba[te] = pte
        proba_sm[te] = smooth(pte, takes[te], times[te])
        pred_tuned[te] = (pte > thr).astype(int)
    print(f"\n[{name}]")
    report(occ, (proba > 0.5).astype(int), 'raw @ 0.5')
    report(occ, (proba_sm > 0.5).astype(int), 'smoothed @ 0.5')
    report(occ, pred_tuned, 'train-tuned thr')
    if rooms is not None and len(set(rooms)) > 1:
        p05 = (proba > 0.5).astype(int)
        print("  per held-out environment (raw @ 0.5):")
        for rm in sorted(set(rooms)):
            m = rooms == rm
            report(occ[m], p05[m], f'    {rm}')


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--win', type=float, default=WIN_S)
    ap.add_argument('--hop', type=float, default=HOP_S)
    ap.add_argument('--no-breathing', dest='breathing', action='store_false',
                    help='ablation: disable breathing features (default: on)')
    ap.add_argument('--no-zcal', dest='zcal', action='store_false',
                    help='ablation: disable per-environment scale calibration '
                         '(z-score vs that env empty windows). Default: on.')
    args = ap.parse_args()
    X, yb, y3, takes, rooms, times = build(args.win, args.hop, args.breathing)
    if args.zcal:
        for rm in set(rooms):
            m = rooms == rm
            emp = m & (yb == 'empty')
            if emp.sum() < 5:
                continue
            mu, sd = X[emp].mean(0), X[emp].std(0) + 1e-6
            X[m] = (X[m] - mu) / sd
    print(f"Windows: {len(yb)}  {dict(Counter(yb))}  "
          f"takes={len(set(takes))}  rooms={sorted(set(rooms))}  "
          f"win={args.win}s hop={args.hop}s  zcal={args.zcal}")
    evaluate(X, yb, takes, takes, times, 'Pooled (leave-one-take-out)')
    if len(set(rooms)) >= 2:
        evaluate(X, yb, rooms, takes, times,
                 'CROSS-ORIENTATION (leave-one-room-out)', rooms=rooms)


if __name__ == '__main__':
    main()
