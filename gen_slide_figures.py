#!/usr/bin/env python3
"""Generate clean, slide-ready figures for the weekly PPT."""
import csv
import os

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT = 'slides_assets'
os.makedirs(OUT, exist_ok=True)
plt.rcParams.update({'font.size': 13, 'axes.titlesize': 15, 'figure.dpi': 130})


def load_amp(path, n=600):
    amps = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                v = [int(x) for x in row['csi'].split()]
            except (ValueError, KeyError):
                continue
            if not v:
                continue
            a = np.asarray(v, float)
            r, i = a[0::2], a[1::2]
            m = min(len(r), len(i))
            amps.append(np.sqrt(r[:m] ** 2 + i[:m] ** 2))
            if len(amps) >= n:
                break
    L = max(set(len(x) for x in amps), key=[len(x) for x in amps].count)
    return np.array([x for x in amps if len(x) == L])


e = load_amp('data/raw/empty_20260624_150511.csv')
s = load_amp('data/raw/still_20260624_150323.csv')
m = load_amp('data/raw/moving_20260624_150022.csv')
alld = np.concatenate([e, s, m])
vmin, vmax = np.percentile(alld, 3), np.percentile(alld, 97)


def spectro(ax, dat, title):
    im = ax.imshow(dat.T, aspect='auto', origin='lower', cmap='jet', vmin=vmin, vmax=vmax)
    ax.set_title(title); ax.set_xlabel('time (packets)'); ax.set_ylabel('subcarrier')
    return im


# ---- 1. two-panel: empty vs moving ----
fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
spectro(ax[0], e, 'EMPTY room')
im = spectro(ax[1], m, 'MOVING person')
fig.colorbar(im, ax=ax, label='amplitude', fraction=0.025)
fig.savefig(f'{OUT}/spectrogram_compare.png', bbox_inches='tight')
plt.close(fig)

# ---- 2. three-panel: empty / still / moving ----
fig, ax = plt.subplots(1, 3, figsize=(13.5, 4.0))
spectro(ax[0], e, 'EMPTY')
spectro(ax[1], s, 'STILL person (standing)')
im = spectro(ax[2], m, 'MOVING person')
fig.colorbar(im, ax=ax, label='amplitude', fraction=0.02)
fig.savefig(f'{OUT}/spectrogram3.png', bbox_inches='tight')
plt.close(fig)

# ---- 3. presence accuracy ----
fig, a = plt.subplots(figsize=(7, 4.3))
bars = a.bar(['Same setup', 'New, unseen\nplacement'], [81, 73],
             color=['#2c7fb8', '#41b6c4'], width=0.55)
a.axhline(50, color='gray', ls='--', lw=1)
a.text(1.45, 51.5, 'coin-flip (50%)', ha='right', color='gray', fontsize=10)
for b, v in zip(bars, [81, 73]):
    a.text(b.get_x() + b.get_width() / 2, v + 1.5, f'{v}%', ha='center',
           fontsize=15, fontweight='bold')
a.set_ylim(0, 100); a.set_ylabel('Presence accuracy')
a.set_title('Presence detection accuracy')
fig.text(0.5, -0.02, 'Rarely misses someone present (occupied recall 0.86-0.94)',
         ha='center', fontsize=11, style='italic')
fig.savefig(f'{OUT}/presence_bar.png', bbox_inches='tight')
plt.close(fig)

# ---- 4. coverage: motion recall vs distance ----
fig, a = plt.subplots(figsize=(7, 4.3))
bars = a.bar(['Near link', 'Mid', 'Far'], [26, 7, 0],
             color=['#31a354', '#fec44f', '#de2d26'], width=0.55)
for b, v in zip(bars, [26, 7, 0]):
    a.text(b.get_x() + b.get_width() / 2, v + 1.5, f'{v}%', ha='center',
           fontsize=15, fontweight='bold')
a.set_ylim(0, 100); a.set_ylabel('% of motion detected')
a.set_title('One link only senses motion near it')
fig.text(0.5, -0.02, 'A moving person becomes invisible far from the single link',
         ha='center', fontsize=11, style='italic')
fig.savefig(f'{OUT}/coverage_bar.png', bbox_inches='tight')
plt.close(fig)

# ---- 5. OFDM diagram ----
fig, ax = plt.subplots(1, 2, figsize=(12, 3.6))
xx = np.linspace(-3.5, 4.5, 1400)
for k in range(-1, 4):
    ax[0].plot(xx, np.sinc(xx - k), lw=1.5)
ax[0].axhline(0, color='k', lw=0.6)
ax[0].set_title('Subcarriers are orthogonal tones')
ax[0].set_xlabel('frequency  →'); ax[0].set_yticks([]); ax[0].set_xticks([])

sc = np.arange(-32, 32)
env = 6 + 2.5 * np.sin(sc / 7.0) + 1.3 * np.cos(sc / 3.0) + 0.6 * np.sin(sc / 1.5)
null = (np.abs(sc) >= 27) | (sc == 0)
env[null] = 0
ml, sl, bl = ax[1].stem(sc, env, basefmt=' ')
plt.setp(ml, color='#2c7fb8', markersize=3); plt.setp(sl, color='#2c7fb8')
ax[1].set_title('Each subcarrier measures the channel  →  CSI')
ax[1].set_xlabel('subcarrier index'); ax[1].set_ylabel('|channel|'); ax[1].set_yticks([])
fig.savefig(f'{OUT}/ofdm_diagram.png', bbox_inches='tight')
plt.close(fig)

print('Saved figures to', OUT)
for f in sorted(os.listdir(OUT)):
    print(' ', f)
