# CSI Presence Sensing — Project State & Agent Handoff

Last updated: 2026-07-02. This file is the onboarding doc for any agent working
in this workspace. Read it fully before touching anything.

## 1. What this project is

Wi-Fi CSI (Channel State Information) human presence + activity detection using
SEEED Studio XIAO ESP32-C3 boards. Strategy: fully characterize what **one**
TX→RX link can do, then scale to 2 and 3 receivers. The user is in a PhD
research lab; results go to an **external, well-funded team that re-tests
everything**, so depth, statistical rigor, error analysis, and reproducibility
are mandatory — not nice-to-haves.

**Division of labor: the agent (Claude) writes ALL code** (firmware + Python).
The user oversees, runs things physically (moves boards, records sessions),
and gives feedback. Do not wait for the user to write code.

Boss requirements (2026-06-26): rigorous CHARACTERIZATION study — "see the
errors"; test all variables (activities, positions, orientations, multi-person
later); document every decision and model stat; model efficiency matters; vary
the **orientation of the nodes** so the model doesn't depend on one placement.

## 2. Hardware / firmware (the part that will bite you)

- Working rig: `firmware/csi_rx` (SoftAP receiver, **COM17**, plugged into the
  logging PC) + `firmware/csi_tx` (STA metronome, **COM18**, wall power).
  TX sends 100 Hz UDP unicast to 192.168.4.1:5000 on channel 1; RX streams
  `CSI_DATA,...` lines at 921600 baud, ~97 Hz effective, 52 usable subcarriers
  (LLTF, HT20; DC null ~index 32, guard nulls at edges).
- **CRITICAL: esp32 Arduino core MUST be 2.0.17** (`arduino-cli core install
  esp32:esp32@2.0.17`). Core 3.x/IDF5 forces PMF (802.11w) and the link flaps
  (~1 Hz, disconnect reason 209 SA_QUERY_TIMEOUT). Never let it upgrade.
- Flash command:
  `& "$env:USERPROFILE\arduino-cli\arduino-cli.exe" compile --fqbn esp32:esp32:XIAO_ESP32C3 --upload -p COMxx firmware/<sketch>`
- ~10 s association delay after boot before CSI flows.
- **RX serial gets STUCK if COM17 isn't drained** (its Serial.print blocks).
  Fix: power-cycle the RX board, start the logger immediately, keep it running
  continuously. Never open/close the port repeatedly.
- `firmware/_archive/` holds abandoned experiments (broadcast+sniffer path —
  SoftAP broadcast is DSSS, no OFDM LTF → no CSI). Don't flash those.
- Future multi-RX plan (Phase C): AP as RX0 + promiscuous sniffers capturing
  the STA's **unicast** OFDM frames (those DO produce CSI). 2 spare ESPs exist.

## 3. Data collection workflow (two terminals, decoupled by design)

**TKINTER-SERIAL CURSE:** on this Windows machine, any serial reader living in
or spawned by a tkinter process reads 0 packets, while the identical script in
its own terminal reads ~97/s. Hence the split — do not "simplify" it back:

1. Terminal A: `python stream_logger.py COM17 data/study/_live_stream.tsv`
   (writes `<pc_time>\t<raw line>`; stderr heartbeat `HB read=N` must climb).
2. Terminal B: `python guided_collect.py --ports COM17 --orientation <o> --placement <p> --tx x,y --rx x,y`
   — a GUI that is ONLY a guide (per user request): shows each segment prompt +
   6 s GET READY + 30 s RECORDING, records start/stop timestamps, and at the end
   slices the stream file by time into per-segment CSVs + JSON sidecars.

Output: `data/study/home_L/<subject>_<placement>_<ts>/segNN_<label>_<ts>__COM17.csv`
+ `segNN_....json` (label, position, xy_in, orientation, packets, drops) +
`session.json` (placement, node_orientation, tx/rx coords).

