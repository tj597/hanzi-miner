"""Tone-marked pinyin, in one place.

Used by the miner (per-card), the review queue (per-sentence hint), and the
library/generate views (per dialogue line). Kept separate from `miner` so `db`
can use it without importing jieba and the dictionary.
"""

from __future__ import annotations

import re

# A capturing group makes re.split emit [sep, run, sep, run, ..., sep], so the
# CJK runs land on odd indices and the separators on even ones.
_CJK_RUN = re.compile(r"([\u3400-\u4dbf\u4e00-\u9fff]+)")


def to_pinyin(text: str) -> str:
    """Tone-marked pinyin for the Chinese in `text`; punctuation/Latin pass through.

    pypinyin returns one entry per syllable, so syllables are joined with spaces —
    an unsegmented run of romanisation is hard to parse mid-sentence.
    """
    if not text:
        return ""
    try:
        from pypinyin import Style, pinyin
    except ImportError:
        return ""

    out = []
    for i, chunk in enumerate(_CJK_RUN.split(text)):
        if not chunk:
            continue
        if i % 2:
            out.append(" ".join(x[0] for x in pinyin(chunk, style=Style.TONE, errors="ignore")))
        else:
            out.append(chunk)
    return "".join(out)
