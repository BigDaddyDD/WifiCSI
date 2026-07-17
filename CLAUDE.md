# CSI Presence Sensing — Project State & Agent Handoff

Last updated: 2026-07-16. This file is the onboarding doc for any agent working
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

**New-environment two-script workflow (2026-07-12).** For a fresh room, empty
baseline and activities are recorded SEPARATELY via `collect_gui.py` (shared
parameterized GUI + slicer; per-segment duration/ready/direction; writes
`session_type` + `config=placement/orientation` into every JSON):
  - `collect_baseline.py --room <r> --placement <p> --orientation <o>` — one long
    empty capture (default 5 min) = the calibration reference for that config.
  - `collect_activity.py --room <r> --placement <p> --orientation <o>` —
    DIRECTIONAL passes: run/walk each as short single-direction segments
    (R2L/L2R alternating so you end where the next starts) + circular CW/CCW
    loops + stand/sit at spots, bracketed by short empties. `--light` = fewer
    reps; `--dry-run` prints the plan. Direction is stored in each seg JSON.
  - `collect_blocks.py --room <r> --placement <p> --orientation <o>` — **the
    preferred protocol for confined/square rooms (2026-07-12).** One long
    continuous block per activity (stand/sit/walk/run, `--secs` default 120),
    self-labeled → reliable labels, BALANCED classes (equal-length blocks ≈ equal
    windows, fixes the run/sit starvation), no get-ready waste. WHY: in a small
    square room you can't translate far enough to leave the antenna's sensing
    zone, so the directional passes barely differ (R2L≈L2R) and unbalance the
    data — drop direction there. Webcam auto-labeling also rejected (user reports
    it mislabels activities). Direction/webcam only make sense in an open room.
  - `collect_2p.py --room <r> --placement <p> --orientation <o>` — TWO-PERSON
    counting/activity: empty(0) / 1-person / 2-person segments (both stand
    spread+close, both sit, both walk, both jog, MIXED one-still-one-moving).
    Each seg JSON stores `count` (0/1/2) + `people` (per-person activities);
    labels are tokens like 2pstand/2pmixSW. Colour by count. Reuses collect_gui
    (which now supports per-segment `prompt`/`color`/`count`/`people`).
Pair baseline+activity by `config` (same placement/orientation/room). The
activity session also has its own short empty brackets, so it can self-calibrate
even before the analysis is wired to pool the 5-min baseline.
`guided_collect.py` (old single-script home_L flow) is unchanged and still works.

**Multi-environment status (2026-07-12), analysis in `analyze_liv.py` (multi-room,
config-based calibration, discovers data/study/<room>):**
- liv_room (living room, 5 configs p1-p5/vert): WEAK link, RSSI −61 dBm, ~72 Hz,
  deep fades during activity. KEY FINDING: presence is separable WITHIN a config
  (~0.89 balanced leaky) but does NOT generalize across configs (leave-one-
  config-out collapses to ~0.50) — unlike home_L which generalized (0.93).
  Resampling isn't the cause (raw≈resampled). Leading hypothesis: the weak,
  low-SNR link makes each placement's "occupied" signature idiosyncratic →
  no transfer. Direction probe was UNSTABLE (0.24-0.63 across preprocessing) —
  inconclusive, needs dedicated features. So the recordings are usable per-config
  but flagged the weak link as the suspect for the generalization failure.
- basement (enclosed, from 2026-07-12): EXCELLENT link — RSSI −43 dBm (10 dB
  stronger than home_L), 96-100 Hz, 0.0% drops, RSSI std 0.7. Best data yet.
- **RESULT CONFIRMED 2026-07-12 (the pending experiment):** presence
  leave-one-config-out (cross-placement) — home_L (−52 dBm) 0.93, basement
  (−43 dBm) **0.79**, liv_room (−61 dBm) **0.36**. STRONG LINK GENERALIZES,
  WEAK LINK DOES NOT. Link quality (not the room) drove the liv_room failure →
  deployment guideline: strong link (~−50 dBm+). 5-class activity generalization
  still weak everywhere (basement 0.41: empty 0.90/walk 0.58 ok, stand 0.30/
  sit 0.26/run 0.01 — run fails because directional passes are 5 s ≈ 4 windows).
  Run generalization via `python /tmp/gen.py`-style leave-one-config-out in
  analyze_liv (loco per room, brackets calibration).
