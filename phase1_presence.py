#!/usr/bin/env python3
"""
Phase 1 - presence classifier (empty / still / moving).

Honest evaluation: trains/tests with Leave-One-Session-Out cross-validation
(every window of a held-out session is predicted by a model that never saw
that session), so accuracy isn't inflated by within-session leakage.

Also reports a variance-threshold baseline (the "motion score") for reference.

Usage:
    python phase1_presence.py
    python phase1_presence.py --win 200 --hop 100

Requires: numpy, scikit-learn, matplotlib.
"""

import argparse
from collections import Counter

import numpy as np
import matplotlib.pyplot as plt

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import (LeaveOneGroupOut, StratifiedKFold,
                                         cross_val_score)
    from sklearn.metrics import (classification_report, confusion_matrix,
                                 accuracy_score)
except ImportError:
    raise SystemExit("scikit-learn not installed. Run:  pip install scikit-learn")

import csi_dataset as cd


def build_dataset(sessions, mask, win, hop):
    X, y, groups, var_score = [], [], [], []
    for s in sessions:
        A, _ = cd.load_amplitude(s['csv_path'])
        if A.size == 0:
            continue
        Wm = A[:, mask]
        for a, b in cd.window_indices(len(Wm), win, hop):
            w = Wm[a:b]
            X.append(cd.window_features(w))
            y.append(s['label'])
            groups.append(s['session_id'])
            var_score.append(float(w.var(axis=0).mean()))   # baseline motion score
    return (np.asarray(X), np.asarray(y),
            np.asarray(groups), np.asarray(var_score))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw', default='data/raw')
    ap.add_argument('--win', type=int, default=200, help='window length (packets)')
    ap.add_argument('--hop', type=int, default=100, help='window hop (packets)')
    args = ap.parse_args()

    sessions = cd.discover_sessions(args.raw)
    if not sessions:
        raise SystemExit(f"No labelable sessions found in {args.raw}")

    print("Sessions discovered:")
    for s in sessions:
        print(f"  [{s['label']:6}] {s['session_id']}")
    inv = Counter(s['label'] for s in sessions)
    print("Inventory (sessions/class):", dict(inv))

    mask = cd.global_active_mask(sessions)
    print(f"Active subcarriers: {int(mask.sum())} / {len(mask)}")

    X, y, groups, var_score = build_dataset(sessions, mask, args.win, args.hop)
    print(f"Windows: {len(y)}  (win={args.win}, hop={args.hop})")
    print("Per-class windows:", dict(Counter(y)))

    if len(set(groups)) < 2:
        raise SystemExit("Need >= 2 sessions to evaluate by session. Collect more.")
    thin = {c: inv[c] for c in cd.CLASSES if inv.get(c, 0) < 2}
    if thin:
        print(f"  NOTE: classes with <2 sessions can't be held out cleanly: {thin}")

    # ---- Baseline: variance-threshold (moving vs rest) ----
    is_moving = (y == 'moving').astype(int)
    best_acc, best_thr = 0.0, 0.0
    cand = np.unique(var_score)
    step = max(1, len(cand) // 300)
    for t in cand[::step]:
        acc = ((var_score > t).astype(int) == is_moving).mean()
        if acc > best_acc:
            best_acc, best_thr = acc, t
    print(f"\n[Baseline] variance-threshold, moving-vs-rest best accuracy: "
          f"{best_acc:.3f} (thr={best_thr:.3f})")
    print("  (a motion detector; by design it cannot tell still from empty)")

    # ---- Sanity: LEAKY random-split CV (windows shuffled across sessions) ----
    # Inflated on purpose: adjacent windows of one session leak into both
    # train and test. High here + low in LOSO => the model memorizes a
    # session/position-specific fingerprint that does NOT generalize.
    skf = StratifiedKFold(5, shuffle=True, random_state=0)
    rf = RandomForestClassifier(n_estimators=300, random_state=0,
                                n_jobs=-1, class_weight='balanced')
    leaky = cross_val_score(rf, X, y, cv=skf).mean()
    print(f"\n[Sanity] LEAKY random 5-fold CV accuracy: {leaky:.3f}  "
          "(inflated - windows leak across folds)")

    # ---- RandomForest, Leave-One-Session-Out ----
    logo = LeaveOneGroupOut()
    yt, yp = [], []
    for tr, te in logo.split(X, y, groups):
        clf = RandomForestClassifier(n_estimators=300, random_state=0,
                                     n_jobs=-1, class_weight='balanced')
        clf.fit(X[tr], y[tr])
        yp.extend(clf.predict(X[te]))
        yt.extend(y[te])
    yt, yp = np.asarray(yt), np.asarray(yp)
    labels = [c for c in cd.CLASSES if c in set(y)]

    print("\n[RandomForest] Leave-One-Session-Out (honest cross-session):")
    print(f"  Overall accuracy: {accuracy_score(yt, yp):.3f}")
    print(classification_report(yt, yp, labels=labels, zero_division=0))
    cm = confusion_matrix(yt, yp, labels=labels)
    print("Confusion (rows=true, cols=pred):")
    print("        " + "  ".join(f"{l:>7}" for l in labels))
    for i, l in enumerate(labels):
        print(f"{l:>7} " + "  ".join(f"{cm[i, j]:7d}" for j in range(len(labels))))

    # ---- feature importance (full fit, for insight only) ----
    full = RandomForestClassifier(n_estimators=300, random_state=0,
                                  n_jobs=-1, class_weight='balanced').fit(X, y)
    imp = full.feature_importances_
    names = cd.feature_names(int(mask.sum()))

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    ax = axes[0]
    ax.imshow(cm, cmap='Blues')
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels)
    ax.set_yticks(range(len(labels))); ax.set_yticklabels(labels)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, cm[i, j], ha='center', va='center',
                    color='white' if cm[i, j] > cm.max() / 2 else 'black')
    ax.set_title('Confusion (LOSO)'); ax.set_xlabel('predicted'); ax.set_ylabel('true')

    ax = axes[1]
    top = np.argsort(imp)[::-1][:20][::-1]
    ax.barh(range(len(top)), imp[top])
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels([names[i] for i in top], fontsize=7)
    ax.set_title('Top 20 feature importances')
    plt.tight_layout()
    out = 'data/phase1_results.png'
    plt.savefig(out, dpi=120)
    print(f"\nSaved {out}")
    plt.show()


if __name__ == '__main__':
    main()