Room: `home_L` (in `data/rooms.json`) — L-shaped, bottom 150 in, left 124 in,
right 314 in; taped grid cols A–F (x = 0..150 in, 30 in step), rows 1–5
(y = 0..120 in). In EXTERNAL documents call it a **"library study room"** —
never "house"/"home".

## 4. Dataset state (as of 2026-07-02)

5 clean sessions in `data/study/home_L/`, each = one placement×orientation
config, 10 segments each, ~97 Hz:
`p1/flat`, `p2/rot90`, `p3/vertical`, `p3/rot90`, `b3/vertical` → 1,408
two-second windows: empty 281, stand 422, sit 282, walk 282, run 141.

Metadata corrections already applied (don't redo): stand/sit prompted at C3
were actually at **C2** (xy [60,30]); placement **p3 = grid F5** (annotated
`placement_grid`/`placement_xy_in`); orientation typo `vetical`→`vertical`
normalized everywhere. Walk segments were back-and-forth (not one-way); run =
perimeter. rot90 seg08 path was C1→F5. Bad/failed sessions are quarantined in
`data/study_bad/` — never mix them back in.

## 5. Analysis pipeline & current results

Main script: `phase_a_analysis.py` (auto-discovers sessions; just re-run it as
new data arrives). Pipeline: CSV → amplitude √(I²+Q²) (phase is discarded —
raw phase on these chips is unusable without sanitization) → active-subcarrier
mask (~52/64) → 2 s windows, 1 s hop → features per window (per-subcarrier
temporal std, level-normalized mean shape, 0.5–5 Hz motion-band fraction,
summaries; from `csi_dataset.window_features`) → RandomForest(400,
class_weight=balanced) → **leave-one-SESSION-out** CV (train 4 configs, test
the unseen 5th). Also prints a leaky random-CV ceiling and an RSSI-only
baseline for contrast.

**Calibration is the core recipe:** raw CSI amplitude overfits the exact
geometry. Calibrated features = deviation of each window from that session's
own EMPTY baseline, then per-session z-score vs empty. Calibration-empty
windows (every-other empty window, spread across the session) are held out of
train AND test so empty-recall isn't self-flattered.

Current numbers (leave-one-session-out, pooled over held-out configs):

| Task | Raw CSI | Calibrated | RSSI-only |
|---|---|---|---|
| Presence acc / balanced | 0.87 / 0.52 | **0.94 / 0.89** | — |
| 5-class acc / balanced | 0.38 / 0.29 | **0.50 / 0.49** | 0.42 / 0.33 |

Presence per-class: occupied recall 0.96, empty recall 0.83; per-fold 0.83–0.98.
5-class per-class recall: empty 0.88, stand 0.63, walk 0.53, **sit 0.28,
run 0.14** (sit↔stand and run↔walk are the failure modes; run has least data).
Leaky ceiling 0.83 vs honest 0.50 quantifies geometry memorization.

**Key scientific findings so far:**
- Zero-shot portability is dead; per-environment empty-baseline calibration
  restores cross-config generalization. This is the deployment recipe.
- Empty fingerprint is STABLE within a session (start-vs-end shape cosine
  ≥0.999, RSSI ≤0.7 dB drift) — no channel drift over ~8 min.
- BUT presence hinges on a REPRESENTATIVE empty baseline: one short contiguous
  30 s empty capture underestimates empty-class variance → balanced 0.63 /
  empty-recall 0.32; a spread/longer baseline → 0.89 / 0.83. Deployment rule:
  longer or periodically-refreshed empty baseline at install.
- Single link senses motion mainly near its LoS/Fresnel zone (office-era
  coverage analysis) — the argument for adding RX #2/#3.

Earlier office-era work (confA rooms, webcam/YOLO auto-labeling, breathing
features, `presence_model.py`, `phase1_av*.py`) is superseded for Phase A but
its findings and code (esp. `breathing_feats`) are reusable.

## 6. Deliverables & how to regenerate

- `docs/Phase_A_Interim_Report.docx` — brief boss-facing report. Built by
  `report_figs.py` (figures + `docs/report_assets/metrics.json`, plain
  matplotlib, real numbers only) then `build_report.py`.
- `docs/Phase_A_Interim_Slides.pptx` — 7 slides, **plain white bg, black Arial,
  no theme** (user requirement). Built by `build_slides.py`.
- `CSI Weekly Progress.pptx` — older weekly deck (`build_ppt.py` +
  `gen_slide_figures.py`).
- Rigor docs: `docs/Experimental_Protocol.md` (pre-registered plan),
  `docs/Methods_and_Decisions.md`, `docs/model_stats_report.md`.
- Regeneration order: `python report_figs.py` → `python build_report.py` →
  `python build_slides.py`. **Office locks open files** (PermissionError) —
  ask the user to close Word/PowerPoint before rebuilding.
- PENDING: figures were regenerated with **no titles** and no chance/majority
  line; the Word doc was rebuilt, but `build_slides.py` still needs a re-run
  once the user closes the PPTX (it was locked at last attempt).

## 7. Presentation/wording rules from the user

- Never use the word "honest" in outward-facing slides/docs.
- Say "low-frequency fluctuations", NOT "breathing" (we are not claiming
  breathing detection yet).
- Setup is a "library study room" in external docs.
- Charts: plain default matplotlib, nothing that "looks AI-generated"; no chart
  titles; colored charts are fine but slide THEME is strict black-on-white.
- Reports must explain how numbers were computed (the audience re-derives them).

## 8. Workspace layout

- Root `.py` = active pipeline; they use FLAT sibling imports (`csi_dataset`,
  `log_csi`, `phase1_av`, `collect_scripted`, `phase_a_analysis`...) — moving
  them breaks imports, keep them at root.
- `diagnostics/` = standalone serial debug tools (see its README).
- `old/` = pre-project scripts + dead code (`test_plogger.py` references a
  removed class). `collect.py`, `phase1_presence.py` at root are superseded but
  kept.
- `firmware/` = working `csi_rx` + `csi_tx`; `firmware/_archive/` = failed
  experiments (see its README).
- `data/study/` = the real dataset; `data/study_bad/` = quarantine;
  `data/av/` + `data/raw/` = office-era data.

## 9. Agreed next steps (in priority order)

1. **Before the user records more:** rebalance `SEGMENTS` in
   `guided_collect.py` toward sit/run (e.g. 2×empty, 2×stand, 3×sit at
   different spots, 1×walk, 3×run variants) — run/sit are data-starved.
2. More sessions: new placements/orientations for CIs (target ~8–10 configs)
   AND at least one **repeat of an existing config** to separate config
   variance from session variance.
3. Model upgrades using existing data: hierarchical classifier
   ({empty/static/moving} → static:{sit,stand}, moving:{walk,run}); motion
   sub-band features (0.5–2 / 2–5 / 5–10 Hz + spectral peak) for run-vs-walk;
   longer windows (5–10 s) + low-frequency (0.15–0.5 Hz) features for
   sit-vs-stand; temporal smoothing reported separately from per-window scores.
4. Presence: distance-to-empty-distribution detector as a principled baseline.
5. Efficiency study (accuracy vs trees/features/window — boss request).
6. Phase C: validate 2nd RX as promiscuous sniffer on the unicast link.

## 10. Environment notes

- Windows 11, PowerShell 5.1; Python 3.11 at
  `C:\Users\li107\AppData\Local\Programs\Python\Python311`. numpy, sklearn,
  matplotlib, python-docx, python-pptx, PIL, ultralytics, opencv installed.
- No LibreOffice (can't render pptx/docx to images for visual checks; verify
  PNGs directly with the Read tool instead).
- One program per COM port — close Arduino IDE Serial Monitor before running
  any Python serial reader.
