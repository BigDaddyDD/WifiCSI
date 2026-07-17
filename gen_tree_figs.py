#!/usr/bin/env python3
"""
"See inside the forest" visuals for the boss (requested: a visual of the tree /
features / connections the random forest actually uses).

Trained on the same main pooled dataset as gen_model_report.py (all strong-link
home_L + basement configs, per-config empty-baseline calibration).

Outputs to docs/model_report_assets/:
  fig10_feature_importance.png  top 15 real feature importances, full 300-tree
                                 forest (the actual production model)
  fig11_tree_diagram.png        the WHOLE first tree of the actual production
                                 forest (clf.estimators_[0]) — every one of its
                                 ~1,700 real nodes is drawn; nodes above
                                 LABEL_DEPTH show their real split/class text,
                                 deeper nodes are small colored dots (still the
                                 real node, just unlabeled) so the whole shape
                                 fits in one image without overlapping text.

Usage: python gen_tree_figs.py
"""
import os
import sys
from collections import Counter

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from sklearn.ensemble import RandomForestClassifier

import analyze_liv as al

sys.setrecursionlimit(10000)

CLASS_COLORS = {'empty': '#f2c295', 'stand': '#b5b0e8', 'sit': '#aee8e0',
                'walk': '#f2b8e0', 'run': '#d7f2b0'}

OUT = os.path.join('docs', 'model_report_assets')
os.makedirs(OUT, exist_ok=True)
LABELS = ['empty', 'stand', 'sit', 'walk', 'run']
rng = np.random.default_rng(0)


def feature_names(nsc):
    names = [f'rel. amplitude change (subcarrier {i})' for i in range(nsc)]
    names += [f'temporal std (subcarrier {i})' for i in range(nsc)]
    names += [f'motion-band fraction (subcarrier {i})' for i in range(nsc)]
    names += ['mean |rel. amplitude change|', 'max |rel. amplitude change|',
             'overall deviation from empty (L2 norm)', 'mean temporal std']
    return names


def short_feature_names(nsc):
    """Compact labels for the whole-tree diagram, where box width is tight."""
    names = [f'relΔ sc{i}' for i in range(nsc)]
    names += [f'tstd sc{i}' for i in range(nsc)]
    names += [f'motion sc{i}' for i in range(nsc)]
    names += ['mean|relΔ|', 'max|relΔ|', 'L2 dev.', 'mean tstd']
    return names


