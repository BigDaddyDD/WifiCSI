#!/usr/bin/env python3
"""
Long-block single-activity recorder — the efficient, confined-room protocol.

Instead of many short directional passes (which need an open room to work and
badly unbalance the classes), this records ONE long continuous block per
activity. Benefits: labels are perfectly reliable (you set the activity for the
whole block, no webcam), classes come out BALANCED (equal-length blocks), and it
works in a small/square room because you never have to leave the sensing zone.

Each activity block is `--secs` long (default 70 -- shortened 2026-07-21 from
120; still comfortably enough windows per class at 2s/1s-hop and cuts session
time by roughly 40%, see CLAUDE.md 4d). Do the activity continuously and
naturally for the whole block (walk = wander/loop the room; run = jog in
place or small loops if space is tight). Record a `collect_baseline.py` empty
for the same config first (that 90s, held-out capture is the REAL calibration
reference); vary --placement/--orientation across sessions for the placement
diversity that drives generalization.

Only ONE empty bracket now (at the start, before activities begin -- the room
is most reliably empty then). It used to be two (start + end); the second was
redundant now that collect_baseline.py's dedicated capture is the primary
empty reference -- this bracket is just a quick within-session sanity check /
fallback calibration source, so one is enough.

Usage (Terminal A already running stream_logger.py):
  python collect_blocks.py --room basement --placement p5 --orientation vert
  # --secs 120  longer blocks (more data, old default)   |   --dry-run  print the plan
"""
from collect_gui import base_argparser, run


def build_segments(secs):
    return [
        {'label': 'empty', 'dur': 30, 'ready': 8},
        {'label': 'stand', 'pos': 'stand still (pick a spot)', 'dur': secs, 'ready': 6},
        {'label': 'sit',   'pos': 'sit still',                 'dur': secs, 'ready': 8},
        {'label': 'walk',  'pos': 'walk continuously, vary your path', 'dur': secs, 'ready': 6},
        {'label': 'run',   'pos': 'jog continuously (in place / small loops)', 'dur': secs, 'ready': 8},
    ]


def main():
    ap = base_argparser('Record long balanced single-activity blocks (confined-room protocol).')
    ap.add_argument('--secs', type=int, default=70, help='seconds per activity block')
    args = ap.parse_args()
    run(args, build_segments(args.secs), session_type='blocks')


if __name__ == '__main__':
    main()
