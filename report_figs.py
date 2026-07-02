#!/usr/bin/env python3
"""
Generate the figures + metrics for the brief Phase-A report.

Reuses the exact feature/calibration/evaluation code in phase_a_analysis.py so
the report numbers match the analysis. Plain matplotlib (default styling).
Writes PNGs to docs/report_assets/ and a metrics.json used by build_report.py.
"""
import json
import os
from collections import Counter

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix, recall_score)

import phase_a_analysis as pa
import csi_dataset as cd

OUT = os.path.join('docs', 'report_assets')
os.makedirs(OUT, exist_ok=True)


def build_windows():
    segs, configs = pa.discover()
    sids = sorted(set(s['session'] for s in segs))
    total = None; count = 0
    for s in segs:
        amp, _ = pa.load_seg(s['csv'])
        if amp.size:
            total = amp.sum(0) if total is None else total + amp.sum(0)
            count += len(amp)
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])

    Xc, Xr, y, groups = [], [], [], []
    MV, SV, MO = [], [], []
    for s in segs:
        amp, rssi = pa.load_seg(s['csv'])
        if amp.size == 0:
            continue
        A = amp[:, mask]
        for a, b in cd.window_indices(len(A), pa.WIN, pa.HOP):
            W = A[a:b]
            Xc.append(cd.window_features(W))
            MV.append(W.mean(0)); SV.append(W.std(0)); MO.append(pa.motion_frac(W))
            rw = rssi[a:b]
            Xr.append([rw.mean(), rw.std(), rw.min(), rw.max()])
            y.append(s['label']); groups.append(s['session'])
    d = dict(Xc=np.asarray(Xc), Xr=np.asarray(Xr), MV=np.asarray(MV),
             SV=np.asarray(SV), MO=np.asarray(MO), y=np.asarray(y),
             groups=np.asarray(groups), configs=configs, sids=sids, nsc=int(mask.sum()))
    return d


def loso(X, y, groups, calib_mask):
    score = ~calib_mask
    yt, yp, folds = [], [], []
    for sid in sorted(set(groups)):
        tr = score & (groups != sid); te = score & (groups == sid)
        clf = RandomForestClassifier(n_estimators=400, random_state=0,
                                     n_jobs=-1, class_weight='balanced')
        clf.fit(X[tr], y[tr]); pred = clf.predict(X[te])
        folds.append((sid, accuracy_score(y[te], pred)))
        yt.extend(y[te]); yp.extend(pred)
    return np.asarray(yt), np.asarray(yp), folds


def spread_mask(y, groups, sids):
    cm = np.zeros(len(y), bool)
    for sid in sids:
        emp = np.where((groups == sid) & (y == 'empty'))[0]; cm[emp[::2]] = True
    return cm


