#!/usr/bin/env python3
"""
Model-family comparison for the CSI occupancy/activity task (boss request:
"how would various models perform — CNNs, deep learning, etc.").

RIGOR: everything except the classifier is held FIXED so any difference is the
model, not the setup. Same data, same per-config empty-baseline calibration,
same leave-one-configuration-out (train-once / cross-placement) evaluation, and
the same per-install (within-config 5-fold) ceiling as gen_model_report.py.

Two input representations are compared honestly:
  A. Hand-crafted 160-D window features (the current pipeline) fed to classical
     models: RandomForest (current baseline), ExtraTrees, gradient boosting,
     logistic regression, RBF-SVM, k-NN, naive Bayes, and an MLP neural net.
  B. RAW calibrated 2 s windows (52 subcarriers x 200 samples) fed to a 1-D CNN
     and a GRU — end-to-end deep learning that learns its own features.

Reports, per model: cross-placement PRESENCE balanced acc, cross-placement
5-class balanced acc / accuracy, and (classical) per-install 5-class balanced.
Writes docs/model_report_assets/fig7_model_comparison.png and
docs/model_report_assets/model_comparison.json.

Usage:  python model_comparison.py
"""
import json
import os
import sys
import time
from collections import Counter

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from sklearn.ensemble import (RandomForestClassifier, ExtraTreesClassifier,
                              HistGradientBoostingClassifier)
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             recall_score)

import analyze_liv as al
import phase_a_analysis as pa
import csi_dataset as cd

OUT = os.path.join('docs', 'model_report_assets')
os.makedirs(OUT, exist_ok=True)
RESULTS_JSON = os.path.join(OUT, 'model_comparison.json')
LABELS = ['empty', 'stand', 'sit', 'walk', 'run']
WIN, HOP = al.WIN, al.HOP
rng = np.random.default_rng(0)


def log(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------------------
# Data prep: identical to gen_model_report.py, but ALSO capture the raw window
# tensor (aligned row-for-row) so the CNN/GRU train on the same windows.
# ----------------------------------------------------------------------------
def build_with_raw(rooms):
    """Mirror analyze_liv.build() exactly, additionally returning raw windows R
    aligned with d's rows (same segment order, same subcarrier mask)."""
    segs = []
    for r in rooms:
        segs += al.load_segments(r)
    total, count = None, 0
    amps = {}
    for i, s in enumerate(segs):
        a, _ = pa.load_seg(s['csv'])
        amps[i] = a
        if a.size:
            total = a.sum(0) if total is None else total + a.sum(0)
            count += len(a)
    mask = (total / count) > 0.1 * np.median((total / count)[(total / count) > 0])

    MV, SV, MO, SL, R = [], [], [], [], []
    y, direction, config, room, stype = [], [], [], [], []
    for i, s in enumerate(segs):
        a = amps[i]
        if a.size == 0:
            continue
        A = a[:, mask]
        for lo, hi in cd.window_indices(len(A), WIN, HOP):
            W = A[lo:hi]
            MV.append(W.mean(0)); SV.append(W.std(0))
            MO.append(pa.motion_frac(W)); SL.append(al.slope_vec(W))
            R.append(W.astype(np.float32))
            y.append(s['label']); direction.append(s['direction'])
            config.append(s['config']); room.append(s['room'])
            stype.append(s['stype'])
    d = dict(MV=np.asarray(MV), SV=np.asarray(SV), MO=np.asarray(MO),
             SL=np.asarray(SL), y=np.asarray(y), direction=np.asarray(direction),
             config=np.asarray(config), room=np.asarray(room),
             stype=np.asarray(stype), nsc=int(mask.sum()))
    al.set_calib(d, 'auto')
    R = np.asarray(R, dtype=np.float32)            # [N, WIN, nsc]
    return d, R


def calibrated_raw(d, R):
    """Per-config calibrated raw windows for the deep models, matching the
    feature pipeline: rel = (W - empty_base)/empty_base, then per-config
    per-subcarrier z-score using the held-out calibration (empty) windows.
    Returns Rc [N, nsc, WIN] (channels=subcarriers) as float32."""
    cfg, cal = d['config'], d['calib']
    N, _, nsc = R.shape
    Rc = np.zeros((N, nsc, WIN), dtype=np.float32)
    for c in set(cfg):
        m = np.where(cfg == c)[0]
        ref = m[cal[m]]
        if len(ref) < 3:
            ref = m
        base = R[ref].mean(axis=(0, 1))                     # [nsc] empty mean
        rel_ref = (R[ref] - base) / (base + 1e-6)           # [r, WIN, nsc]
        mu = rel_ref.mean(axis=(0, 1)); sd = rel_ref.std(axis=(0, 1)) + 1e-6
        for i in m:
            rel = (R[i] - base) / (base + 1e-6)
            Rc[i] = ((rel - mu) / sd).T.astype(np.float32)  # -> [nsc, WIN]
    return Rc


# ----------------------------------------------------------------------------
# Deep models (PyTorch, CPU). Trained inside each LOSO fold.
# ----------------------------------------------------------------------------
import torch
import torch.nn as nn

torch.manual_seed(0)
np.random.seed(0)


class CNN1D(nn.Module):
    def __init__(self, n_sc, n_cls):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(n_sc, 64, 7, padding=3), nn.BatchNorm1d(64), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(64, 64, 5, padding=2), nn.BatchNorm1d(64), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(64, 64, 3, padding=1), nn.BatchNorm1d(64), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1))
        self.head = nn.Sequential(nn.Flatten(), nn.Dropout(0.3),
                                  nn.Linear(64, n_cls))

    def forward(self, x):
        return self.head(self.net(x))


