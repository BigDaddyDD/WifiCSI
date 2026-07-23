#!/usr/bin/env python3
"""
Wireless antenna comparison figures (this week's repeat of the antenna-
portability test, on the wireless dataset): both-Taoglas vs the mixed pair
(RX=Taoglas, TX=stock), across bedroom/Pbedroom/PBath/Pbasement.

Same methodology and visual style as gen_antenna_figs.py's fig8/fig9 (Blues
confusion matrices, per-install = 5-fold cross_val_predict within each
config, cross-antenna = train on all of one antenna's data, test on the
other's, no retraining):

  fig13_confusion_wireless_antenna.png  3-panel: taoglas per-install |
                                         mixed per-install | cross-antenna
                                         transfer (taoglas -> mixed) -- the
                                         third panel is the "does not
                                         transfer" picture directly.
  fig14_wireless_antenna_transfer.png   same-antenna (per-install) vs
                                         cross-antenna bars, presence + 5-class

Usage: python gen_wireless_antenna_figs.py
"""
import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import confusion_matrix, balanced_accuracy_score

import analyze_liv as al

OUT = os.path.join('docs', 'model_report_assets')
os.makedirs(OUT, exist_ok=True)
ROOMS = ['bedroom', 'Pbedroom', 'PBath', 'Pbasement']
LABELS = al.CLASSES


def rf():
    return RandomForestClassifier(n_estimators=400, random_state=0,
                                  n_jobs=-1, class_weight='balanced')


