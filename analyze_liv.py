#!/usr/bin/env python3
"""
Interim analysis of the new-environment (liv_room) data recorded so far, paired
with home_L. Answers three "are we heading in the right direction?" questions:

  1. Does presence / 5-class activity hold up WITHIN the new room?
     (leave-one-config-out across the liv_room configs)
  2. Does a model trained on home_L TRANSFER to liv_room? (the portability goal —
     train on all home_L configs, test on liv_room, per-config empty calibration)
  3. Is there any DIRECTION signal to build on? (walk/run R2L-vs-L2R, walk
     CW-vs-CCW) — a first probe with current + temporal-slope features.

Calibration: per CONFIG (placement/orientation). liv_room configs use their
dedicated 5-min BASELINE session as the empty reference (held out of scoring);
home_L configs (no separate baseline) use a spread half of their own empties.

CAVEAT printed loudly: only 2 liv_room configs so far -> 2-fold, wide error;
this is a directional read, not a confidence-interval result.

Usage:  python analyze_liv.py
"""
import glob
import json
import os
from collections import Counter

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix, recall_score)

import phase_a_analysis as pa
import csi_dataset as cd

WIN, HOP = 200, 100
CLASSES = pa.CLASSES


def _rf():
    return RandomForestClassifier(n_estimators=400, random_state=0,
                                  n_jobs=-1, class_weight='balanced')


def load_segments(room):
    segs = []
    for sj in sorted(glob.glob(f'data/study/{room}/*/session.json')):
        d = os.path.dirname(sj); m = json.load(open(sj))
        cfg = m.get('config') or f"{m.get('placement','?')}/{m.get('node_orientation','?')}"
        stype = m.get('session_type', 'activity')
        seg_csvs = sorted(glob.glob(os.path.join(d, 'seg*__*.csv')))
        if stype in ('activity', '2person') and len(seg_csvs) < 8:
            continue                      # skip interrupted/incomplete sessions (home_L=10, stub=6)
        for cf in seg_csvs:
            label = os.path.basename(cf).split('_')[1]
            if label not in CLASSES:
                continue
            jf = cf.split('__')[0] + '.json'
            direction = ''
            if os.path.exists(jf):
                direction = json.load(open(jf)).get('direction', '')
            segs.append({'csv': cf, 'label': label, 'direction': direction,
                         'config': f'{room}:{cfg}', 'room': room, 'stype': stype})
    return segs


def slope_vec(W):
    t = np.arange(W.shape[0], dtype=float)
    tc = t - t.mean()
    return (tc[:, None] * (W - W.mean(0))).sum(0) / (tc ** 2).sum()


def build(rooms):
    segs = []
    for r in rooms:
        segs += load_segments(r)
    # global active-subcarrier mask (shared across rooms; hardware-fixed)
    total, count = None, 0
    amps = {}
    for i, s in enumerate(segs):
        a, _ = pa.load_seg(s['csv'])
        amps[i] = a
        if a.size:
            total = a.sum(0) if total is None else total + a.sum(0)
            count += len(a)
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])

    MV, SV, MO, SL = [], [], [], []
    y, direction, config, room, stype = [], [], [], [], []
    for i, s in enumerate(segs):
        a = amps[i]
        if a.size == 0:
            continue
        A = a[:, mask]
        for lo, hi in cd.window_indices(len(A), WIN, HOP):
            W = A[lo:hi]
            MV.append(W.mean(0)); SV.append(W.std(0))
            MO.append(pa.motion_frac(W)); SL.append(slope_vec(W))
            y.append(s['label']); direction.append(s['direction'])
            config.append(s['config']); room.append(s['room'])
            stype.append(s['stype'])
    d = dict(MV=np.asarray(MV), SV=np.asarray(SV), MO=np.asarray(MO),
             SL=np.asarray(SL), y=np.asarray(y), direction=np.asarray(direction),
             config=np.asarray(config), room=np.asarray(room),
             stype=np.asarray(stype), nsc=int(mask.sum()))
    set_calib(d, 'auto')
    return d


def set_calib(d, mode):
    """Choose each config's empty CALIBRATION reference (held out of scoring).
      baseline : liv_room's separate 5-min baseline session (deployment recipe)
      brackets : spread half of the activity session's own empty brackets
      allspread: spread half of ALL the config's empties (home_L style)
      auto     : baseline if present else allspread
    """
    y, cfg, st = d['y'], d['config'], d['stype']
    calib = np.zeros(len(y), bool)
    for c in set(cfg):
        m = cfg == c
        base = np.where(m & (y == 'empty') & (st == 'baseline'))[0]
        brk = np.where(m & (y == 'empty') & (st != 'baseline'))[0]
        alle = np.where(m & (y == 'empty'))[0]
        if mode == 'baseline' and len(base):
            calib[base] = True
        elif mode == 'brackets' and len(brk):
            calib[brk[::2]] = True
        elif mode == 'auto' and len(base):
            calib[base] = True
        else:
            calib[alle[::2]] = True
    d['calib'] = calib
    return d


