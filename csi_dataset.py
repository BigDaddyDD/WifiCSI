#!/usr/bin/env python3
"""
Shared CSI dataset utilities: discover labeled sessions, load amplitude,
window them, and extract per-window features.

Used by phase1_presence.py (and later by the webcam-labeled pipeline).
"""

import csv
import glob
import json
import os

import numpy as np

CLASSES = ['empty', 'still', 'moving']
FS = 100.0   # sample rate (Hz)


def canonicalize_label(s):
    """Map any label/filename string to one of CLASSES (or None)."""
    if not s:
        return None
    t = str(s).lower()
    if 'walk' in t or 'mov' in t or 'run' in t:
        return 'moving'
    if 'sit' in t or 'stand' in t or 'still' in t:
        return 'still'
    if 'empty' in t:
        return 'empty'
    return None


def discover_sessions(raw_dir='data/raw', verbose=True):
    """Return [{csv_path, session_id, label, meta}] for every clean CSV.

    A session is only used if it has a matching JSON sidecar whose `label`
    field is a canonical class. We deliberately do NOT guess from filenames:
    renamed files with stale sidecars are how walking data once got labeled
    "empty". Skipped files are reported rather than silently included.
    """
    sessions, skipped = [], []
    for csv_path in sorted(glob.glob(os.path.join(raw_dir, '*.csv'))):
        base = os.path.splitext(os.path.basename(csv_path))[0]
        if base.endswith('_analysis'):
            continue
        json_path = os.path.join(raw_dir, base + '.json')
        if not os.path.exists(json_path):
            skipped.append((base, 'no JSON sidecar'))
            continue
        try:
            with open(json_path) as f:
                meta = json.load(f)
        except Exception as ex:
            skipped.append((base, f'bad JSON ({ex})'))
            continue
        label = canonicalize_label(meta.get('label'))
        if label is None:
            skipped.append((base, f"label={meta.get('label')!r} not canonical"))
            continue
        sessions.append({'csv_path': csv_path, 'session_id': base,
                         'label': label, 'meta': meta})
    if verbose and skipped:
        print("Skipped (not used):")
        for b, why in skipped:
            print(f"  - {b}: {why}")
    return sessions


def load_amplitude(csv_path):
    """Return (amp [T x S], local_us [T]) using the most common vector length."""
    amps, local_us = [], []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            try:
                vals = [int(x) for x in row['csi'].split()]
            except (ValueError, KeyError):
                continue
            if not vals:
                continue
            a = np.asarray(vals, dtype=float)
            real, imag = a[0::2], a[1::2]
            m = min(len(real), len(imag))
            amps.append(np.sqrt(real[:m] ** 2 + imag[:m] ** 2))
            try:
                local_us.append(int(row['local_us']))
            except (ValueError, KeyError):
                local_us.append(-1)
    if not amps:
        return np.empty((0, 0)), np.array([])
    lens = [len(x) for x in amps]
    L = max(set(lens), key=lens.count)
    keep = [i for i, x in enumerate(amps) if len(x) == L]
    return (np.array([amps[i] for i in keep]),
            np.array([local_us[i] for i in keep]))


def global_active_mask(sessions, frac=0.1):
    """Subcarriers whose mean amplitude (over ALL sessions) exceeds frac*median.
    Drops null/guard subcarriers in a data-driven, ordering-agnostic way."""
    total, count = None, 0
    for s in sessions:
        A, _ = load_amplitude(s['csv_path'])
        if A.size == 0:
            continue
        total = A.sum(0) if total is None else total + A.sum(0)
        count += len(A)
    mean_amp = total / count
    pos = mean_amp[mean_amp > 0]
    thr = frac * np.median(pos) if pos.size else 0.0
    return mean_amp > thr


def window_indices(n, win, hop):
    out, s = [], 0
    while s + win <= n:
        out.append((s, s + win))
        s += hop
    return out


def window_features(W, fs=FS):
    """W: [T x S] amplitude window (active subcarriers). -> 1-D feature vector.

    Feature blocks (in order):
      std[S]     temporal std per subcarrier        (motion magnitude)
      shape[S]   mean amp per subcarrier, level-normalized (static fingerprint)
      motion[S]  fraction of spectral power in 0.5-5 Hz (motion/breathing band)
      summary[7] aggregate stats
    """
    std_sc = W.std(axis=0)
    mean_sc = W.mean(axis=0)
    denom = mean_sc.mean() if mean_sc.mean() != 0 else 1.0
    shape_sc = mean_sc / denom                       # absolute level removed

    Wd = W - mean_sc
    power = np.abs(np.fft.rfft(Wd, axis=0)) ** 2      # [F x S]
    freqs = np.fft.rfftfreq(W.shape[0], d=1.0 / fs)
    band = (freqs >= 0.5) & (freqs <= 5.0)
    motion_frac = power[band, :].sum(0) / (power.sum(0) + 1e-9)

    summary = np.array([
        std_sc.mean(), std_sc.max(), np.median(std_sc), std_sc.std(),
        float(np.mean(std_sc > std_sc.mean() + std_sc.std())),
        motion_frac.mean(), motion_frac.max(),
    ])
    return np.concatenate([std_sc, shape_sc, motion_frac, summary])


def feature_names(n_sc):
    names = ([f'std[{i}]' for i in range(n_sc)]
             + [f'shape[{i}]' for i in range(n_sc)]
             + [f'motion[{i}]' for i in range(n_sc)]
             + ['std_mean', 'std_max', 'std_med', 'std_std',
                'frac_high_var', 'motion_mean', 'motion_max'])
    return names
