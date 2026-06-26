#!/usr/bin/env python3
"""
Model statistics & clustering analysis (data-science documentation).

Produces, from the current dataset:
  - dataset + feature-set summary
  - per-class means of the interpretable summary features
  - Random Forest feature importances (top-k)
  - unsupervised clustering view: PCA + t-SNE scatter by class, and
    KMeans(k=3) agreement with the true labels (ARI / NMI / silhouette)

Outputs: printed report + slides_assets/{feature_importance,clusters}.png
         + docs/model_stats_report.md

NOTE: run on the CURRENT single-link dataset (preliminary). Re-run on the
rigorous multi-orientation dataset once collected.
"""
import os
from collections import Counter

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (adjusted_rand_score, normalized_mutual_info_score,
                             silhouette_score)

from presence_model import build

os.makedirs('slides_assets', exist_ok=True)
os.makedirs('docs', exist_ok=True)
CLASS_COLORS = {'empty': '#7570b3', 'still': '#d95f02', 'moving': '#1b9e77'}


def main():
    X_raw, yb, y3, takes, rooms, times = build()
    n, ncol = X_raw.shape
    S = (ncol - 12) // 3
    names = ([f'dev_sc{i}' for i in range(S)]
             + [f'std_sc{i}' for i in range(S)]
             + [f'motion_sc{i}' for i in range(S)]
             + ['dev_mean', 'dev_max', 'dev_l2', 'one_minus_cos',
                'std_mean', 'std_max', 'motion_mean', 'motion_max',
                'breath_fracmean', 'breath_fracmax', 'breath_peakmean', 'breath_peakmax'])
    summary_idx = list(range(3 * S, ncol))      # the 12 interpretable features

    # per-environment z-score vs that env's empty windows (the model's input)
    X = X_raw.astype(float).copy()
    for rm in set(rooms):
        m = rooms == rm
        emp = m & (y3 == 'empty')
        if emp.sum() < 5:
            continue
        mu, sd = X_raw[emp].mean(0), X_raw[emp].std(0) + 1e-6
        X[m] = (X_raw[m] - mu) / sd

    classes = [c for c in ['empty', 'still', 'moving'] if c in set(y3)]
    lines = []
    def P(s=''):
        print(s); lines.append(s)

    P('=' * 64)
    P('DATASET & FEATURE SET')
    P('=' * 64)
    P(f'Windows: {n}   per-class: {dict(Counter(y3))}')
    P(f'Takes: {len(set(takes))}   environments: {sorted(set(rooms))}')
    P(f'Features: {ncol}  ({S} active subcarriers x 3 [deviation, std, motion] '
      f'+ 8 summary + 4 low-freq)')

    # ---- per-class means of interpretable summary features ----
    P('\n' + '=' * 64)
    P('PER-CLASS MEANS  (interpretable summary features, pre-calibration)')
    P('=' * 64)
    header = 'feature           ' + ''.join(f'{c:>10}' for c in classes)
    P(header)
    for fi in summary_idx:
        row = f'{names[fi]:<18}'
        for c in classes:
            row += f'{X_raw[y3 == c, fi].mean():>10.3f}'
        P(row)

    # ---- RF feature importances ----
    rf = RandomForestClassifier(n_estimators=400, random_state=0,
                                n_jobs=-1, class_weight='balanced').fit(X, y3)
    imp = rf.feature_importances_
    order = np.argsort(imp)[::-1][:15]
    P('\n' + '=' * 64)
    P('TOP 15 FEATURE IMPORTANCES (Random Forest, 3-class)')
    P('=' * 64)
    for i in order:
        P(f'  {names[i]:<16} {imp[i]:.4f}')

    # ---- clustering / unsupervised separability ----
    Xs = StandardScaler().fit_transform(X)
    km = KMeans(n_clusters=len(classes), n_init=10, random_state=0).fit(Xs)
    ari = adjusted_rand_score(y3, km.labels_)
    nmi = normalized_mutual_info_score(y3, km.labels_)
    sil = silhouette_score(Xs, km.labels_)
    pca = PCA(n_components=2).fit(Xs)
    XP = pca.transform(Xs)
    P('\n' + '=' * 64)
    P('UNSUPERVISED CLUSTERING (do the classes form natural clusters?)')
    P('=' * 64)
    P(f'KMeans(k={len(classes)}) vs true labels:  ARI={ari:.3f}  NMI={nmi:.3f}  '
      f'silhouette={sil:.3f}')
    P(f'PCA explained variance (2 comps): {pca.explained_variance_ratio_[:2].sum()*100:.1f}%')
    P('(ARI/NMI near 0 = classes overlap in feature space; near 1 = cleanly separable)')

    # t-SNE (subsample if large)
    idx = np.arange(n)
    if n > 1500:
        rng = np.random.default_rng(0)
        idx = rng.choice(n, 1500, replace=False)
    XT = TSNE(n_components=2, perplexity=30, init='pca',
              random_state=0).fit_transform(Xs[idx])

    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    for c in classes:
        m = y3 == c
        ax[0].scatter(XP[m, 0], XP[m, 1], s=8, alpha=0.5,
                      color=CLASS_COLORS[c], label=c)
    ax[0].set_title('PCA of features (colored by true class)')
    ax[0].set_xlabel('PC1'); ax[0].set_ylabel('PC2'); ax[0].legend()
    yt = y3[idx]
    for c in classes:
        m = yt == c
        ax[1].scatter(XT[m, 0], XT[m, 1], s=8, alpha=0.5,
                      color=CLASS_COLORS[c], label=c)
    ax[1].set_title('t-SNE of features (colored by true class)')
    ax[1].set_xticks([]); ax[1].set_yticks([]); ax[1].legend()
    fig.savefig('slides_assets/clusters.png', dpi=130, bbox_inches='tight')
    plt.close(fig)

    fig, a = plt.subplots(figsize=(8, 5))
    a.barh(range(len(order)), imp[order][::-1], color='steelblue')
    a.set_yticks(range(len(order)))
    a.set_yticklabels([names[i] for i in order[::-1]], fontsize=9)
    a.set_title('Top 15 feature importances (Random Forest)')
    fig.savefig('slides_assets/feature_importance.png', dpi=130, bbox_inches='tight')
    plt.close(fig)

    P('\nSaved slides_assets/clusters.png, slides_assets/feature_importance.png')
    with open('docs/model_stats_report.md', 'w', encoding='utf-8') as f:
        f.write('# Model statistics & clustering report\n\n')
        f.write('_Preliminary — current single-link dataset. Regenerate on the '
                'rigorous multi-orientation dataset._\n\n```\n')
        f.write('\n'.join(lines))
        f.write('\n```\n\n')
        f.write('![clusters](../slides_assets/clusters.png)\n\n')
        f.write('![feature importance](../slides_assets/feature_importance.png)\n')
    print('Wrote docs/model_stats_report.md')


if __name__ == '__main__':
    main()