def build_row_rescaled_layout(children_left, children_right):
    """x-position every node so NO level can overlap regardless of how lopsided
    its subtrees are: get a topologically-correct ORDER per depth (via a plain
    mean-of-children layout, which never crosses siblings), then re-space each
    depth level evenly across a shared [0, 1] width. A level with 2 nodes gets
    huge gaps; a level with 200 gets tiny ones — every level fits the same
    canvas width regardless of node count, so text can be sized per-level."""
    n = len(children_left)
    depth = np.zeros(n, dtype=int)
    order_x = np.zeros(n, dtype=float)
    counter = [0]

    def layout(nid, d):
        depth[nid] = d
        cl, cr = children_left[nid], children_right[nid]
        if cl == -1:
            order_x[nid] = counter[0]; counter[0] += 1
            return order_x[nid]
        lx = layout(cl, d + 1)
        rx = layout(cr, d + 1)
        order_x[nid] = (lx + rx) / 2
        return order_x[nid]

    layout(0, 0)
    x = np.zeros(n, dtype=float)
    for d in range(depth.max() + 1):
        ids = np.where(depth == d)[0]
        ranked = ids[np.argsort(order_x[ids])]
        k = len(ranked)
        for rank, nid in enumerate(ranked):
            x[nid] = (rank + 0.5) / k
    return x, depth


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

    Xk, yk = X[keep], y[keep]
    names = feature_names(d['nsc'])
    print(f"training windows: {len(yk)}  features: {Xk.shape[1]}-D  "
        f"classes: {dict(Counter(yk))}")

    # ---------------- Fig 10: real feature importance, full production forest --
    clf = RandomForestClassifier(n_estimators=300, random_state=0, n_jobs=-1,
                                 class_weight='balanced')
    clf.fit(Xk, yk)
    imp = clf.feature_importances_
    order = np.argsort(imp)[::-1][:15]
    top_names = [names[i] for i in order]
    top_imp = imp[order]

    fig, ax = plt.subplots(figsize=(7.6, 5.4))
    yy = np.arange(len(top_names))
    ax.barh(yy, top_imp[::-1], color='#3a6ea5', edgecolor='black', linewidth=0.6)
    ax.set_yticks(yy); ax.set_yticklabels(top_names[::-1], fontsize=9)
    ax.set_xlabel('Feature importance (mean decrease in impurity)')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig10_feature_importance.png'), dpi=120)
    plt.close(fig)
    print('\nTop 5 features:')
    for n, v in zip(top_names[:5], top_imp[:5]):
        print(f'  {v:.4f}  {n}')

    # ---------------- Fig 11: the WHOLE first tree of the REAL forest ---------
    # clf.estimators_[0] is an actual member of the production 300-tree forest —
    # not a separately-trained stand-in. It is deep (see printed depth/nodes
    # below): every node is drawn (full topology, nothing truncated), but only
    # nodes at or above LABEL_DEPTH get real split-text boxes; deeper nodes are
    # small colored dots (still real nodes, just too many to caption). Row
    # spacing is rescaled per depth level (build_row_rescaled_layout) so a
    # crowded deep level never collides with its neighbors regardless of how
    # lopsided the real subtrees are.
    est = clf.estimators_[0]
    tr = est.tree_
    classes = clf.classes_
    n_nodes = tr.node_count
    print(f"\nfig11: production tree[0]  depth={est.get_depth()}  "
        f"leaves={est.get_n_leaves()}  nodes={n_nodes}")

    short_names = short_feature_names(d['nsc'])
    x, depth = build_row_rescaled_layout(tr.children_left, tr.children_right)
    max_depth = int(depth.max())
    LABEL_DEPTH = 5

    W = 46.0                              # canvas width, inches
    DY = 0.42                             # canvas height per depth level, inches
    H = (max_depth + 1) * DY
    fig, ax = plt.subplots(figsize=(W, H))

    def node_class(nid):
        return classes[tr.value[nid, 0].argmax()]

    # edges first (under the nodes)
    for nid in range(n_nodes):
        cl, cr = tr.children_left[nid], tr.children_right[nid]
        for ch in (cl, cr):
            if ch != -1:
                ax.plot([x[nid] * W, x[ch] * W], [-depth[nid] * DY, -depth[ch] * DY],
                       color='#999', lw=0.5, zorder=1)

    for nid in range(n_nodes):
        cls = node_class(nid)
        color = CLASS_COLORS.get(cls, '#dddddd')
        xi, yi = x[nid] * W, -depth[nid] * DY
        if depth[nid] <= LABEL_DEPTH:
            leaf = tr.children_left[nid] == -1
            pct = 100.0 * tr.n_node_samples[nid] / tr.n_node_samples[0]
            if leaf:
                text = f"{pct:.1f}%\n{cls}"
            else:
                fname = short_names[tr.feature[nid]]
                text = f"{fname} <= {tr.threshold[nid]:.1f}\n{pct:.1f}%  {cls}"
            box = FancyBboxPatch((xi - 0.85, yi - 0.11), 1.7, 0.22,
                                 boxstyle='round,pad=0.02,rounding_size=0.03',
                                 linewidth=0.5, edgecolor='black', facecolor=color,
                                 zorder=3)
            ax.add_patch(box)
            ax.text(xi, yi, text, ha='center', va='center', fontsize=6.5, zorder=4)
        else:
            ax.scatter([xi], [yi], s=4, color=color, edgecolor='none', zorder=2)

    ax.set_xlim(-1, W + 1); ax.set_ylim(-H - 0.3, 0.3)
    ax.axis('off')
    handles = [plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=c,
                         markersize=10, label=lab)
              for lab, c in CLASS_COLORS.items()]
    ax.legend(handles=handles, loc='upper right', fontsize=10, ncol=5,
             frameon=False)
    fig.savefig(os.path.join(OUT, 'fig11_tree_diagram.png'), dpi=130,
               bbox_inches='tight')
    plt.close(fig)
    print(f"  labeled nodes (depth<={LABEL_DEPTH}): {int((depth <= LABEL_DEPTH).sum())} "
        f"of {n_nodes}")

    print('\nWrote fig10_feature_importance.png + fig11_tree_diagram.png to', OUT)


if __name__ == '__main__':
    main()