def per_install_predictions(X, y, cfg, cfgs, sc):
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
    d = al.build(ROOMS)
    y, cfg, st = d['y'], d['config'], d['stype']
    sc = ~d['calib']
    X = al.calibrated(d)
    yb = np.where(y == 'empty', 'empty', 'occupied')

    is_taoglas = np.array([c.endswith('/taoglas') or c.endswith('/wifi') for c in cfg])
    is_mixed = np.array([c.endswith('/rxtaoglas_txstock') for c in cfg])
    tao_cfgs = sorted(set(cfg[sc & is_taoglas]))
    mix_cfgs = sorted(set(cfg[sc & is_mixed]))
    print(f'taoglas configs: {len(tao_cfgs)}   mixed configs: {len(mix_cfgs)}')

    # ---------------- per-install (same antenna) ----------------
    yt_t, yp_t = per_install_predictions(X, y, cfg, tao_cfgs, sc)
    yt_m, yp_m = per_install_predictions(X, y, cfg, mix_cfgs, sc)
    bal_t = balanced_accuracy_score(yt_t, yp_t)
    bal_m = balanced_accuracy_score(yt_m, yp_m)
    print(f'taoglas per-install:  n={len(yt_t)} balanced={bal_t:.3f}')
    print(f'mixed per-install:    n={len(yt_m)} balanced={bal_m:.3f}')

    # ---------------- cross-antenna transfer (taoglas -> mixed) ----------------
    tr = sc & is_taoglas; te = (sc & is_mixed) & (st == 'blocks')
    clf = rf(); clf.fit(X[tr], y[tr])
    yp_cross = clf.predict(X[te]); yt_cross = y[te]
    bal_cross = balanced_accuracy_score(yt_cross, yp_cross)
    print(f'cross-antenna (taoglas->mixed): n={len(yt_cross)} balanced={bal_cross:.3f}')

    # ---------------- Fig 13: 3-panel confusion matrix ----------------
    cm_t = confusion_matrix(yt_t, yp_t, labels=LABELS)
    cm_m = confusion_matrix(yt_m, yp_m, labels=LABELS)
    cm_c = confusion_matrix(yt_cross, yp_cross, labels=LABELS)
    cmn_t = cm_t / cm_t.sum(1, keepdims=True) * 100
    cmn_m = cm_m / cm_m.sum(1, keepdims=True) * 100
    cmn_c = cm_c / cm_c.sum(1, keepdims=True) * 100

    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.8))
    panels = [(axes[0], cmn_t, 'Taoglas (per-install)', len(yt_t), bal_t),
              (axes[1], cmn_m, 'Mixed pair (per-install)', len(yt_m), bal_m),
              (axes[2], cmn_c, 'Taoglas -> Mixed (no retrain)', len(yt_cross), bal_cross)]
    for ax, cmn, name, n, bal in panels:
        im = ax.imshow(cmn, cmap='Blues', vmin=0, vmax=100)
        ax.set_xticks(range(len(LABELS))); ax.set_xticklabels(LABELS)
        ax.set_yticks(range(len(LABELS))); ax.set_yticklabels(LABELS)
        ax.set_xlabel('Predicted activity')
        ax.set_ylabel('Actual activity')
        ax.text(0.5, -0.22, f'{name}\n(n={n}, balanced {bal:.2f})',
                transform=ax.transAxes, ha='center', fontsize=10)
        for i in range(len(LABELS)):
            for j in range(len(LABELS)):
                ax.text(j, i, f'{cmn[i, j]:.0f}', ha='center', va='center', fontsize=9,
                        color='white' if cmn[i, j] > 55 else 'black')
    fig.colorbar(im, ax=axes, fraction=0.018, pad=0.02).set_label('% of actual class')
    fig.savefig(os.path.join(OUT, 'fig13_confusion_wireless_antenna.png'), dpi=120,
               bbox_inches='tight')
    plt.close(fig)
    print('wrote fig13_confusion_wireless_antenna.png')

    # ---------------- Fig 14: per-install vs cross-antenna bars ----------------
    def presence_bal(yt, yp):
        ytb = np.where(yt == 'empty', 'empty', 'occupied')
        ypb = np.where(yp == 'empty', 'empty', 'occupied')
        return balanced_accuracy_score(ytb, ypb)

    tao_presence = presence_bal(yt_t, yp_t)
    mix_presence = presence_bal(yt_m, yp_m)

    cross5, crossP = [], []
    for src_mask, tgt_mask in [(is_taoglas, is_mixed), (is_mixed, is_taoglas)]:
        tr = sc & src_mask; te = (sc & tgt_mask) & (st == 'blocks')
        clf = rf(); clf.fit(X[tr], y[tr]); pred = clf.predict(X[te])
        cross5.append(balanced_accuracy_score(y[te], pred))
        clfb = rf(); clfb.fit(X[tr], yb[tr]); predb = clfb.predict(X[te])
        crossP.append(balanced_accuracy_score(yb[te], predb))
    cross_5class = float(np.mean(cross5))
    cross_presence = float(np.mean(crossP))
    print(f'cross-antenna avg (both directions): presence={cross_presence:.3f} 5class={cross_5class:.3f}')

    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    groups = ['Presence\n(empty vs occupied)', 'Activity\n(5 classes)']
    xs = np.arange(2); w = 0.26
    bars = [
        ('Taoglas (per-install)', [tao_presence, bal_t], '#3a6ea5', -w),
        ('Mixed pair (per-install)', [mix_presence, bal_m], '#3a7d34', 0),
        ('Cross-antenna (trained on the other, no retrain)',
         [cross_presence, cross_5class], '#b23b3b', w),
    ]
    for label, vals, color, off in bars:
        ax.bar(xs + off, vals, w, label=label, color=color, edgecolor='black', linewidth=0.6)
        for i, v in enumerate(vals):
            ax.text(xs[i] + off, v + 0.02, f'{v:.2f}', ha='center', fontsize=8)
    ax.set_xticks(xs); ax.set_xticklabels(groups)
    ax.set_ylim(0, 1.1); ax.set_ylabel('Balanced accuracy')
    ax.legend(fontsize=8, loc='upper right')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig14_wireless_antenna_transfer.png'), dpi=120)
    plt.close(fig)
    print('wrote fig14_wireless_antenna_transfer.png')


if __name__ == '__main__':
    main()
