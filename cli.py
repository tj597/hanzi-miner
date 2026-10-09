"""Command-line miner — same logic as the web app, for eyeballing card quality.

Usage:
    python cli.py sample_dialogue.txt --level 2 --mode dialogue
    cat subs.txt | python cli.py - --level 3 --mode prose
"""

from __future__ import annotations

import argparse
import sys

from known import build_known
from miner import add_pinyin, mine


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine i+1 sentence cards from Chinese text.")
    ap.add_argument("path", help="text file, or - for stdin")
    ap.add_argument("--level", type=int, default=2, help="HSK level of known words (1-6)")
    ap.add_argument("--mode", choices=["dialogue", "prose"], default="dialogue")
    ap.add_argument("--max-unknown", type=int, default=1)
    ap.add_argument("--window", type=int, default=2, help="dialogue turn window")
    ap.add_argument("--extra-known", default="", help="extra known words, comma-separated")
    ap.add_argument("--limit", type=int, default=None, help="show at most N cards")
    ap.add_argument("--no-pinyin", action="store_true")
    args = ap.parse_args()

    if args.path == "-":
        text = sys.stdin.read()
    else:
        with open(args.path, encoding="utf-8") as fh:
            text = fh.read()

    known = build_known(level=args.level, extra=args.extra_known)
    result = mine(
        text=text,
        known=known,
        mode=args.mode,
        max_unknown=args.max_unknown,
        window=args.window,
    )
    cards = result["cards"]
    if not args.no_pinyin:
        cards = add_pinyin(cards)
    if args.limit:
        cards = cards[: args.limit]

    s = result["stats"]
    print(
        f"\nHSK-{args.level} known set: {len(known)} words | mode={args.mode} "
        f"| max_unknown={args.max_unknown}\n"
        f"{s['lines']} lines -> {s['units']} units -> "
        f"{s['candidates']} matched i+{args.max_unknown} -> {s['mined']} cards "
        f"({s['complex_forms']} complex-form)\n"
    )
    for i, c in enumerate(cards, 1):
        sent = c["sentence"].replace(c["target"], f"[{c['target']}]")
        flag = "  (complex form — chars already known)" if c.get("charwise_known") else ""
        print(f"{i:3}. {sent}{flag}")
        print(f"     {c.get('target_pinyin','')}  ->  {'; '.join(c['defs']) or '(no dict entry)'}")
        if c.get("sentence_pinyin"):
            print(f"     {c['sentence_pinyin']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
