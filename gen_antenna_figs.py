#!/usr/bin/env python3
"""
Stock vs. Taoglas antenna comparison figures (boss request: does the trained
model plug-and-play onto a different antenna?).

Data: data/study/antenna_test/ (stock, blocks only) + data/study/basement_antenna_test/
(stock + taoglas, baseline + blocks), same room, 3 placements (p1/p2/p3).
p2/stock blocks was a dead recording (0 packets, RX stall) -> excluded; its
baseline was originally mislabeled p1 and has been corrected to p2 on disk.

Same pipeline as gen_model_report.py / model_comparison.py: amplitude features,
per-config empty-baseline calibration, RandomForest(300, balanced).

Outputs (plain matplotlib, no chart titles, style matches fig1-fig7):
  fig8_confusion_antenna.png   per-install 5-class confusion, stock vs taoglas
  fig9_antenna_transfer.png    same-antenna (per-install) vs cross-antenna bars

Usage: python gen_antenna_figs.py
"""
import glob
import json
import os
from collections import Counter

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix, recall_score)

import phase_a_analysis as pa
import csi_dataset as cd

OUT = os.path.join('docs', 'model_report_assets')
os.makedirs(OUT, exist_ok=True)
WIN, HOP = 200, 100
LABELS = ['empty', 'stand', 'sit', 'walk', 'run']
FOLDERS = ['data/study/antenna_test', 'data/study/basement_antenna_test']


def rf():
    return RandomForestClassifier(n_estimators=300, random_state=0,
                                  n_jobs=-1, class_weight='balanced')


def load_segments():
    segs = []
    for folder in FOLDERS:
        for sj in sorted(glob.glob(folder + '/*/session.json')):
            d = os.path.dirname(sj); m = json.load(open(sj))
            cfg = f"{m['placement']}/{m['node_orientation']}"
            for cf in sorted(glob.glob(os.path.join(d, 'seg*__*.csv'))):
                label = os.path.basename(cf).split('_')[1]
                if label not in LABELS:
                    continue
                segs.append({'csv': cf, 'label': label, 'config': cfg,
                            'ant': m['node_orientation'], 'plc': m['placement'],
                            'stype': m['session_type']})
    return segs


def build():
    segs = load_segments()
    amps = {}; total = None; count = 0
    for i, s in enumerate(segs):
        a, _ = pa.load_seg(s['csv']); amps[i] = a
        if a.size:
            total = a.sum(0) if total is None else total + a.sum(0)
            count += len(a)
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])

    MV, SV, MO = [], [], []
    y, cfg, ant, st = [], [], [], []
    for i, s in enumerate(segs):
        a = amps[i]
        if a.size == 0:
            continue
        A = a[:, mask]
        for lo, hi in cd.window_indices(len(A), WIN, HOP):
            W = A[lo:hi]
            MV.append(W.mean(0)); SV.append(W.std(0)); MO.append(pa.motion_frac(W))
            y.append(s['label']); cfg.append(s['config'])
            ant.append(s['ant']); st.append(s['stype'])
    return dict(MV=np.array(MV), SV=np.array(SV), MO=np.array(MO), y=np.array(y),
               cfg=np.array(cfg), ant=np.array(ant), st=np.array(st),
               nsc=int(mask.sum()))


def calib_mask(d):
    y, cfg, st = d['y'], d['cfg'], d['st']
    cal = np.zeros(len(y), bool)
    for c in set(cfg):
        m = cfg == c
        base = np.where(m & (y == 'empty') & (st == 'baseline'))[0]
        alle = np.where(m & (y == 'empty'))[0]
        if len(base):
            cal[base] = True
        elif len(alle):
            cal[alle[::2]] = True
    d['cal'] = cal
    return cal


def calibrated(d):
    MV, SV, MO = d['MV'], d['SV'], d['MO']
    cfg, cal = d['cfg'], d['cal']
    nsc = MV.shape[1]
    X = np.zeros((len(MV), 3 * nsc + 4))
    for c in set(cfg):
        m = cfg == c
        ref = np.where(m & cal)[0]
        if len(ref) < 3:
            ref = np.where(m)[0]
        base = MV[ref].mean(0)
        for i in np.where(m)[0]:
            rel = (MV[i] - base) / (base + 1e-6)
            summ = [np.abs(rel).mean(), np.abs(rel).max(),
                    np.linalg.norm(rel), SV[i].mean()]
            X[i] = np.concatenate([rel, SV[i], MO[i], summ])
        mu, sd = X[ref].mean(0), X[ref].std(0) + 1e-6
        X[m] = (X[m] - mu) / sd
    return X


def per_install_predictions(X, y, cfg, cfgs, sc):
    """Pooled 5-fold cross_val_predict per config, concatenated across cfgs."""
    yt, yp = [], []
    for c in cfgs:
        m = sc & (cfg == c)
        if m.sum() == 0 or len(set(y[m])) < 2:
            continue
        pred = cross_val_predict(rf(), X[m], y[m],
                                 cv=StratifiedKFold(5, shuffle=True, random_state=0))
        yt.extend(y[m]); yp.extend(pred)
    return np.asarray(yt), np.asarray(yp)


