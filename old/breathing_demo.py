#!/usr/bin/env python3
"""Can we SEE breathing? Time-series + frequency spectrum of one subcarrier,
still (seated person) vs empty. Breathing should appear as a 0.15-0.5 Hz peak
in the frequency view (not in the raw heatmap)."""
import csv
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def load(path):
    pc, amp = [], []
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
            amp.append(np.sqrt(r[:m] ** 2 + i[:m] ** 2))
            pc.append(float(row['pc_time']))
    L = max(set(len(x) for x in amp), key=[len(x) for x in amp].count)
    keep = [j for j, x in enumerate(amp) if len(x) == L]
    return np.array([pc[j] for j in keep]), np.array([amp[j] for j in keep])


def detrend(x, fs):
    k = max(1, int(fs * 6))                     # 6 s moving avg removes slow drift
    ma = np.convolve(x, np.ones(k) / k, mode='same')
    return x - ma


def band_power(sig, fs, lo, hi):
    f = np.fft.rfftfreq(len(sig), 1 / fs)
    P = np.abs(np.fft.rfft(sig)) ** 2
    m = (f >= lo) & (f <= hi)
    return f, P, m


still_pc, still = load('data/raw/still_20260624_150323.csv')
emp_pc, emp = load('data/raw/empty_20260624_150511.csv')
fs_s = len(still_pc) / (still_pc[-1] - still_pc[0])
fs_e = len(emp_pc) / (emp_pc[-1] - emp_pc[0])

# pick the subcarrier with the most 0.15-0.5 Hz power in the STILL recording
best, best_p = 0, -1
for sc in range(still.shape[1]):
    x = detrend(still[:, sc], fs_s)
    f, P, m = band_power(x, fs_s, 0.15, 0.5)
    if m.any() and P[m].sum() > best_p:
        best_p, best = P[m].sum(), sc

xs = detrend(still[:, best], fs_s)
xe = detrend(emp[:, best], fs_e)
fS, PS, mS = band_power(xs, fs_s, 0.15, 0.5)
fE, PE, mE = band_power(xe, fs_e, 0.15, 0.5)
peak_f = fS[mS][np.argmax(PS[mS])]
print(f"best subcarrier={best}, fs~{fs_s:.0f} Hz")
print(f"STILL breathing-band peak at {peak_f:.2f} Hz = {peak_f*60:.0f} breaths/min")
print(f"breathing-band power  still={PS[mS].sum():.1f}  empty={PE[mE].sum():.1f}  "
      f"ratio={PS[mS].sum()/(PE[mE].sum()+1e-9):.1f}x")

fig, ax = plt.subplots(2, 1, figsize=(9, 6.5))
n = int(min(len(xs), fs_s * 40))                # ~40 s slice
t = np.arange(n) / fs_s
ax[0].plot(t, xs[:n], color='#d95f02', lw=0.9, label='still (seated person)')
ax[0].plot(t, xe[:min(n, len(xe))], color='#7570b3', lw=0.9, alpha=0.7, label='empty')
ax[0].set_title(f'One subcarrier over time (sc {best}, drift removed)')
ax[0].set_xlabel('seconds'); ax[0].set_ylabel('amplitude'); ax[0].legend()

ax[1].plot(fS, PS, color='#d95f02', label='still')
ax[1].plot(fE, PE, color='#7570b3', alpha=0.7, label='empty')
ax[1].axvspan(0.15, 0.5, color='green', alpha=0.12, label='breathing band 0.15-0.5 Hz')
ax[1].axvline(peak_f, color='green', ls='--', lw=1)
ax[1].set_xlim(0, 1.2); ax[1].set_title('Frequency content (FFT) — breathing shows here, not in the heatmap')
ax[1].set_xlabel('Hz'); ax[1].set_ylabel('power'); ax[1].legend()
ax[1].text(peak_f + 0.02, ax[1].get_ylim()[1] * 0.7,
           f'{peak_f*60:.0f} br/min', color='green')
plt.tight_layout()
plt.savefig('slides_assets/breathing_demo.png', dpi=130)
print('saved slides_assets/breathing_demo.png')
