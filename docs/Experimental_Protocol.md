# Wi-Fi CSI Human Sensing — Experimental Protocol & Evaluation Plan

**Version:** 0.1 (draft) · **Date:** 2026-06-26 · **Author:** Dylan Dhawan
**Status:** pre-registration draft for internal + external review

> Purpose: define, *before* data collection, exactly what we measure, how, and how
> we will evaluate it — so results are reproducible and survive external scrutiny.
> This is a **characterization study**: the goal is to quantify what CSI sensing
> can and cannot do as we scale hardware (1→2→3 links), and to document the error
> structure and its physical causes — not merely to maximize one accuracy number.

---

## 1. Objectives & Research Questions

- **RQ1 — Scaling:** How does each capability change from 1→2→3 receiver links, and what residual errors remain at each step?
- **RQ2 — Generalization:** How well does each capability transfer across rooms, link placements, and people, with and without per-site calibration?
- **RQ3 — Failure modes:** What are the confusion structures and their *physical* causes (coverage dead zones, count saturation, activity aliasing, ground-truth occlusion)?
- **RQ4 — Efficiency:** What is the accuracy↔compute trade-off, and where can this run (laptop → edge → on-ESP)?

## 2. Capabilities (target variables)

| ID | Task | Classes / output | Notes |
|----|------|------------------|-------|
| C1 | Presence | empty / occupied | subset of C2 |
| C2 | Single-person activity | empty, stand, sit, walk, run | **Phase A focus**; "run" = fast pacing/jog (small room) — define operationally |
| C3 | People counting | 0, 1, 2, 3 | **needs helpers**; ground-truth burden |
| C4 | Position (zone) | near-RX / center / near-TX, left / right | zone-level only (no floor grid yet) |
| C5 | Direction of motion | toward / away / lateral / none | from camera bbox tracking |

## 3. System Under Test

- **Radio:** Seeed XIAO ESP32-C3 (single antenna, 2.4 GHz, 802.11n, 20 MHz / HT20).
- **CSI:** LLTF only → fixed 64 subcarriers (128 int8 I/Q), ~52 usable; logged at 921600 baud.
- **Topology (NEW, fixed for the whole study):** **1 TX broadcasting + N receiver "sniffers"** (promiscuous CSI capture, no association), N ∈ {1,2,3}. All RX log to **one PC** → shared clock; align RX streams by PC timestamp. Phase A uses N=1; scaling adds RX with **no firmware change** (avoids a mid-study confound).
- **Controlled parameters (fixed & logged every session):** Wi-Fi channel (fixed, e.g. 6), TX power, packet rate (target 100 Hz), TX MAC (filter target), firmware commit hash, device positions/orientation, room ID, date/time.
- **Acceptance gate per session:** mean rate within 10% of target, packet loss < ~5% (per RX), constant vector length — else re-record.

## 4. Ground Truth & Labeling

- **Sensor:** webcam recording synchronized to CSI on the **same PC clock**; per-frame pose estimation via **Ultralytics YOLO11-pose** (COCO-pretrained).
- **Activity (C2):** **scripted protocol** is the gold label — subject performs a cued activity for a fixed interval; webcam used to *verify* and to flag deviations. This is robust to table occlusion (which defeats pose-based sit/stand).
- **Presence/motion:** webcam person-detection + frame-difference motion (auto-labeled), as already validated.
- **Counting (C3):** webcam person count; **manual audit** of a random subset to quantify count-label error; mitigate occlusion via camera placement (and a 2nd/overhead camera if available).
- **Position/direction (C4/C5):** camera image regions → coarse zones; bbox trajectory → direction. Coordinate-level localization deferred (needs floor calibration / clear room).
- **Label quality reporting:** audit a random ≥5% of windows manually; report label-noise rate. Reviewers will ask — we answer with a number.

## 5. Experimental Design

Factors: **capability × hardware (N links) × environment × subject.** Full factorial is intractable, so we use a **phased fractional design** and report coverage explicitly.

- **Environments:** Room L (large conference, existing), Room S (small conference, new); ≥2 link placements per room; ≥2 days where feasible (temporal robustness).
- **Subjects:** start **solo** (1 subject); add **2–3 subjects** for C3 and cross-subject tests in a batched session. Solo-achievable vs helper-required is tagged below.
- **Solo-now:** C1, C2, C4, C5 (single person); single-link and multi-link (one mover).
- **Needs helpers:** C3 (counting), cross-subject generalization for C1/C2.

## 6. Data Collection Protocol

- **Session = one continuous take**, one configuration (room, placement, N links, subject). Each take is the unit held out in evaluation.
- **Per take (~3–5 min):** start with ~30 s **empty calibration** (records the per-site "empty" baseline used by the model), then a **randomized, cued sequence** of the target activities, with the cue times logged. Randomize order across takes to decorrelate activity from time-in-session.
- **Replication:** ≥3–4 takes per (room × placement) cell, spread across time; vary subject position within the room.
- **Metadata sidecar (JSON) per take:** room, placement ID, N links + per-RX position, subject ID, activity script + cue timestamps, channel, TX power, packet rate, firmware hash, software version, seeds.
- **Naming/versioning:** immutable raw data under `data/`, dataset snapshots tagged with a version; nothing renamed after the fact (a prior bug: renamed files with stale labels — never again).