def main():
    d = build()
    sc = calib_mask(d); sc = ~sc
    X = calibrated(d)
    y, cfg, ant = d['y'], d['cfg'], d['ant']
    yb = np.where(y == 'empty', 'empty', 'occupied')

    print("=" * 66)
    print("configs & scoring windows per class:")
    for c in sorted(set(cfg)):
        m = (cfg == c) & sc
        print(f"  {c:14s} {dict(Counter(y[m]))}")

    stock_cfgs = sorted(set(cfg[ant == 'stock']))
    tao_cfgs = sorted(set(cfg[ant == 'taoglas']))
    stock_cfgs = [c for c in stock_cfgs if (sc & (cfg == c)).sum() > 0]
    tao_cfgs = [c for c in tao_cfgs if (sc & (cfg == c)).sum() > 0]
    print(f"stock configs (with activity data): {stock_cfgs}")
    print(f"taoglas configs (with activity data): {tao_cfgs}")

    # ---------------- Fig 8: per-install confusion, stock vs taoglas ----------
    yt_s, yp_s = per_install_predictions(X, y, cfg, stock_cfgs, sc)
    yt_t, yp_t = per_install_predictions(X, y, cfg, tao_cfgs, sc)
    labs = LABELS
    cm_s = confusion_matrix(yt_s, yp_s, labels=labs)
    cm_t = confusion_matrix(yt_t, yp_t, labels=labs)
    cmn_s = cm_s / cm_s.sum(1, keepdims=True) * 100
    cmn_t = cm_t / cm_t.sum(1, keepdims=True) * 100
    bal_s = balanced_accuracy_score(yt_s, yp_s)
    bal_t = balanced_accuracy_score(yt_t, yp_t)
    print(f"\nSTOCK per-install pooled:   n={len(yt_s)} balanced={bal_s:.3f}")
    print(f"TAOGLAS per-install pooled: n={len(yt_t)} balanced={bal_t:.3f}")

    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.6))
    for ax, cmn, name, n, bal in [(axes[0], cmn_s, 'Stock antenna', len(yt_s), bal_s),
                                  (axes[1], cmn_t, 'Taoglas antenna', len(yt_t), bal_t)]:
        im = ax.imshow(cmn, cmap='Blues', vmin=0, vmax=100)
        ax.set_xticks(range(len(labs))); ax.set_xticklabels(labs)
        ax.set_yticks(range(len(labs))); ax.set_yticklabels(labs)
        ax.set_xlabel('Predicted activity')
        ax.set_ylabel('Actual activity')
        ax.text(0.5, -0.22, f'{name}  (n={n}, balanced {bal:.2f})',
                transform=ax.transAxes, ha='center', fontsize=10)
        for i in range(len(labs)):
            for j in range(len(labs)):
                ax.text(j, i, f"{cmn[i, j]:.0f}", ha='center', va='center', fontsize=9,
                        color='white' if cmn[i, j] > 55 else 'black')
    fig.colorbar(im, ax=axes, fraction=0.023, pad=0.02).set_label('% of actual class')
    fig.savefig(os.path.join(OUT, 'fig8_confusion_antenna.png'), dpi=120,
               bbox_inches='tight')
    plt.close(fig)

    # ---------------- Fig 9: per-antenna (per-install) vs cross-antenna -------
    ptb_s = np.where(yt_s == 'empty', 'empty', 'occupied')
    ppb_s = np.where(yp_s == 'empty', 'empty', 'occupied')
    ptb_t = np.where(yt_t == 'empty', 'empty', 'occupied')
    ppb_t = np.where(yp_t == 'empty', 'empty', 'occupied')
    stock_presence = balanced_accuracy_score(ptb_s, ppb_s)
    tao_presence = balanced_accuracy_score(ptb_t, ppb_t)
    stock_5class, tao_5class = bal_s, bal_t

    # cross-antenna: train on ALL of one antenna's configs, test on the other's,
    # each config calibrated against its own empty baseline (as always); average
    # both directions (stock->taoglas, taoglas->stock).
    cross5, crossP = [], []
    for src, tgt in [('stock', 'taoglas'), ('taoglas', 'stock')]:
        tr = sc & (ant == src); te = sc & (ant == tgt)
        clf = rf(); clf.fit(X[tr], y[tr]); pred = clf.predict(X[te])
        cross5.append(balanced_accuracy_score(y[te], pred))
        clfb = rf(); clfb.fit(X[tr], yb[tr]); predb = clfb.predict(X[te])
        crossP.append(balanced_accuracy_score(yb[te], predb))
        print(f"train {src} -> test {tgt}: 5-class bal {cross5[-1]:.3f}  "
              f"presence bal {crossP[-1]:.3f}")
    cross_5class = float(np.mean(cross5))
    cross_presence = float(np.mean(crossP))

    print(f"\nESP32 stock per-install:  presence {stock_presence:.3f}  5-class {stock_5class:.3f}")
    print(f"Taoglas per-install:      presence {tao_presence:.3f}  5-class {tao_5class:.3f}")
    print(f"Cross-antenna (swap avg): presence {cross_presence:.3f}  5-class {cross_5class:.3f}")

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    groups = ['Presence\n(empty vs occupied)', 'Activity\n(5 classes)']
    xs = np.arange(2); w = 0.26
    bars = [
        ('ESP32 stock antenna (per-install)', [stock_presence, stock_5class], '#3a6ea5', -w),
        ('Taoglas antenna (per-install)', [tao_presence, tao_5class], '#3a7d34', 0),
        ('Cross-antenna (trained on the other, no retrain)',
         [cross_presence, cross_5class], '#b23b3b', w),
    ]
    for label, vals, color, off in bars:
        ax.bar(xs + off, vals, w, label=label, color=color, edgecolor='black', linewidth=0.6)
        for i, v in enumerate(vals):
            ax.text(xs[i] + off, v + 0.02, f"{v:.2f}", ha='center', fontsize=8)
    ax.set_xticks(xs); ax.set_xticklabels(groups)
    ax.set_ylim(0, 1.1); ax.set_ylabel('Balanced accuracy')
    ax.legend(fontsize=8, loc='upper right')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig9_antenna_transfer.png'), dpi=120)
    plt.close(fig)

    print('\nWrote fig8_confusion_antenna.png + fig9_antenna_transfer.png to', OUT)


if __name__ == '__main__':
    main()
