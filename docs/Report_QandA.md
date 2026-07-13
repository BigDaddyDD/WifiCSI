# Anticipated Q&A — CSI Occupancy & Activity Report

Likely questions from reviewers, with short honest answers. Grouped by theme.

## Methodology & rigor

**Q: How did you evaluate — aren't you testing on the same data you trained on?**
No. The main results use leave-one-configuration-out cross-validation: train on
every placement/orientation except one, test on the held-out one, and rotate
through all of them. The activity confusion matrix (Fig 1) is those held-out
predictions pooled. The per-install matrix (Fig 6) uses 5-fold cross-validation
within each location. In no case is a window scored by a model that trained on it.

**Q: Why report "balanced accuracy" instead of plain accuracy?**
The classes are uneven (more walk windows than run, etc.). Balanced accuracy
averages the per-class recall, so a rare class counts as much as a common one. A
plain-accuracy number can look high just by predicting the majority class;
balanced accuracy is the honest measure and the one we lead with.

**Q: Isn't calibrating against an empty-room baseline a form of leakage?**
No. The baseline is recorded with nobody present, so it contains no
activity-label information — it's background normalization, like subtracting a
reference frame. The windows used to compute the baseline are held out of
scoring. It also mirrors real deployment: you record a short empty capture when
you install the sensor.

**Q: Why did you exclude some recordings?**
Two configurations had a weak radio link (RSSI below −60 dBm, 14–37% packet
loss) — past the usable range shown in Fig 5. Training on degraded-hardware data
would misrepresent the model. We documented the range limit rather than hide the
exclusion, and note it explicitly.

**Q: How much data, and how many people?**
14 configurations across multiple placements and orientations, 29 sessions,
~2.7 hours of recording, ~4,971 labeled 2-second windows. It is **one subject** —
cross-subject generalization is a stated limitation and future work.

## Results & interpretation

**Q: Why is presence reliable but fine activity weak across placements?**
A single antenna sees the room from one geometry. "Is a body present?" is a
large, robust change that transfers to new placements (0.79). The subtle
differences between two still postures, or two speeds, are geometry-specific and
do not transfer without on-site calibration.

**Q: Run looks broken in Fig 1 (10%) but fine in Fig 6 (73%). Which is real?**
Both, measuring different things. Fig 1 is the hardest case — two recording
protocols pooled and tested on an unseen placement; mixing an earlier
short-pass protocol's poor run data with the good long-block data drags run down.
Fig 6 is the consistent long-block protocol, calibrated on-site. Real deployment
(calibrate where you install) is closer to Fig 6.

**Q: What is the difference between "per-install" and "cross-placement"?**
Per-install = the model is trained/calibrated for the location it runs in.
Cross-placement = trained elsewhere and dropped into an unseen location. Both
record a short empty baseline on-site; the difference is only whether the trained
model has previously seen that location.

**Q: What's the residual presence error?**
Occasionally labeling a truly empty room "occupied." A motionless person is hard
to distinguish from an empty room because both produce almost no channel motion.
Occupied rooms are detected ~93% of the time.

## Hardware & signal

**Q: What is CSI, and how is it different from RSSI?**
RSSI is a single number — total received signal strength. CSI is the per-
subcarrier channel response (~52 values per packet), a much richer picture of how
the room reshapes the signal. RSSI is reported in the same packets and we use it
as a link-quality gauge.

**Q: Why do you discard the phase and use only amplitude?**
Raw CSI phase on these low-cost chips is corrupted by clock/timing offsets and is
unusable without heavy sanitization. Amplitude is stable and carries the motion
information we rely on.

**Q: What is the range limit and why?**
About 120 inches (~10 ft) between the two nodes for a single link. Beyond that,
signal strength falls below ~−60 dBm and packets drop heavily. Enclosed rooms
hold a usable link farther than open rooms. Additional nodes extend coverage.

**Q: Will this port to better hardware later?**
The **trained model** does not transfer across chipsets or antennas — CSI is
hardware-specific. What transfers is the collection protocol, the feature
pipeline, the labeling scheme, and the overall approach. New hardware requires
retraining or fine-tuning on that device. This should be set as an expectation up
front.

## Deployment & scope

**Q: Does it need calibration at every install?**
For reliable activity, yes — record ~90 seconds of the empty room at each
install. Presence tolerates a pre-trained model better, but the short baseline is
cheap and recommended everywhere.

**Q: Can it count how many people are present?**
Not yet — counting (0/1/2 people) is set up but not yet evaluated. Two people,
especially standing close together, partly overlap in the signal, so it is a
harder task that follows the single-person work.

**Q: Did you try to detect direction of movement?**
We explored it. With amplitude-only CSI the direction signal was weak and
unstable, so we do not claim it. It may become recoverable with phase or with
multiple receivers.

## Limitations & next steps

**Q: What is the single biggest limitation?**
One antenna = one viewpoint. That single fact drives the cross-placement activity
gap, coverage dead zones, and the empty-vs-motionless-person confusion.

**Q: What would improve the model the most?**
Adding a second and third receiver. Independent viewpoints directly attack
coverage, cross-placement transfer, and fine-activity separation. That is the
planned next phase; the spare hardware is already on hand.

**Q: How statistically confident are these numbers?**
They are point estimates over 14 configurations, one subject, one collection
period. More configurations and subjects would add formal confidence intervals.
That said, the qualitative findings — presence is portable, fine activity needs
on-site calibration, range ≈120 inches — held consistently across every subset we
evaluated.

**Q: Why a random forest rather than deep learning?**
With ~5,000 windows, a random forest is the right tool: fast, interpretable, and
resistant to overfitting on a modest dataset. Deep models need far more data and
become worth considering once multi-receiver collection scales the dataset up.
