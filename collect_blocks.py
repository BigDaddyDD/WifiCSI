#!/usr/bin/env python3
"""
Long-block single-activity recorder — the efficient, confined-room protocol.

Instead of many short directional passes (which need an open room to work and
badly unbalance the classes), this records ONE long continuous block per
activity. Benefits: labels are perfectly reliable (you set the activity for the
whole block, no webcam), classes come out BALANCED (equal-length blocks), and it
works in a small/square room because you never have to leave the sensing zone.

Each activity block is `--secs` long (default 120). Do the activity continuously
and naturally for the whole block (walk = wander/loop the room; run = jog in
place or small loops if space is tight). Record a `collect_baseline.py` empty for
the same config first; vary --placement/--orientation across sessions for the
placement diversity that drives generalization.

Usage (Terminal A already running stream_logger.py):
  python collect_blocks.py --room basement --placement p5 --orientation vert
  # --secs 180  longer blocks (more data)   |   --dry-run  print the plan
"""
from collect_gui import base_argparser, run


def build_segments(secs):
    return [
        {'label': 'empty', 'dur': 30, 'ready': 8},
        {'label': 'stand', 'pos': 'stand still (pick a spot)', 'dur': secs, 'ready': 6},
        {'label': 'sit',   'pos': 'sit still',                 'dur': secs, 'ready': 8},
        {'label': 'walk',  'pos': 'walk continuously, vary your path', 'dur': secs, 'ready': 6},
        {'label': 'run',   'pos': 'jog continuously (in place / small loops)', 'dur': secs, 'ready': 8},
        {'label': 'empty', 'dur': 30, 'ready': 8},
    ]


def main():
    ap = base_argparser('Record long balanced single-activity blocks (confined-room protocol).')
    ap.add_argument('--secs', type=int, default=120, help='seconds per activity block')
    args = ap.parse_args()
    run(args, build_segments(args.secs), session_type='blocks')


if __name__ == '__main__':
    main()