def contig_mask(y, groups, sids):
    cm = np.zeros(len(y), bool)
    for sid in sids:
        emp = np.where((groups == sid) & (y == 'empty'))[0]; cm[emp[:len(emp) // 2]] = True
    return cm


def main():
    d = build_windows()
    Xc, Xr, y, groups = d['Xc'], d['Xr'], d['y'], d['groups']
    MV, SV, MO, sids, configs = d['MV'], d['SV'], d['MO'], d['sids'], d['configs']
    labels = [c for c in pa.CLASSES if c in set(y)]
    yb = np.where(y == 'empty', 'empty', 'occupied')
    cmask = spread_mask(y, groups, sids)
    Xcal = pa.build_calibrated(MV, SV, MO, groups, y, cmask)

    M = {'n_sessions': len(sids), 'n_windows': int(len(y)),
         'per_class': {k: int(v) for k, v in Counter(y).items()},
         'n_active_sc': d['nsc'],
         'configs': [configs[s] for s in sids]}

    def acc_pair(X, target, name):
        yt, yp, folds = loso(X, target, groups, cmask)
        return {'name': name, 'acc': accuracy_score(yt, yp),
                'bal': balanced_accuracy_score(yt, yp),
                'folds': [(configs[s], a) for s, a in folds],
                'yt': yt, 'yp': yp}

    pres_raw = acc_pair(Xc, yb, 'Raw CSI')
    pres_cal = acc_pair(Xcal, yb, 'Calibrated CSI')
    act_rssi = acc_pair(Xr, y, 'RSSI baseline')
    act_raw = acc_pair(Xc, y, 'Raw CSI')
    act_cal = acc_pair(Xcal, y, 'Calibrated CSI')

    # ---- Figure 1: method comparison (2 panels) --------------------------------
    fig, ax = plt.subplots(1, 2, figsize=(9, 4))
    for a, group, title in [
            (ax[0], [pres_raw, pres_cal], 'Presence (empty vs occupied)'),
            (ax[1], [act_rssi, act_raw, act_cal], '5-class activity')]:
        names = [g['name'] for g in group]
        xs = np.arange(len(names)); w = 0.38
        a.bar(xs - w / 2, [g['acc'] for g in group], w, label='Accuracy', color='#4C72B0')
        a.bar(xs + w / 2, [g['bal'] for g in group], w, label='Balanced acc.', color='#C44E52')
        for i, g in enumerate(group):
            a.text(i - w / 2, g['acc'] + .01, f"{g['acc']:.2f}", ha='center', fontsize=8)
            a.text(i + w / 2, g['bal'] + .01, f"{g['bal']:.2f}", ha='center', fontsize=8)
        a.set_xticks(xs); a.set_xticklabels(names, fontsize=9)
        a.set_ylim(0, 1.18); a.set_ylabel('Score'); a.set_title(title, fontsize=10)
        a.axhline(0.5 if 'Presence' in title else 0.30, ls='--', lw=.8,
                  color='gray', label='Chance / majority')
        a.legend(fontsize=7, loc='upper center', ncol=3)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig1_methods.png'), dpi=120)
    plt.close(fig)

    # ---- Figure 2: presence confusion (calibrated) -----------------------------
    cm = confusion_matrix(pres_cal['yt'], pres_cal['yp'], labels=['empty', 'occupied'])
    fig, a = plt.subplots(figsize=(4.2, 3.8))
    a.imshow(cm, cmap='Blues')
    a.set_xticks([0, 1]); a.set_xticklabels(['empty', 'occupied'])
    a.set_yticks([0, 1]); a.set_yticklabels(['empty', 'occupied'])
    a.set_xlabel('Predicted'); a.set_ylabel('Actual')
    a.set_title('Presence confusion (calibrated)', fontsize=10)
    for i in range(2):
        for j in range(2):
            a.text(j, i, cm[i, j], ha='center', va='center',
                   color='white' if cm[i, j] > cm.max() / 2 else 'black')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig2_presence_confusion.png'), dpi=120)
    plt.close(fig)

    # ---- Figure 3: per-class recall, 5-class (calibrated) ----------------------
    rec = recall_score(act_cal['yt'], act_cal['yp'], labels=labels, average=None, zero_division=0)
    fig, a = plt.subplots(figsize=(5.2, 3.6))
    colors = ['#55A868' if r >= 0.5 else '#C44E52' for r in rec]
    a.bar(labels, rec, color=colors)
    for i, r in enumerate(rec):
        a.text(i, r + .01, f"{r:.2f}", ha='center', fontsize=9)
    a.set_ylim(0, 1); a.set_ylabel('Recall (correctly identified)')
    a.set_title('Per-activity recall, unseen configuration', fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig3_perclass_recall.png'), dpi=120)
    plt.close(fig)

    # ---- Figure 4: empty-baseline sensitivity ----------------------------------
    ccm = contig_mask(y, groups, sids)
    Xcal_c = pa.build_calibrated(MV, SV, MO, groups, y, ccm)
    yt_c, yp_c, _ = loso(Xcal_c, yb, groups, ccm)
    er_contig = recall_score(yt_c, yp_c, labels=['empty', 'occupied'], average=None)[0]
    er_spread = recall_score(pres_cal['yt'], pres_cal['yp'], labels=['empty', 'occupied'], average=None)[0]
    bal_contig = balanced_accuracy_score(yt_c, yp_c)
    fig, a = plt.subplots(figsize=(5.2, 3.6))
    xs = np.arange(2); w = 0.38
    a.bar(xs - w / 2, [er_contig, er_spread], w, label='Empty recall', color='#4C72B0')
    a.bar(xs + w / 2, [bal_contig, pres_cal['bal']], w, label='Balanced acc.', color='#C44E52')
    a.set_xticks(xs)
    a.set_xticklabels(['Short one-shot\nbaseline', 'Representative\nbaseline'])
    a.set_ylim(0, 1); a.set_ylabel('Score')
    a.set_title('Effect of the empty-room calibration window', fontsize=10)
    for i, v in enumerate([er_contig, er_spread]):
        a.text(i - w / 2, v + .01, f"{v:.2f}", ha='center', fontsize=8)
    for i, v in enumerate([bal_contig, pres_cal['bal']]):
        a.text(i + w / 2, v + .01, f"{v:.2f}", ha='center', fontsize=8)
    a.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig4_baseline_sensitivity.png'), dpi=120)
    plt.close(fig)

    # ---- dump metrics ----------------------------------------------------------
    def strip(g):
        return {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                for k, v in g.items() if k not in ('yt', 'yp')}
    M['presence'] = {'raw': strip(pres_raw), 'calibrated': strip(pres_cal)}
    M['activity'] = {'rssi': strip(act_rssi), 'raw': strip(act_raw),
                     'calibrated': strip(act_cal)}
    M['activity']['per_class_recall'] = dict(zip(labels, [float(r) for r in rec]))
    M['presence']['empty_recall_calibrated'] = float(er_spread)
    M['presence']['occ_recall_calibrated'] = float(
        recall_score(pres_cal['yt'], pres_cal['yp'], labels=['empty', 'occupied'], average=None)[1])
    M['baseline_sensitivity'] = {'contiguous': {'empty_recall': float(er_contig),
                                                'balanced': float(bal_contig)},
                                 'spread': {'empty_recall': float(er_spread),
                                            'balanced': float(pres_cal['bal'])}}
    with open(os.path.join(OUT, 'metrics.json'), 'w') as f:
        json.dump(M, f, indent=2)
    print('Wrote figures + metrics to', OUT)
    print(json.dumps({k: M[k] for k in ('n_sessions', 'n_windows', 'configs')}, indent=2))


if __name__ == '__main__':
    main()