- **FINAL SINGLE-LINK RESULT 2026-07-12 (strong-link basement blocks p5-p8, p9
  excluded as weak-link, `/tmp/final.py` = analyze_liv allspread, empty capped
  120/config):** PER-INSTALL (within-config, calibrate where deployed) 5-class
  balanced **0.76**. TRAIN-ONCE (cross-placement leave-one-config-out): presence
  0.58, coarse empty/static/moving 0.60, 5-class 0.36. CONCLUSION: single link
  works WITH per-install calibration but does NOT generalize train-once to an
  unseen placement; presence cross-config is dragged by empty-vs-still-person
  (blocks protocol has abundant stationary data). Single-link CHARACTERIZATION
  IS ESSENTIALLY COMPLETE — capabilities + walls mapped (range ~120 in, link
  quality drives generalization, per-install works, fine activity/direction/
  cross-placement do not). NEXT PHASE = 2nd/3rd RX (user has 2 spare ESP32-C3):
- **CORRECTION 2026-07-12 (pooled all STRONG-link data, `/tmp/pooled.py`, 14
  configs = home_L 5 + basement 9, excl weak liv_room + p9):** cross-config
  PRESENCE **0.79** (empty recall 0.65), 5-class **0.42**. The basement-blocks-
  only presence 0.58 was UNDER-TRAINED (3 training placements) — with 14 configs
  presence recovers to 0.79 (≈ last week's home_L 0.89). So PRESENCE DOES
  generalize train-once (~0.79, rising with data); it did NOT regress. FINE
  ACTIVITY is the real wall (5-class 0.42 cross-placement; stand/sit/run stay
  hard; run drops to 0.10 when pooling mixes good block-runs with bad directional
  short-runs — blocks-only run was 0.81). Refined conclusion: single link
  generalizes PRESENCE across placements, does NOT generalize FINE ACTIVITY;
  per-install calibration recovers fine activity to 0.76. 2nd RX targets fine
  activity + coverage, NOT presence (already portable).
  validate multi-RX capture (AP-as-RX0 + promiscuous sniffer on the STA unicast),
  re-run this analysis with 2 viewpoints. Deliverable to freeze: per-install
  model + range/link-quality guidelines + honest generalization boundary.
- **BLOCKS PROTOCOL VALIDATED 2026-07-12 (`collect_blocks.py`):** first long-block
  config (basement p5, 120 s balanced blocks) vs a directional config (p4),
  within-config leaky 5-class, same room/link: blocks **0.91 balanced** vs
  directional 0.72; run recall **0.28 → 0.81**, sit **0.67 → 0.95**. Balanced
  classes + long continuous signature fix the two chronically-failing classes.
  This is the leaky ceiling (within-config); need 2-3 more block configs for the
  honest cross-config number, but the protocol switch is clearly correct — use
  collect_blocks going forward, not the directional collect_activity. load_segments now skips
  incomplete sessions (<8 segs) — dropped the interrupted p3 stub (6 segs);
  p3ReRUN is the good p3 activity (its config tag is p3ReRUN/vert, calibrates
  off its own brackets). analyze_liv.py has a calibration-source diagnostic
  (baseline / brackets / allspread).

## 4c. Model-family comparison & antenna portability (2026-07-13 to 2026-07-16)

- **Model comparison (`model_comparison.py`, boss request "how would CNNs/deep
  learning do?"):** 10 model families under one FIXED protocol (same data,
  same per-config empty-baseline calibration, same leave-one-config-out) — 8
  classical models on the 160-D features + a 1D-CNN and GRU on raw calibrated
  windows. RESULT: no model beats the RandomForest baseline cross-placement —
  every classical model lands 0.37–0.46 balanced 5-class (RF 0.42); the 1D-CNN
  is nominally highest (0.46) but far worse at presence (0.59 vs RF 0.84) since
  it doesn't exploit the empty-baseline calibration the way engineered features
  do. Per-install, simpler linear models (logistic reg 0.85, RBF-SVM 0.83) edge
  out RF (0.79) once calibrated on-site. CONCLUSION: cross-placement activity is
  model-independent — the ceiling is the single-antenna sensor, not the
  algorithm. RF stays the default (fast, interpretable, tied for best).
  `docs/model_report_assets/fig7_model_comparison.png` + `model_comparison.json`.
- **Antenna portability test (`data/study/antenna_test/`, `basement_antenna_test/`,
  `gen_antenna_figs.py`):** boss asked whether the trained model plug-and-plays
  onto different hardware (Taoglas WDMP.2458.A UFL antenna; eventually a
  different capture device — Alfa AWUS036ACHM was proposed but its MT7610U
  chipset CANNOT do CSI, only Intel 5300/AX200/AX210, Atheros QCA9300, Nexmon,
  or ESP32 can). Recorded stock vs Taoglas antenna at 3 basement placements
  (p1/p2/p3), same protocol (baseline + blocks). **p2/stock blocks session is
  DEAD (0 packets, RX serial stall — never recorded, needs redo if p2/stock
  activity data is wanted).** One baseline was mislabeled p1 when it was
  actually p2/stock (fixed on disk 2026-07-16: renamed folder + corrected
  `session.json`/seg json `placement`/`config` fields).
  RESULT: **per-install (each antenna calibrated+evaluated on its own data)
  works fine for both** — stock 0.72 balanced, Taoglas 0.77 (Taoglas ~10 dB
  stronger RSSI, tracks the established link-quality-drives-accuracy finding).
  **Cross-antenna transfer (train on one, test on the other, zero retrain)
  COLLAPSES to chance** — 5-class 0.18 balanced (chance 0.20), presence 0.49
  (chance 0.50) — replicates and firms up the earlier single-position antenna
  finding across 3 orientations. CONCLUSION FOR LEADERSHIP: a new antenna/device
  is NOT a drop-in swap; CSI is hardware-specific (raw amplitude signature
  differs per antenna even for the identical physical scene) — what transfers
  is the collection protocol + feature pipeline + labeling scheme, not the
  trained model. New hardware needs its own calibration pass (minimum) or
  retraining (for full accuracy).
  `docs/model_report_assets/fig8_confusion_antenna.png` (stock vs Taoglas
  per-install confusion) + `fig9_antenna_transfer.png` (same-antenna vs
  cross-antenna bar chart). NEXT: 2-3 more stock/Taoglas position pairs to
  firm up the confidence interval (currently thin: stock n=2 configs, Taoglas
  n=3 configs).
- **Tree/feature visualizations for the boss (`gen_tree_figs.py`):**
  `fig10_feature_importance.png` (top-15 real feature importances from the
  production 300-tree forest — dominated by deviation-from-empty summary
  features: max/mean relative amplitude change, overall L2 deviation; no single
  subcarrier dominates) and `fig11_tree_diagram.png` — **the actual first tree
  of the real production forest, `clf.estimators_[0]`, whole** (depth 36, 850
  leaves, 1,699 nodes, ALL drawn, nothing truncated).
  **User feedback history on this figure (2026-07-16 → 2026-07-17), each round
  correcting the previous:** (1) rejected a conceptual "how ensembles work"
  schematic (`gen_forest_diagram.py` → `fig12_forest_ensemble.png`, dataset→
  trees→vote, no real data) — wanted the actual tree, deleted; (2) a depth-3-
  capped real tree wasn't "whole" (a depth cap truncates a tree that would keep
  splitting) and boxes had too much detail (`value=[...]` proportions); (3) a
  `min_samples_leaf=180` fully-terminated-but-smaller stand-alone tree (41
  nodes) was STILL not what was wanted — user explicitly wanted the actual
  production tree with all its real nodes/parameters, "does not need to be
  detailed, just a good representation." FINAL approach (implemented in
  `gen_tree_figs.py`): extract `clf.estimators_[0]` directly (genuinely one of
  the 300 real trees, unrestricted growth, exactly as trained) and render
  EVERY node via a custom layout (`build_row_rescaled_layout`) — nodes at
  depth ≤ 5 (63 of 1,699) get real labeled boxes (split feature/threshold,
  sample %, majority class, using abbreviated `short_feature_names` so text
  fits); nodes deeper than that (1,636 of them, down to depth 36) are drawn as
  small dots colored by majority class — still the real node/edge, just
  unlabeled since 1,699 text boxes cannot fit in one legible image. The key
  layout trick: space each DEPTH LEVEL independently across the same shared
  width (rather than positioning by descendant-leaf-count, which is what a
  naive layout does and is what made earlier attempts either overlap or waste
  huge space) — this is what makes a real, wildly-imbalanced 1,699-node tree
  renderable as one ~4660×1600px, ~1.1 MB image at all. No in-image caption/
  title (project convention); a legend (empty/stand/sit/walk/run colors) is
  the only text outside the tree itself.
