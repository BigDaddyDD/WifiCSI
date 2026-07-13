#!/usr/bin/env python3
"""
Baseline-EMPTY recorder. A short empty-room capture per placement/orientation.

This gives the "empty" class its training windows for a config (the calibration
reference itself comes from the activity/block session's own empty brackets).
~90 s is plenty: at ~100 Hz that's ~88 windows, comparable to one activity block.
Record this, then run collect_blocks.py for the SAME --placement/--orientation
(and --room). The analysis pairs them by config.

Usage (Terminal A already running stream_logger.py):
  python collect_baseline.py --room basement --placement p1 --orientation vert
  # options: --secs 90   --ports COM17
"""
from collect_gui import base_argparser, run


def build_segments(secs):
    # single short empty block; longer get-ready so you can clear the room first
    return [{'label': 'empty', 'dur': int(secs), 'ready': 10}]


def main():
    ap = base_argparser('Record a short empty-room baseline for one config.')
    ap.add_argument('--secs', type=int, default=90, help='empty capture seconds (default 90)')
    args = ap.parse_args()
    run(args, build_segments(args.secs), session_type='baseline')


if __name__ == '__main__':
    main()