class GRUNet(nn.Module):
    """Unidirectional GRU; input time axis is strided x2 (200->100 steps) to keep
    CPU training tractable. Enough resolution for 0.5-5 Hz motion at 100 Hz."""
    def __init__(self, n_sc, n_cls):
        super().__init__()
        self.gru = nn.GRU(n_sc, 64, batch_first=True)
        self.head = nn.Sequential(nn.Dropout(0.3), nn.Linear(64, n_cls))

    def forward(self, x):                      # x: [B, nsc, WIN]
        o, _ = self.gru(x[:, :, ::2].transpose(1, 2))   # -> [B, WIN/2, 64]
        return self.head(o.mean(1))


def train_torch(make_model, Rtr, ytr_idx, Rte, n_cls, classes, epochs=25):
    """Train a torch model with class-weighted CE; return predicted class idx."""
    torch.manual_seed(0)
    cnt = np.bincount(ytr_idx, minlength=n_cls).astype(float)
    w = torch.tensor((cnt.sum() / (n_cls * np.maximum(cnt, 1))), dtype=torch.float32)
    model = make_model()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss(weight=w)
    Xtr = torch.tensor(Rtr); Ytr = torch.tensor(ytr_idx)
    ds = torch.utils.data.TensorDataset(Xtr, Ytr)
    g = torch.Generator(); g.manual_seed(0)
    dl = torch.utils.data.DataLoader(ds, batch_size=64, shuffle=True, generator=g)
    model.train()
    for _ in range(epochs):
        for xb, yb in dl:
            opt.zero_grad(); loss = lossf(model(xb), yb); loss.backward(); opt.step()
    model.eval()
    with torch.no_grad():
        pred = model(torch.tensor(Rte)).argmax(1).numpy()
    return np.array([classes[p] for p in pred])


# ----------------------------------------------------------------------------
# Classical model zoo (on the 160-D hand-crafted features).
# ----------------------------------------------------------------------------
def classical_models():
    return {
        'RandomForest (baseline)': RandomForestClassifier(
            n_estimators=300, random_state=0, n_jobs=-1, class_weight='balanced'),
        'ExtraTrees': ExtraTreesClassifier(
            n_estimators=300, random_state=0, n_jobs=-1, class_weight='balanced'),
        'GradientBoosting': HistGradientBoostingClassifier(
            random_state=0, class_weight='balanced', max_iter=100,
            early_stopping=True, n_iter_no_change=8),
        'LogisticRegression': make_pipeline(StandardScaler(), LogisticRegression(
            max_iter=2000, class_weight='balanced', C=1.0)),
        'SVM (RBF)': make_pipeline(StandardScaler(), SVC(
            kernel='rbf', class_weight='balanced', C=4.0, gamma='scale')),
        'k-NN (k=15)': make_pipeline(StandardScaler(), KNeighborsClassifier(
            n_neighbors=15)),
        'NaiveBayes': GaussianNB(),
        'MLP neural net': make_pipeline(StandardScaler(), MLPClassifier(
            hidden_layer_sizes=(128, 64), max_iter=600, random_state=0,
            early_stopping=True)),
    }


def loco_predict_classical(model, X, y, cfg, keep, cfgs):
    yt, yp = [], []
    for c in cfgs:
        tr, te = keep & (cfg != c), keep & (cfg == c)
        if tr.sum() == 0 or te.sum() == 0:
            continue
        from sklearn.base import clone
        m = clone(model); m.fit(X[tr], y[tr])
        yt.extend(y[te]); yp.extend(m.predict(X[te]))
    return np.asarray(yt), np.asarray(yp)


def per_install_classical(model, X, y, cfg, keep, cfgs):
    from sklearn.base import clone
    accs = []
    for c in cfgs:
        m = keep & (cfg == c)
        if len(set(y[m])) < 5:
            continue
        pred = cross_val_predict(clone(model), X[m], y[m],
                                 cv=StratifiedKFold(5, shuffle=True, random_state=0))
        accs.append(balanced_accuracy_score(y[m], pred))
    return float(np.mean(accs)) if accs else float('nan')


