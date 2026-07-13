#!/usr/bin/env python3
"""
TWO-PERSON presence / counting / activity recorder.

Extends the single-person protocol to 0 / 1 / 2 people so we can start on:
  - COUNTING: empty vs 1 vs 2 people (the core new capability)
  - 2-person ACTIVITY: both standing/sitting/walking/jogging, and MIXED
    (one still, one moving) — the realistic multi-occupant case.

Each segment stores `count` (0/1/2) and `people` (per-person activities) in its
JSON, plus a colour by count (grey/orange/purple) on screen. It reuses the same
engine as the 1-person scripts (GUI never touches serial; timestamp slicing).

Record a `collect_baseline.py` empty capture for the SAME --placement/orientation
first — that's the empty (count 0) calibration reference.

Prompts address BOTH people; "A" = you (running the GUI), "B" = the other person.
"spread apart" vs "close together" matters: two people close together look like
one to a single link, so we sample both.

Usage (Terminal A already running stream_logger.py):
  python collect_2p.py --room basement --placement p1 --orientation vert
  # --light  fewer segments   |   --dry-run  print the plan
"""
from collect_gui import base_argparser, run

GREY, ORANGE, PURPLE = '#455a64', '#e08a1e', '#8e44ad'


def seg(label, count, people, prompt, dur, ready, color):
    return {'label': label, 'count': count, 'people': people,
            'prompt': prompt, 'dur': dur, 'ready': ready, 'color': color}


def build_segments(light=False):
    S = [
        seg('empty', 0, [], 'EMPTY\nboth leave the room', 30, 8, GREY),

        # --- counting anchors: ONE person ---
        seg('1pstand', 1, ['stand'], '1 PERSON\nB leaves — A stands still', 25, 8, ORANGE),
        seg('1pwalk', 1, ['walk'], '1 PERSON\nA walks around (B stays out)', 25, 6, ORANGE),

        # --- TWO people, stationary ---
        seg('2pstand', 2, ['stand', 'stand'],
            '2 PEOPLE — STAND\nspread apart (opposite sides)', 25, 8, PURPLE),
        seg('2pstandC', 2, ['stand', 'stand'],
            '2 PEOPLE — STAND\nclose together (side by side)', 25, 6, PURPLE),
        seg('2psit', 2, ['sit', 'sit'], '2 PEOPLE — SIT\nboth seated', 25, 8, PURPLE),

        # --- TWO people, moving ---
        seg('2pwalk', 2, ['walk', 'walk'],
            '2 PEOPLE — WALK\nboth walk around / mingle', 30, 8, PURPLE),
        seg('2pjog', 2, ['run', 'run'],
            '2 PEOPLE — JOG\nboth jog around (careful)', 20, 8, PURPLE),

        # --- MIXED (one still, one moving) ---
        seg('2pmixSW', 2, ['sit', 'walk'],
            '2 PEOPLE — MIXED\nA SITS · B WALKS', 25, 8, PURPLE),
        seg('2pmixTW', 2, ['stand', 'walk'],
            '2 PEOPLE — MIXED\nA STANDS · B WALKS', 25, 8, PURPLE),
    ]
    if not light:
        # repeats at different spots for counting robustness
        S += [
            seg('1pstand', 1, ['stand'], '1 PERSON\nA stands at a DIFFERENT spot', 25, 6, ORANGE),
            seg('2pstand', 2, ['stand', 'stand'],
                '2 PEOPLE — STAND\ndifferent spots than before', 25, 6, PURPLE),
            seg('2pwalk', 2, ['walk', 'walk'],
                '2 PEOPLE — WALK\nboth walk around again', 25, 6, PURPLE),
        ]
    S.append(seg('empty', 0, [], 'EMPTY\nboth leave the room', 30, 8, GREY))
    return S


def main():
    ap = base_argparser('Record two-person presence/counting/activity.')
    ap.add_argument('--light', action='store_true', help='fewer segments (quicker)')
    args = ap.parse_args()
    run(args, build_segments(args.light), session_type='2person')


if __name__ == '__main__':
    main()
