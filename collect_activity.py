#!/usr/bin/env python3
"""
Directional ACTIVITY recorder. Each moving pass is its own short segment tagged
with a DIRECTION, so we can later build direction-aware features and try to
classify heading (a single link only weakly senses direction via how the
per-subcarrier amplitude sequence evolves — this data is what lets us test it).

Protocol per pass (open room):
  - GET READY: walk to the start side and stand still (room looks empty).
  - RECORDING: perform the ONE labelled pass in the named direction, then stop.
  Runs/walks alternate R2L <-> L2R so you end where the next pass begins (no
  repositioning). Circular walks are full CW / CCW loops. Short empty brackets
  at the start/end give a within-session empty check (the main empty comes from
  collect_baseline.py for the same config).

Directions: R2L (right->left), L2R (left->right), CW, CCW.

Usage (Terminal A already running stream_logger.py):
  python collect_activity.py --room lib_study --placement p1 --orientation flat
  # --light   fewer reps (quicker)   |   --dry-run   print plan only
"""
from collect_gui import base_argparser, run


def build_segments(light=False):
    run_reps = 3 if light else 4          # passes per run direction
    walk_reps = 2 if light else 3         # passes per straight-walk direction
    loop_reps = 1 if light else 2         # full loops per circular direction
    S = [{'label': 'empty', 'dur': 30, 'ready': 8}]

    # RUN — short straight passes, alternating direction (end = next start)
    for _ in range(run_reps):
        S.append({'label': 'run', 'direction': 'R2L', 'dur': 5, 'ready': 4})
        S.append({'label': 'run', 'direction': 'L2R', 'dur': 5, 'ready': 3})

    # WALK — straight passes, alternating direction
    for _ in range(walk_reps):
        S.append({'label': 'walk', 'direction': 'R2L', 'dur': 8, 'ready': 4})
        S.append({'label': 'walk', 'direction': 'L2R', 'dur': 8, 'ready': 3})

    # WALK — full circular loops
    for _ in range(loop_reps):
        S.append({'label': 'walk', 'direction': 'CW', 'dur': 18, 'ready': 5})
        S.append({'label': 'walk', 'direction': 'CCW', 'dur': 18, 'ready': 5})

    # STATIONARY — a couple of spots each
    S.append({'label': 'stand', 'pos': 'center', 'dur': 25, 'ready': 6})
    S.append({'label': 'stand', 'pos': 'left', 'dur': 25, 'ready': 6})
    S.append({'label': 'sit', 'pos': 'center', 'dur': 25, 'ready': 6})
    S.append({'label': 'sit', 'pos': 'left', 'dur': 25, 'ready': 6})

    S.append({'label': 'empty', 'dur': 30, 'ready': 8})
    return S


def main():
    ap = base_argparser('Record directional activities for one config.')
    ap.add_argument('--light', action='store_true', help='fewer reps (quicker session)')
    args = ap.parse_args()
    run(args, build_segments(args.light), session_type='activity')


if __name__ == '__main__':
    main()