def save_results(results):
    json.dump(results, open(RESULTS_JSON, 'w'), indent=2)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'all'   # classical | deep | all | figure
    t0 = time.time()
    results = {}
    if mode in ('deep', 'figure') and os.path.exists(RESULTS_JSON):
        results = json.load(open(RESULTS_JSON))           # extend prior classical run

    d, R = build_with_raw(['home_L', 'basement'])
    al.set_calib(d, 'allspread')
    X = al.calibrated(d, slope=False)
    y, cfg = d['y'], d['config']
    yb = np.where(y == 'empty', 'empty', 'occupied')

    # exact keep/cfgs from gen_model_report (weak-link p9 excluded, empty capped)
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

    log("=" * 74)
    log("MODEL COMPARISON — same data, same calibration, same leave-one-config-out")
    log(f"configs: {len(cfgs)} | windows used: {int(keep.sum())} | "
        f"features: {X.shape[1]}-D | raw window: {d['nsc']}x{WIN} | data build {time.time()-t0:.0f}s")
    log(f"class counts (used): {dict(Counter(y[keep]))}")
    log("=" * 74)

    def record(name, kind, yt5, yp5, pi):
        # Presence is derived from the 5-class predictions (non-empty -> occupied),
        # uniformly for every model, so the presence column compares like-for-like.
        pt = np.where(yt5 == 'empty', 'empty', 'occupied')
        pp = np.where(yp5 == 'empty', 'empty', 'occupied')
        labs = [l for l in LABELS if l in set(yt5)]
        results[name] = dict(
            kind=kind,
            presence_bal=float(balanced_accuracy_score(pt, pp)),
            act5_bal=float(balanced_accuracy_score(yt5, yp5)),
            act5_acc=float(accuracy_score(yt5, yp5)),
            per_install_bal=float(pi),
            recall={l: float(r) for l, r in zip(labs, recall_score(
                yt5, yp5, labels=labs, average=None, zero_division=0))})
        save_results(results)
        pis = f"{pi:.3f}" if pi == pi else "  n/a"
        log(f"{name:24s} presence {results[name]['presence_bal']:.3f} | "
            f"5-class bal {results[name]['act5_bal']:.3f} acc {results[name]['act5_acc']:.3f} | "
            f"per-install {pis}")

    # ---------- classical models on hand-crafted features ----------
    if mode in ('classical', 'all'):
        for name, model in classical_models().items():
            tt = time.time()
            yt5, yp5 = loco_predict_classical(model, X, y, cfg, keep, cfgs)
            pi = per_install_classical(model, X, y, cfg, keep, cfgs)
            record(name, 'features', yt5, yp5, pi)
            log(f"    ({time.time()-tt:.0f}s)")

    # ---------- deep models on raw calibrated windows ----------
    if mode in ('deep', 'all'):
        Rc = calibrated_raw(d, R)
        cls_idx = {c: i for i, c in enumerate(LABELS)}
        yidx = np.array([cls_idx[c] for c in y])
        deep = [('1D-CNN (raw)', lambda n: CNN1D(d['nsc'], n), 18),
                ('GRU (raw)', lambda n: GRUNet(d['nsc'], n), 12)]
        for name, mk, ep in deep:
            tt = time.time()
            yt5, yp5 = [], []
            for k, c in enumerate(cfgs):
                tr, te = keep & (cfg != c), keep & (cfg == c)
                if tr.sum() == 0 or te.sum() == 0:
                    continue
                yt5.extend(y[te])
                yp5.extend(train_torch(lambda: mk(5), Rc[tr], yidx[tr], Rc[te], 5, LABELS, ep))
                log(f"    {name}: fold {k+1}/{len(cfgs)}  ({time.time()-tt:.0f}s)")
            record(name, 'raw-deep', np.asarray(yt5), np.asarray(yp5), float('nan'))

    # ---------------- figure: grouped bars ----------------
    names = list(results.keys())
    pres = [results[n]['presence_bal'] for n in names]
    act = [results[n]['act5_bal'] for n in names]
    order = np.argsort(act)[::-1]
    names = [names[i] for i in order]; pres = [pres[i] for i in order]; act = [act[i] for i in order]
    yy = np.arange(len(names)); h = 0.38
    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    ax.barh(yy + h/2, pres, h, label='Presence (empty vs occupied)',
            color='#3a6ea5', edgecolor='black', linewidth=0.5)
    ax.barh(yy - h/2, act, h, label='Activity (5 classes)',
            color='#c98a20', edgecolor='black', linewidth=0.5)
    for i, v in enumerate(pres): ax.text(v + 0.005, yy[i] + h/2, f"{v:.2f}", va='center', fontsize=8)
    for i, v in enumerate(act):  ax.text(v + 0.005, yy[i] - h/2, f"{v:.2f}", va='center', fontsize=8)
    ax.set_yticks(yy); ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.0); ax.set_xlabel('Balanced accuracy (cross-placement, held-out)')
    ax.legend(fontsize=8, loc='lower right')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig7_model_comparison.png'), dpi=120)
    plt.close(fig)

    print(f"\nWrote fig7_model_comparison.png + model_comparison.json  "
          f"(total {time.time()-t0:.0f}s)")


if __name__ == '__main__':
    main()
