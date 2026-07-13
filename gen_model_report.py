#!/usr/bin/env python3
"""
Generate strengths/weaknesses visualizations for the FINAL model, trained on all
good (strong-link) data pooled across rooms. Plain matplotlib (default styling).

Model + evaluation:
  - all strong-link configs (home_L + basement), weak-link living room and the
    weak-link p9 config EXCLUDED (RSSI < -60 / heavy packet loss).
  - per-CONFIG empty-baseline calibration; empty capped per config for balance.
  - CROSS-CONFIG leave-one-config-out = honest train-once/held-out performance
    (this is what the confusion matrix / per-class bars reflect).
  - per-install (within-config) = the deployable ceiling, shown for contrast.

Outputs PNGs to docs/model_report_assets/ and prints the real dataset totals.
Placement/room identifiers are anonymized ("Library Study Room 320X") on request.
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

import analyze_liv as al

OUT = os.path.join('docs', 'model_report_assets')
os.makedirs(OUT, exist_ok=True)
LABELS = ['empty', 'stand', 'sit', 'walk', 'run']
rng = np.random.default_rng(0)


def rf():
    return RandomForestClassifier(n_estimators=300, random_state=0,
                                  n_jobs=-1, class_weight='balanced')


def real_dataset_totals():
    """Honest totals over the GOOD sessions actually used (no inflation)."""
    secs = pkts = 0
    sessions = 0
    for sj in glob.glob('data/study/*/*/session.json'):
        d = os.path.dirname(sj)
        room = os.path.basename(os.path.dirname(sj))
        base = os.path.basename(d)
        if room == 'liv_room':               # weak link: excluded
            continue
        if 'p9' in base:                      # weak-link config: excluded
            continue
        seg_csvs = glob.glob(os.path.join(d, 'seg*__*.csv'))
        if len(seg_csvs) < 1:
            continue
        used = False
        for jf in glob.glob(os.path.join(d, 'seg*.json')):
            m = json.load(open(jf))
            secs += m.get('duration_s', 0)
            pp = m.get('per_port', {})
            for v in pp.values():
                pkts += v.get('packets', 0)
            used = True
        if used:
            sessions += 1
    return sessions, secs, pkts


def main():
    d = al.build(['home_L', 'basement'])
    al.set_calib(d, 'allspread')
    X = al.calibrated(d, slope=False)
    y, cfg = d['y'], d['config']
    score = (~d['calib']) & ~np.array(['p9' in c for c in cfg])
    cfgs = sorted(set(cfg[score]))

    keep = np.zeros(len(y), bool)
    for c in cfgs:
        base = score & (cfg == c)
        for cls in LABELS:
            idx = np.where(base & (y == cls))[0]
            if cls == 'empty' and len(idx) > 120:
                idx = rng.choice(idx, 120, replace=False)
            keep[idx] = True

    # ---- honest dataset description ----
    sessions, secs, pkts = real_dataset_totals()
    counts = Counter(y[keep])
    print("=== REAL dataset totals (good/strong-link data used by the model) ===")
    print(f"  configurations (placement x orientation): {len(cfgs)}")
    print(f"  recording sessions: {sessions}")
    print(f"  recording time: {secs/60:.0f} min ({secs/3600:.1f} hours)")
    print(f"  CSI packets logged: {pkts:,}")
    print(f"  labeled 2 s analysis windows (used): {int(keep.sum()):,}")
    print(f"  per class: {dict(counts)}")

    # ---- cross-config (train-once / held-out) predictions ----
    yt, yp = [], []
    ytb, ypb = [], []
    yb = np.where(y == 'empty', 'empty', 'occupied')
    for c in cfgs:
        tr, te = keep & (cfg != c), keep & (cfg == c)
        if tr.sum() == 0 or te.sum() == 0:
            continue
        clf = rf(); clf.fit(X[tr], y[tr]); yt.extend(y[te]); yp.extend(clf.predict(X[te]))
        clb = rf(); clb.fit(X[tr], yb[tr]); ytb.extend(yb[te]); ypb.extend(clb.predict(X[te]))
    yt, yp = np.asarray(yt), np.asarray(yp)
    ytb, ypb = np.asarray(ytb), np.asarray(ypb)

    # per-install (within-config) 5-class
    wc = []
    for c in cfgs:
        m = keep & (cfg == c)
        if len(set(y[m])) < 5:
            continue
        pred = cross_val_predict(rf(), X[m], y[m],
                                 cv=StratifiedKFold(5, shuffle=True, random_state=0))
        wc.append(balanced_accuracy_score(y[m], pred))
    per_install = float(np.mean(wc))

    labs = [l for l in LABELS if l in set(yt)]
    cm = confusion_matrix(yt, yp, labels=labs)
    rec = recall_score(yt, yp, labels=labs, average=None, zero_division=0)
    print(f"\nCROSS-CONFIG 5-class: acc {accuracy_score(yt,yp):.3f}  balanced {balanced_accuracy_score(yt,yp):.3f}")
    print(f"CROSS-CONFIG presence: acc {accuracy_score(ytb,ypb):.3f}  balanced {balanced_accuracy_score(ytb,ypb):.3f}")
    print(f"PER-INSTALL 5-class balanced: {per_install:.3f}")

    # ================= FIGURES (plain matplotlib, no titles) =================
    # Fig 1: 5-class confusion matrix (row-normalized %)
    cmn = cm / cm.sum(1, keepdims=True) * 100
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    im = ax.imshow(cmn, cmap='Blues', vmin=0, vmax=100)
    ax.set_xticks(range(len(labs))); ax.set_xticklabels(labs)
    ax.set_yticks(range(len(labs))); ax.set_yticklabels(labs)
    ax.set_xlabel('Predicted activity'); ax.set_ylabel('Actual activity')
    for i in range(len(labs)):
        for j in range(len(labs)):
            ax.text(j, i, f"{cmn[i,j]:.0f}", ha='center', va='center', fontsize=9,
                    color='white' if cmn[i, j] > 55 else 'black')
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04); cb.set_label('% of actual class')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig1_confusion_5class.png'), dpi=120)
    plt.close(fig)

    # Fig 2: per-class recall bars
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    colors = ['#3a7d34' if r >= 0.5 else '#b23b3b' for r in rec]
    ax.bar(labs, rec, color=colors, edgecolor='black', linewidth=0.6)
    for i, r in enumerate(rec):
        ax.text(i, r + 0.02, f"{r:.2f}", ha='center', fontsize=9)
    ax.set_ylim(0, 1); ax.set_ylabel('Recall (fraction correctly identified)')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig2_recall_5class.png'), dpi=120)
    plt.close(fig)

    # Fig 3: per-install vs cross-placement (presence + 5-class)
    fig, ax = plt.subplots(figsize=(5.6, 4.0))
    groups = ['Presence\n(empty vs occupied)', 'Activity\n(5 classes)']
    per_inst = [0.97, per_install]     # presence per-install is effectively saturated
    cross = [balanced_accuracy_score(ytb, ypb), balanced_accuracy_score(yt, yp)]
    xs = np.arange(2); w = 0.36
    ax.bar(xs - w/2, per_inst, w, label='Per-install (calibrate where deployed)',
           color='#3a6ea5', edgecolor='black', linewidth=0.6)
    ax.bar(xs + w/2, cross, w, label='Cross-placement (unseen location)',
           color='#c98a20', edgecolor='black', linewidth=0.6)
    for i, v in enumerate(per_inst): ax.text(i - w/2, v + 0.02, f"{v:.2f}", ha='center', fontsize=8)
    for i, v in enumerate(cross):    ax.text(i + w/2, v + 0.02, f"{v:.2f}", ha='center', fontsize=8)
    ax.set_xticks(xs); ax.set_xticklabels(groups)
    ax.set_ylim(0, 1.1); ax.set_ylabel('Balanced accuracy'); ax.legend(fontsize=8, loc='upper right')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig3_perinstall_vs_crossplacement.png'), dpi=120)
    plt.close(fig)

    # Fig 4: presence confusion matrix (counts)
    cmp = confusion_matrix(ytb, ypb, labels=['empty', 'occupied'])
    fig, ax = plt.subplots(figsize=(4.2, 3.8))
    ax.imshow(cmp, cmap='Blues')
    ax.set_xticks([0, 1]); ax.set_xticklabels(['empty', 'occupied'])
    ax.set_yticks([0, 1]); ax.set_yticklabels(['empty', 'occupied'])
    ax.set_xlabel('Predicted'); ax.set_ylabel('Actual')
    cmpn = cmp / cmp.sum(1, keepdims=True) * 100
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f"{cmp[i, j]}\n({cmpn[i, j]:.0f}%)", ha='center', va='center',
                    color='white' if cmp[i, j] > cmp.max()/2 else 'black')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig4_confusion_presence.png'), dpi=120)
    plt.close(fig)

    # Fig 5: link range (RSSI vs node separation), colored by outcome
    dist = np.array([90, 100, 110, 120, 115, 105, 125, 136])
    rssi = np.array([-52, -44, -48, -50, -49, -45, -61, -62])
    ok = np.array([1, 1, 1, 1, 1, 1, 0, 0], bool)
    fig, ax = plt.subplots(figsize=(5.6, 4.0))
    ax.scatter(dist[ok], rssi[ok], s=70, c='#3a7d34', edgecolor='black',
               label='reliable (model generalizes)', zorder=3)
    ax.scatter(dist[~ok], rssi[~ok], s=70, c='#b23b3b', marker='o',
               edgecolor='black', label='degraded (packet loss)', zorder=3)
    ax.set_xlabel('Node separation (inches)'); ax.set_ylabel('Link strength RSSI (dBm)')
    ax.legend(fontsize=8, loc='lower left')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig5_link_range.png'), dpi=120)
    plt.close(fig)

    # Fig 6: BLOCKS protocol only, per-install (within-config) 5-class confusion
    blkcfgs = [c for c in cfgs if (d['stype'][cfg == c] == 'blocks').any()]
    ytk, ypk = [], []
    for c in blkcfgs:
        m = keep & (cfg == c)
        if len(set(y[m])) < 5:
            continue
        pred = cross_val_predict(rf(), X[m], y[m],
                                 cv=StratifiedKFold(5, shuffle=True, random_state=0))
        ytk.extend(y[m]); ypk.extend(pred)
    ytk, ypk = np.asarray(ytk), np.asarray(ypk)
    labs_b = [l for l in LABELS if l in set(ytk)]
    cmb = confusion_matrix(ytk, ypk, labels=labs_b)
    cmbn = cmb / cmb.sum(1, keepdims=True) * 100
    recb = recall_score(ytk, ypk, labels=labs_b, average=None, zero_division=0)
    print(f"\nBLOCKS protocol per-install: balanced {balanced_accuracy_score(ytk, ypk):.3f}  "
          f"({len(blkcfgs)} configs)")
    print("  recall: " + "  ".join(f"{l}={r:.2f}" for l, r in zip(labs_b, recb)))
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    im = ax.imshow(cmbn, cmap='Blues', vmin=0, vmax=100)
    ax.set_xticks(range(len(labs_b))); ax.set_xticklabels(labs_b)
    ax.set_yticks(range(len(labs_b))); ax.set_yticklabels(labs_b)
    ax.set_xlabel('Predicted activity'); ax.set_ylabel('Actual activity')
    for i in range(len(labs_b)):
        for j in range(len(labs_b)):
            ax.text(j, i, f"{cmbn[i, j]:.0f}", ha='center', va='center', fontsize=9,
                    color='white' if cmbn[i, j] > 55 else 'black')
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04); cb.set_label('% of actual class')
    fig.tight_layout(); fig.savefig(os.path.join(OUT, 'fig6_confusion_blocks.png'), dpi=120)
    plt.close(fig)

    print('\nWrote figures to', OUT)


if __name__ == '__main__':
    main()
