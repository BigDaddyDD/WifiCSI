#!/usr/bin/env python3
"""
Phase 1 on webcam-auto-labeled takes (data/av/<take>/).

Aligns the vision label timeline onto the CSI by timestamp, windows the CSI in
WALL-CLOCK time (robust to dropped packets / variable rate), labels each window
by the majority vision label, and evaluates.

Each take = one session (group). With >=2 takes it runs honest
Leave-One-Session-Out; with 1 take it can only show within-take numbers
(leaky CV + a chronological holdout), which are optimistic.

Usage:  python phase1_av.py
Requires: numpy, scikit-learn, matplotlib.
"""

import argparse
import csv
import glob
import json
import os
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

WIN_S, HOP_S = 2.0, 1.0       # window / hop in seconds (wall clock)
MIN_PKTS = 100                # skip windows with too few CSI packets
PURITY = 0.7                  # skip windows whose vision label isn't this pure


def load_csi(path):
    pc, amp = [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                vals = [int(x) for x in row['csi'].split()]
            except (ValueError, KeyError):
                continue
            if not vals:
                continue
            a = np.asarray(vals, float)
            real, imag = a[0::2], a[1::2]
            m = min(len(real), len(imag))
            amp.append(np.sqrt(real[:m] ** 2 + imag[:m] ** 2))
            pc.append(float(row['pc_time']))
    if not amp:
        return np.array([]), np.empty((0, 0))
    lens = [len(x) for x in amp]
    L = max(set(lens), key=lens.count)
    keep = [i for i, x in enumerate(amp) if len(x) == L]
    return np.array([pc[i] for i in keep]), np.array([amp[i] for i in keep])


def load_labels(path):
    t, lab = [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                t.append(float(row['pc_time']))
                lab.append(row['auto_label'])
            except (ValueError, KeyError):
                continue
    return np.array(t), np.array(lab)


def discover_takes(av_dir):
    out = []
    for d in sorted(glob.glob(os.path.join(av_dir, '*'))):
        if (os.path.isdir(d) and os.path.exists(os.path.join(d, 'csi.csv'))
                and os.path.exists(os.path.join(d, 'labels.csv'))):
            out.append(d)
    return out


def multiclass_loso(X, y, groups, labels, title):
    """Leave-One-Group-Out multiclass eval over arbitrary groups (takes or rooms)."""
    logo = LeaveOneGroupOut()
    yt, yp = [], []
    for tr, te in logo.split(X, y, groups):
        clf = RandomForestClassifier(n_estimators=300, random_state=0,
                                     n_jobs=-1, class_weight='balanced')
        clf.fit(X[tr], y[tr])
        yp.extend(clf.predict(X[te]))
        yt.extend(y[te])
    yt, yp = np.asarray(yt), np.asarray(yp)
    print(f"\n[{title}]")
    print(f"  Overall accuracy: {accuracy_score(yt, yp):.3f}")
    print(classification_report(yt, yp, labels=labels, zero_division=0))
    cm = confusion_matrix(yt, yp, labels=labels)
    print("Confusion (rows=true, cols=pred):")
    print("        " + "  ".join(f"{l:>7}" for l in labels))
    for i, l in enumerate(labels):
        print(f"{l:>7} " + "  ".join(f"{cm[i, j]:7d}" for j in range(len(labels))))
    return cm


def binary_loso(X, y, groups, pos_labels, pos_name, neg_name):
    """Leave-One-Take-Out for a binary regrouping of the 3 classes."""
    yb = np.where(np.isin(y, pos_labels), pos_name, neg_name)
    logo = LeaveOneGroupOut()
    yt, yp = [], []
    for tr, te in logo.split(X, yb, groups):
        clf = RandomForestClassifier(n_estimators=300, random_state=0,
                                     n_jobs=-1, class_weight='balanced')
        clf.fit(X[tr], yb[tr])
        yp.extend(clf.predict(X[te]))
        yt.extend(yb[te])
    yt, yp = np.asarray(yt), np.asarray(yp)
    bl = [neg_name, pos_name]
    print(f"\n[BINARY {pos_name} vs {neg_name}] Leave-One-Take-Out:")
    print(f"  Overall accuracy: {accuracy_score(yt, yp):.3f}")
    print(classification_report(yt, yp, labels=bl, zero_division=0))
    bcm = confusion_matrix(yt, yp, labels=bl)
    print("Confusion (rows=true, cols=pred):")
    print("          " + "  ".join(f"{l:>9}" for l in bl))
    for i, l in enumerate(bl):
        print(f"{l:>9} " + "  ".join(f"{bcm[i, j]:9d}" for j in range(len(bl))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--av', default='data/av')
    ap.add_argument('--win', type=float, default=WIN_S)
    ap.add_argument('--hop', type=float, default=HOP_S)
    args = ap.parse_args()

    takes = discover_takes(args.av)
    if not takes:
        raise SystemExit(f"No takes with csi.csv + labels.csv under {args.av}")

    # load everything, compute a global active-subcarrier mask
    loaded, total, count = [], None, 0
    for d in takes:
        pc, amp = load_csi(os.path.join(d, 'csi.csv'))
        lt, lab = load_labels(os.path.join(d, 'labels.csv'))
        if amp.size == 0 or lt.size == 0:
            print(f"  (skipping empty take {os.path.basename(d)})")
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
        dur = pc[-1] - pc[0]
        print(f"  {os.path.basename(d)} [{room}]: {len(amp)} pkts "
              f"(~{len(amp)/dur:.0f} Hz), {len(lt)} label frames, {dur:.0f}s")
    if not loaded:
        raise SystemExit("No usable takes.")
    mean_amp = total / count
    pos = mean_amp[mean_amp > 0]
    mask = mean_amp > 0.1 * np.median(pos)
    print(f"Active subcarriers: {int(mask.sum())} / {len(mask)}")

    # build windows (wall-clock) with majority vision label
    X, y, groups, rooms = [], [], [], []
    skipped = Counter()
    for take_id, room, pc, amp, lt, lab in loaded:
        amp_m = amp[:, mask]
        t = pc[0]
        while t + args.win <= pc[-1]:
            a, b = t, t + args.win
            t += args.hop
            csel = (pc >= a) & (pc < b)
            if csel.sum() < MIN_PKTS:
                skipped['few_pkts'] += 1
                continue
            lsel = (lt >= a) & (lt < b)
            if lsel.sum() == 0:
                skipped['no_label'] += 1
                continue
            labs = lab[lsel]
            top, ntop = Counter(labs).most_common(1)[0]
            if ntop / len(labs) < PURITY:
                skipped['impure'] += 1
                continue
            X.append(cd.window_features(amp_m[csel]))
            y.append(top)
            groups.append(take_id)
            rooms.append(room)
    X, y, groups, rooms = (np.asarray(X), np.asarray(y),
                           np.asarray(groups), np.asarray(rooms))
    print(f"\nWindows: {len(y)}  (win={args.win}s hop={args.hop}s)  skipped={dict(skipped)}")
    print("Per-class windows:", dict(Counter(y)))
    print("Per-take windows:", dict(Counter(groups)))
    labels = [c for c in cd.CLASSES if c in set(y)]

    # leaky (optimistic) reference
    skf = StratifiedKFold(5, shuffle=True, random_state=0)
    rf = RandomForestClassifier(n_estimators=300, random_state=0,
                                n_jobs=-1, class_weight='balanced')
    leaky = cross_val_score(rf, X, y, cv=skf).mean()
    print(f"\n[Leaky] random 5-fold CV accuracy: {leaky:.3f}  (optimistic)")

    n_takes = len(set(groups))
    if n_takes >= 2:
        logo = LeaveOneGroupOut()
        yt, yp = [], []
        for tr, te in logo.split(X, y, groups):
            clf = RandomForestClassifier(n_estimators=300, random_state=0,
                                         n_jobs=-1, class_weight='balanced')
            clf.fit(X[tr], y[tr])
            yp.extend(clf.predict(X[te]))
            yt.extend(y[te])
        yt, yp = np.asarray(yt), np.asarray(yp)
        print("\n[HONEST] Leave-One-Take-Out:")
        print(f"  Overall accuracy: {accuracy_score(yt, yp):.3f}")
        print(classification_report(yt, yp, labels=labels, zero_division=0))
        cm = confusion_matrix(yt, yp, labels=labels)
        title = 'Leave-One-Take-Out'
    else:
        # only one take -> chronological holdout (still same-session, optimistic)
        order = np.arange(len(y))
        split = int(0.7 * len(y))
        clf = RandomForestClassifier(n_estimators=300, random_state=0,
                                     n_jobs=-1, class_weight='balanced')
        clf.fit(X[order[:split]], y[order[:split]])
        yp = clf.predict(X[order[split:]])
        yt = y[order[split:]]
        print("\n[Within-take] chronological 70/30 holdout (only 1 take; optimistic):")
        print(f"  Overall accuracy: {accuracy_score(yt, yp):.3f}")
        print(classification_report(yt, yp, labels=labels, zero_division=0))
        cm = confusion_matrix(yt, yp, labels=labels)
        title = 'Chronological holdout (1 take)'
        print("\n  NOTE: record >=2 takes for an honest Leave-One-Take-Out number.")

    print("Confusion (rows=true, cols=pred):")
    print("        " + "  ".join(f"{l:>7}" for l in labels))
    for i, l in enumerate(labels):
        print(f"{l:>7} " + "  ".join(f"{cm[i, j]:7d}" for j in range(len(labels))))

    # ---- Binary framings (honest leave-one-TAKE-out) ----
    if n_takes >= 2:
        binary_loso(X, y, groups, ['still', 'moving'], 'occupied', 'empty')
        binary_loso(X, y, groups, ['moving'], 'moving', 'static')

    # ---- Cross-ENVIRONMENT: train on one room/orientation, test on another ----
    env_set = sorted(set(rooms))
    if len(env_set) >= 2:
        print(f"\n{'='*60}\nCROSS-ENVIRONMENT (leave-one-room-out) over {env_set}")
        print("  This is the real generalization test: did it learn presence,")
        print(f"  or just one geometry's fingerprint?\n{'='*60}")
        print("Windows per environment:", dict(Counter(rooms)))
        binary_loso(X, y, rooms, ['still', 'moving'], 'occupied', 'empty')
        multiclass_loso(X, y, rooms, labels, 'cross-room 3-class (leave-one-room-out)')

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
    ax.set_title(title); ax.set_xlabel('predicted'); ax.set_ylabel('true')
    ax = axes[1]
    top = np.argsort(imp)[::-1][:20][::-1]
    ax.barh(range(len(top)), imp[top])
    ax.set_yticks(range(len(top))); ax.set_yticklabels([names[i] for i in top], fontsize=7)
    ax.set_title('Top 20 feature importances')
    plt.tight_layout()
    out = 'data/phase1_av_results.png'
    plt.savefig(out, dpi=120)
    print(f"\nSaved {out}")
    plt.show()


if __name__ == '__main__':
    main()