def calibrated(d, slope=False):
    MV, SV, MO, SL = d['MV'], d['SV'], d['MO'], d['SL']
    cfg, cal = d['config'], d['calib']
    nsc = MV.shape[1]
    width = 3 * nsc + (nsc if slope else 0) + 4
    X = np.zeros((len(MV), width))
    for c in set(cfg):
        m = cfg == c
        ref = np.where(m & cal)[0]
        if len(ref) < 3:
            ref = np.where(m)[0]
        base = MV[ref].mean(0)
        for i in np.where(m)[0]:
            rel = (MV[i] - base) / (base + 1e-6)
            blocks = [rel, SV[i], MO[i]] + ([SL[i]] if slope else [])
            summ = [np.abs(rel).mean(), np.abs(rel).max(),
                    np.linalg.norm(rel), SV[i].mean()]
            X[i] = np.concatenate(blocks + [summ])
        mu, sd = X[ref].mean(0), X[ref].std(0) + 1e-6
        X[m] = (X[m] - mu) / sd
    return X


def show(yt, yp, name, labels=None):
    yt, yp = np.asarray(yt), np.asarray(yp)
    print(f"\n[{name}]  acc {accuracy_score(yt, yp):.3f}  "
          f"balanced {balanced_accuracy_score(yt, yp):.3f}  (n={len(yt)})")
    if labels and len(labels) <= 6:
        rec = recall_score(yt, yp, labels=labels, average=None, zero_division=0)
        print("   recall: " + "  ".join(f"{l}={r:.2f}" for l, r in zip(labels, rec)))


def loco(X, y, groups, score, name, labels=None):
    """Leave-one-config-out over the given (masked) groups."""
    cfgs = sorted(set(groups[score]))
    yt, yp = [], []
    for c in cfgs:
        tr = score & (groups != c)
        te = score & (groups == c)
        if te.sum() == 0 or tr.sum() == 0:
            continue
        clf = _rf(); clf.fit(X[tr], y[tr])
        yt.extend(y[te]); yp.extend(clf.predict(X[te]))
    show(yt, yp, f"{name}  (leave-one-config-out, {len(cfgs)} folds)", labels)


def main():
    d = build(['home_L', 'liv_room'])
    room, cfg, y, direction = d['room'], d['config'], d['y'], d['direction']
    liv = room == 'liv_room'
    home = room == 'home_L'
    score = ~d['calib']
    Xc = calibrated(d, slope=False)
    yb = np.where(y == 'empty', 'empty', 'occupied')
    labels5 = [c for c in CLASSES if c in set(y[liv])]

    print("=" * 70)
    print("liv_room configs:", sorted(set(cfg[liv])))
    print("liv_room windows per class:", dict(Counter(y[liv & score])))
    print("home_L configs:", sorted(set(cfg[home])))
    print(f"active subcarriers: {d['nsc']}")
    nliv = len(set(cfg[liv]))
    print(f"NOTE: {nliv} liv_room configs -> {nliv}-fold, still thin. Directional read, not final CIs.")

    print("\n" + "=" * 70 + "\n0) CALIBRATION-SOURCE diagnostic (liv presence, leave-one-config-out)")
    for mode in ['baseline', 'brackets', 'allspread']:
        set_calib(d, mode)
        Xm = calibrated(d, slope=False)
        sc = ~d['calib']
        loco(Xm, yb, cfg, sc & liv, f"liv presence  calib={mode}", ['empty', 'occupied'])
    # settle on brackets (within-session) for the rest — most robust to link drift
    set_calib(d, 'brackets')
    score = ~d['calib']
    Xc = calibrated(d, slope=False)

    print("\n" + "=" * 70 + "\n1) WITHIN liv_room  (calib=brackets)")
    loco(Xc, yb, cfg, score & liv, "liv presence", ['empty', 'occupied'])
    loco(Xc, y, cfg, score & liv, "liv 5-class", labels5)

    print("\n" + "=" * 70 + "\n2) CROSS-ENVIRONMENT  (train home_L -> test liv_room)")
    for tgt, name, labs in [(yb, "presence", ['empty', 'occupied']),
                            (y, "5-class", labels5)]:
        tr = score & home
        te = score & liv
        clf = _rf(); clf.fit(Xc[tr], tgt[tr])
        show(te_y := tgt[te], clf.predict(Xc[te]),
             f"home_L -> liv_room {name}", labs)

    print("\n" + "=" * 70 + "\n3) DIRECTION probe (liv_room, current + temporal-slope features)")
    Xd = calibrated(d, slope=True)
    probes = [('walk', ['R2L', 'L2R'], 'walk L-R direction'),
              ('run', ['R2L', 'L2R'], 'run L-R direction'),
              ('walk', ['CW', 'CCW'], 'walk rotation CW/CCW')]
    for lab, dirs, name in probes:
        sub = liv & (y == lab) & np.isin(direction, dirs)
        if sub.sum() < 20 or len(set(cfg[sub])) < 2:
            print(f"\n[{name}] too few windows/configs (n={int(sub.sum())})")
            continue
        # leave-one-config-out so it's not memorizing one pass
        yt, yp = [], []
        for c in sorted(set(cfg[sub])):
            tr = sub & (cfg != c); te = sub & (cfg == c)
            if te.sum() == 0 or tr.sum() == 0 or len(set(direction[tr])) < 2:
                continue
            clf = _rf(); clf.fit(Xd[tr], direction[tr])
            yt.extend(direction[te]); yp.extend(clf.predict(Xd[te]))
        if yt:
            show(yt, yp, f"{name}  (LOCO; chance=0.50)", dirs)


if __name__ == '__main__':
    main()
