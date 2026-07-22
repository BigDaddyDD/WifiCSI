#!/usr/bin/env python3
"""
Train the production 5-class model on ALL good (strong-link) pooled data and
persist it (joblib) so the live wireless monitor doesn't retrain on launch.

This is the SAME recipe as gen_model_report.py / gen_tree_figs.py (per-config
empty-baseline calibration, empty capped per config, RandomForest(300,
balanced)) except it trains on the FULL dataset (no held-out fold) since this
is the model we actually want running live, not an evaluation split.

IMPORTANT: this does NOT bake in an empty-room baseline. Per the project's
core finding, a raw/zero-shot model does not generalize across placements --
only PER-INSTALL calibration does. So the live monitor still captures a fresh
~60-90s empty baseline at wherever it's deployed (see live_monitor_gui.py);
what's persisted here is only the trained classifier + the active-subcarrier
mask, which are hardware/pipeline properties, not location properties.

Writes: model_store/live_model.joblib
  {clf, nsc, classes, win, hop}

Usage: python train_live_model.py
"""
import os
from collections import Counter

import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier

import analyze_liv as al

OUT_DIR = 'model_store'
OUT_PATH = os.path.join(OUT_DIR, 'live_model.joblib')
LABELS = ['empty', 'stand', 'sit', 'walk', 'run']
rng = np.random.default_rng(0)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
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

    Xk, yk = X[keep], y[keep]
    print(f"training windows: {len(yk)}  features: {Xk.shape[1]}-D  "
        f"classes: {dict(Counter(yk))}")

    clf = RandomForestClassifier(n_estimators=300, random_state=0, n_jobs=-1,
                                 class_weight='balanced')
    clf.fit(Xk, yk)
    print("trained on ALL pooled configs (no held-out fold -- this is the "
        "deployed model, evaluation numbers come from gen_model_report.py)")

    joblib.dump({'clf': clf, 'nsc': int(d['nsc']), 'mask': d['mask'],
                'classes': list(clf.classes_), 'win': al.WIN, 'hop': al.HOP},
               OUT_PATH)
    print('wrote', OUT_PATH)


if __name__ == '__main__':
    main()
