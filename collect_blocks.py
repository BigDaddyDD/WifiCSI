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
  # --activities stand,sit   confined space (e.g. a bedroom) where walk/run
  #                          aren't possible -- default is stand,sit,walk,run
"""
from collect_gui import base_argparser, run

ACTIVITY_SPECS = {
    'stand': {'label': 'stand', 'pos': 'stand still (pick a spot)', 'ready': 6},
    'sit':   {'label': 'sit',   'pos': 'sit still',                 'ready': 8},
    'walk':  {'label': 'walk',  'pos': 'walk continuously, vary your path', 'ready': 6},
    'run':   {'label': 'run',   'pos': 'jog continuously (in place / small loops)', 'ready': 8},
}


def build_segments(secs, activities=('stand', 'sit', 'walk', 'run')):
    segs = [{'label': 'empty', 'dur': 30, 'ready': 8}]
    for name in activities:
        spec = dict(ACTIVITY_SPECS[name])
        spec['dur'] = secs
        segs.append(spec)
    return segs


def main():
    ap = base_argparser('Record long balanced single-activity blocks (confined-room protocol).')
    ap.add_argument('--secs', type=int, default=70, help='seconds per activity block')
    ap.add_argument('--activities', default='stand,sit,walk,run',
                    help="comma-separated subset/order of activity blocks after the "
                         "empty bracket, e.g. 'stand,sit' when the space is too "
                         "confined for walk/run (default: stand,sit,walk,run)")
    args = ap.parse_args()
    activities = [a.strip() for a in args.activities.split(',') if a.strip()]
    unknown = [a for a in activities if a not in ACTIVITY_SPECS]
    if unknown:
        raise SystemExit(f"unknown --activities entries {unknown}, choose from {list(ACTIVITY_SPECS)}")
    run(args, build_segments(args.secs, activities), session_type='blocks')


if __name__ == '__main__':
    main()