- **Live link-health monitor added to `collect_gui.py` (2026-07-16):** the
  GUI now tails the stream file every ~0.7 s (never touches serial — respects
  the tkinter-serial curse) and shows a color banner (green/yellow/red) with
  live packet rate, loss %, and RSSI; red = alarm (bad link or no data at all)
  with a bell + explicit instruction to move nodes/re-aim or power-cycle both
  ESPs. End-of-session summary also lists any segment that finished bad so it's
  obvious what to re-record. This directly targets failures like the dead
  p2/stock recording above. Thresholds: rate <60 Hz or loss >15% = alarm; rate
  <85 Hz or loss >5% or RSSI < −57 dBm = warn.
- **BUGFIX 2026-07-17 — `gen_model_report.py`'s `real_dataset_totals()` was
  silently over-counting:** it derived "room" from `os.path.basename(os.path.
  dirname(sj))` where `sj` was the session.json path — that's actually the
  SESSION folder name, one level too shallow (needs `dirname(dirname(sj))` to
  reach the room folder). Net effect: its `room == 'liv_room'` exclusion check
  NEVER matched (session folders are named like `s1_p1_baseline_...`, never
  literally `liv_room`), so liv_room's recording time/packets were being added
  into the "dataset totals" text even though the actual model (`al.build(
  ['home_L','basement'])`) never trains on liv_room. Only the `'p9' in base`
  check happened to work (p9 IS in the session folder name). CORRECTED totals
  for the real home_L+basement model: **20 sessions, 113 min (1.9 hr), 669,353
  packets** (previously mis-reported as 29 sessions / 160 min / 910,071 packets
  — that was home_L+basement+liv_room combined). The window count (4,971) and
  all modeling numbers (presence/activity balanced accuracy) were NEVER
  affected — this bug only touched the printed/reported raw-recording totals
  text, not any figure or trained-model result. `real_dataset_totals()` now
  takes a `rooms=` parameter (default `('home_L','basement')`) so it can never
  silently sweep in a room outside the ones actually passed to `al.build()` —
  this matters going forward since new room folders keep appearing
  (antenna_test, basement_antenna_test are NOT part of the main model and must
  stay excluded from its reported totals).
