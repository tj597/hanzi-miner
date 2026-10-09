"""Probe candidate models on the dialogue task and report which behave.

Re-run whenever DigitalOcean adds or changes models — the failure mode is not an
error, it is a model that quietly emits its chain of thought instead of a
dialogue, which passes a naive test and ships garbage cards.

    DO_INFERENCE_KEY=... python scripts/model_probe.py [model ...]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate as gen  # noqa: E402

DEFAULT_MODELS = [
    "qwen3.8-max",
    "deepseek-v4-pro",
    "minimax-m2.5",
    "gemma-4-31B-it",
    # qwen3.5-397b-a17b: strong writer but it timed out at 120s; only include it
    # if you raise the request timeout.
]

WORDS = ["语法", "图书馆", "加班", "报销"]


def main() -> int:
    if not gen.available():
        print("DO_INFERENCE_KEY is not set")
        return 2
    models = sys.argv[1:] or DEFAULT_MODELS
    for model in models:
        gen.MODEL = model
        print(f"\n{'='*70}\n{model}\n{'='*70}")
        try:
            out = gen.generate_dialogue(WORDS, level=3, turns=8)
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        lines = out["body"].splitlines()
        print(f"  turns={len(lines)} used={len(out['words_used'])}/{len(WORDS)} "
              f"missed={out['words_missed']}")
        for ln in lines:
            print("   ", ln)
        # crude junk detector: any latin/digit inside a turn means the parser failed
        junk = [ln for ln in lines if any(c.isascii() and c.isalnum() for c in ln)]
        print(f"  junk lines: {len(junk)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