## 7. Preprocessing & Features

- **Windowing:** 2 s windows, 1 s hop (≈50% overlap); wall-clock windows (robust to packet loss); min-packets and label-purity gates.
- **Per-window features:** per-subcarrier amplitude variability (motion), deviation from per-site empty baseline (presence), 0.5–5 Hz motion-band fraction, 0.15–0.5 Hz low-frequency energy; per-environment **calibration** (subtract + scale-normalize vs empty).
- **Ablation feature groups** (for RQ): amplitude-only vs amplitude+sanitized-phase; motion vs static vs low-freq; calibration on/off; # subcarriers; window length; # links.

## 8. Models

- **Baselines (mandatory, to attribute gains):** (a) chance / majority, (b) **RSSI-only** model (RSSI is logged — shows CSI's added value), (c) variance-threshold motion detector.
- **Primary:** Random Forest (interpretable, robust on modest tabular data).
- **Planned comparisons:** gradient boosting (XGBoost/LightGBM); deep models (CNN/LSTM/transformer on raw CSI) **only** once data volume justifies — reported as a separate study.
- **Selection discipline:** all hyperparameters tuned **within training folds only**; no test-set peeking.

## 9. Evaluation Methodology

- **Splits, reported separately (never random-window splits):**
  - Leave-one-**session**-out (within a configuration),
  - Leave-one-**placement/room**-out (cross-environment, the key generalization axis),
  - Leave-one-**subject**-out (cross-person; when subjects available).
- **Metrics:** accuracy, **balanced accuracy**, per-class precision/recall/F1, macro-F1, confusion matrices; **counting:** MAE + confusion + tolerance-±1 accuracy; **position/direction:** zone accuracy + confusion.
- **Statistics:** report **mean ± 95% CI** across folds/subjects (or bootstrap); **paired significance tests** (e.g., Wilcoxon signed-rank across matched folds) for "N+1 links > N links" and "calibrated > uncalibrated" claims. State effect sizes, not just p-values.
- **Operating points:** ROC / precision–recall and a chosen operating point per deployment cost model (missing a person vs false alarm); probability **calibration/reliability** reported.
- **Honesty controls:** report the leaky random-split number alongside the held-out number to demonstrate the gap and justify the methodology.

## 10. Error-Analysis Plan (RQ3 — "see the errors")

- Confusion matrices per capability and per held-out condition.
- **Coverage maps:** detection performance vs. subject position relative to the link(s); quantify dead zones and how added links fill them.
- **Counting:** saturation curve (accuracy vs. true count); where/why it breaks.
- **Activity:** which activities alias (e.g., sit vs stand vs empty) and the physical reason.
- Each major error tied to a **physical cause** (Fresnel-zone coverage, motion magnitude, ground-truth occlusion, multipath geometry).

## 11. Efficiency Study (RQ4)

- **Metrics:** inference latency/window, throughput, end-to-end decision latency, model size (params/MB), feature-extraction cost, FLOPs, peak memory.
- **Trade-offs (Pareto):** accuracy vs. # features, # subcarriers, sample rate, window length, model family; model compression / quantization.
- **Deployment tiers:** laptop (current) → edge (Raspberry Pi) → on-ESP (TinyML, quantized) — with honest feasibility limits per tier.

## 12. Reproducibility Package (deliverable to external team)

- Versioned raw dataset + **data dictionary**; **data card** and **model card**.
- Pinned software environment, fixed random seeds, exact firmware commit, capture configs.
- Released analysis code with the exact commands to regenerate every figure/number.
- This protocol document, version-controlled, updated as an auditable log of changes.

## 13. Phased Roadmap & Milestones

- **Phase A (solo):** finalize sniffer firmware; collect C1/C2 across Room L + Room S, ≥2 placements each; full baselines/ablations/stats. → establishes the framework.
- **Phase B (solo + helpers):** C4/C5 (zone position/direction); then C3 counting (helpers) in a batched session.
- **Phase C:** scale to 2 then 3 RX links; re-run the matrix; quantify scaling gains and residual errors (RQ1).
- **Phase D:** efficiency & deployment study (RQ4).
- **Cross-cutting:** reproducibility package maintained from Phase A.

## 14. Risks & Limitations (stated up front)

- Early phases are **single-subject** and **two specific rooms**; generalization claims bounded accordingly.
- CSI is **hardware/firmware/environment-specific**; results are for ESP32-C3 HT20 LLTF and may not transfer to other radios without adaptation.
- **Ground-truth limits:** webcam occlusion (counting), no floor calibration yet (coordinate localization), "run" constrained by small-room geometry.
- Single-link motion sensing is **coverage-limited** by design — expected, and a core finding.

## 15. Appendix

- **Label taxonomy:** empty, stand, sit, walk, run; count 0–3; zones {near-RX, center, near-TX} × {left, right}; direction {toward, away, lateral, none}.
- **Metadata schema:** see per-take JSON fields in §6.
- **Change log:** v0.1 — initial draft.