- **Deliverables this week:** `docs/CSI_Model_Comparison.docx` (model
  comparison table + fig7), `docs/CSI_Progress_Update.docx` (recap doc),
  `docs/CSI_Week_Update_Slides.pptx` (9-slide brief deck: title, this week,
  **the dataset** [corrected totals above], model comparison, one real tree,
  feature importance, antenna confusion matrices, plug-and-play transfer
  result, status/next — plain white/black/Arial, no theme, minimal per-slide
  text by design so the user rewrites in their own voice) + matching
  `docs/CSI_Week_Update_TalkingPoints.docx` (full presenter explanation per
  slide, Times New Roman 12, incl. a one-liner per model family tested + what
  the "160 engineered features" breakdown actually is [3×52 per-subcarrier
  measures + 4 summary stats] + the recalibration-vs-retraining distinction:
  calibration = re-center against the new antenna's own empty capture, always
  done; retraining = actually fitting new decision boundaries on THAT
  antenna's own labeled activity data, which is what per-install actually
  does and cross-antenna transfer skips). Builders live in `slides_assets/`.
  **No dashed chance-line (or anything representing chance) on any graph** —
  removed `axhline`/`axvline` 0.5 lines from `gen_antenna_figs.py` (fig9) and
  `model_comparison.py` (fig7); antennas named explicitly ("ESP32 stock
  antenna" / "Taoglas antenna") rather than generic "same/different antenna" —
  fig9 plots 3 bars per group (stock per-install, Taoglas per-install,
  cross-antenna transfer). Regenerate fig7 cheaply with
  `python model_comparison.py figure` (reuses `model_comparison.json`, skips
  the ~10 min retrain of all 10 model families).

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

## 4b. LINK RANGE finding (2026-07-12) — a hard operational limit

Across all environments the controlling variable is RSSI / link margin, and the
single ESP32-C3 TX->RX link has a clear usable-range cliff at ~**120-125 inches
(~10 ft / 3 m)** node separation:
- ~90 in (home_L) −52 dBm 0% loss ✓ | ~100-120 in (basement p1-p8) −43..−51 dBm
  0% loss ✓ | ~125 in (living room) −61 dBm ~28% loss ✗ | ~136 in (basement p9)
  −62 dBm 14-37% loss ✗.
- Below ~120 in RSSI stays ≳ −52 dBm, ~0% packet loss, and the model GENERALIZES
  across placements. Beyond ~125 in RSSI falls to ≈ −61 dBm and packet loss
  spikes (14-37%) — a link-MARGIN cliff (steeper than free-space path loss), and
  generalization BREAKS (this is what killed the living room and p9).
- Environment modulates it: the enclosed basement holds a strong link farther
  (−43 at 120 in) than the open living room (−61 at 125 in) — reflections
  concentrate energy indoors. Practical rule: keep nodes within ~120 in AND
  verify RSSI ≳ −55 dBm before recording. p9 (−62 dBm, 136 in) is EXCLUDED from
  analysis as a weak-link outlier.

## 5. Analysis pipeline & current results

Main script: `phase_a_analysis.py` (auto-discovers sessions; just re-run it as
new data arrives). Pipeline: CSV → amplitude √(I²+Q²) (phase is discarded —
raw phase on these chips is unusable without sanitization) → **resample onto a
uniform 100 Hz grid using the RX `local_us` timestamps** (`load_seg`, added
2026-07-12: received CSI is ~10 ms-spaced in every room; what differs is dropped
packets/gaps, so this fills gaps → uniform sampling, correct FFT axis, and rooms
comparable regardless of packet loss; near-identity on clean streams) →
active-subcarrier mask (~52/64) → 2 s windows, 1 s hop → features per window (per-subcarrier
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
| Presence acc / balanced | 0.87 / 0.52 | **0.96 / 0.93** | — |
| 5-class acc / balanced | 0.38 / 0.30 | **0.52 / 0.52** | 0.40 / 0.32 |

(home_L, post-resampling 2026-07-12; was 0.94/0.89 and 0.50/0.49 before the
uniform-100Hz resample, which cleaned up minor timing gaps and lifted them.)

Presence per-class: occupied recall 0.96, empty recall 0.83; per-fold 0.83–0.98.
5-class per-class recall: empty 0.88, stand 0.63, walk 0.53, **sit 0.28,
run 0.14** (sit↔stand and run↔walk are the failure modes; run has least data).
Leaky ceiling 0.83 vs honest 0.50 quantifies geometry memorization.

**Improved activity model: `phase_a_hier.py` (2026-07-12).** Three upgrades on
the SAME data, same LOSO rigor: (1) richer motion features — sub-band fractions
(0.5-2 / 2-5 / 5-10 Hz), spectral centroid (tempo), 0.15-0.5 Hz over a 10 s
context (feature dim 117); (2) hierarchical classifier — stage 1
{empty/static/moving} then specialists static→{sit,stand}, moving→{walk,run};
(3) temporal smoothing (rolling majority, k=5). 5-class progression:
old-flat 0.50/0.49 → flat+richer 0.524/0.518 → hierarchical 0.540/0.523 →
+smoothing **0.567/0.543** (acc/balanced). Per-class (final): empty 0.96,
stand 0.76, walk 0.60, sit 0.29, **run 0.11**. **STAGE-1 {empty/static/moving}
= 0.81 acc / 0.82 balanced** — this coarse state is the robust, reportable
product. sit↔stand barely moved (single-link physics limit); run got WORSE with
smoothing (minority class absorbed into walk) — run needs DATA, not tuning
(hence the rebalanced collection script). Stage-1 moving-recall (0.66) is the
ceiling: run/walk windows with low instantaneous motion leak to "static".

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

1. **DONE 2026-07-12:** `SEGMENTS` in `guided_collect.py` rebalanced toward
   sit/run (2×empty, 2×stand, 3×sit, 1×walk, 3×run variants incl. in-place jog).
   Hierarchical model + richer features + smoothing built (`phase_a_hier.py`).
2. **NEXT (user, physical):** record sessions with the NEW script — need more
   sit/run windows (the model upgrades are data-limited on those). Also record
   at least one **repeat of an existing config** to separate config variance
   from session variance, and keep adding placements/orientations toward
   ~8–10 configs for confidence intervals.
3. Then re-run `phase_a_hier.py` — the specialists should improve most on the
   newly-abundant sit/run. If run still fails, that's the argument for RX #2.
   **ANALYSIS TODO when new-environment data lands:** (a) make discovery
   room-agnostic (currently `ROOT='data/study/home_L'`); (b) group/calibrate by
   `config` and pool empty windows across the baseline+activity sessions of a
   config (use the 5-min baseline as the empty reference); (c) add
   leave-one-ROOM-out as the cross-environment headline; (d) build
   direction-aware features (temporal evolution / sign of per-subcarrier trend)
   and test whether heading (R2L/L2R/CW/CCW) is recoverable from one link.
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
