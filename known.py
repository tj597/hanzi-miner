"""Known-vocabulary sets.

The whole +1 heuristic rests on knowing which words the learner already knows.
This is the hard part of the whole tool — a bad known-set produces junk cards.

We assemble the known-set from three sources:

1. HSK 2.0 levels 1-6 (data/hsk_L<n>.txt, ~5000 words). Pedagogically ordered,
   but this export has gaps — it omits bare function words like 没 and common
   greetings like 你好, so on its own it generates false "unknown" targets.
2. A frequency list (data/freq_zh.txt, top 5000 Chinese words from wordfreq).
   This fills exactly those gaps: 没 is rank 82, 不错 rank 949.
3. Words pasted by the learner (e.g. an Anki export), which beats both.

A "level" maps to a (HSK level, frequency cutoff) pair — see LEVELS.
"""

from __future__ import annotations

import os
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# level -> (HSK cutoff, how many top-frequency words to also assume known)
LEVELS = {
    1: (1, 500),
    2: (2, 1000),
    3: (3, 2000),
    4: (4, 3000),
    5: (5, 4000),
    6: (6, 5000),
}

_hsk_cache: dict[int, set[str]] = {}
_freq_cache: dict[int, set[str]] = {}


def load_hsk(level: int) -> set[str]:
    """Cumulative known-set for HSK level N (1..6): union of L1..LN words."""
    level = max(1, min(6, int(level)))
    if level in _hsk_cache:
        return _hsk_cache[level]

    words: set[str] = set()
    for n in range(1, level + 1):
        path = os.path.join(DATA_DIR, f"hsk_L{n}.txt")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if parts and parts[0].strip():
                    words.add(parts[0].strip())

    _hsk_cache[level] = words
    return words


def load_freq(n: int) -> set[str]:
    """Top-N most frequent Chinese words (frequency-ordered file)."""
    n = max(0, min(5000, int(n)))
    if n in _freq_cache:
        return _freq_cache[n]

    path = os.path.join(DATA_DIR, "freq_zh.txt")
    words: set[str] = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= n:
                    break
                w = line.strip()
                if w:
                    words.add(w)

    _freq_cache[n] = words
    return words


def parse_word_list(raw: str) -> set[str]:
    """Parse a pasted known-word list (one per line, or whitespace/comma separated)."""
    if not raw:
        return set()
    out: set[str] = set()
    for token in re.split(r"[\s,;，；、\t]+", raw):
        token = token.strip()
        if token and _CJK_RE.search(token):
            out.add(token)
    return out


def build_known(level: int = 2, extra: str = "", freq_n: int | None = None) -> set[str]:
    """Assemble the known-set: HSK preset + frequency list + extra pasted words."""
    level = max(1, min(6, int(level)))
    hsk_level, default_freq = LEVELS[level]
    if freq_n is None:
        freq_n = default_freq
    return load_hsk(hsk_level) | load_freq(freq_n) | parse_word_list(extra)


def charwise_known(word: str, known: set[str]) -> bool:
    """True if every character in `word` is individually a known word.

    Used only as a RANKING signal, never a filter. 你好 / 有意思 / 不错 are
    single jieba tokens whose characters the learner already knows, so they look
    "unknown" to a word-list-based check — but they're usually not the word you
    actually need to drill. Genuinely new words like 语法 or 胡同 fail this check
    and therefore rank first. Not filtering (only demoting) matters: filtering
    would silently drop real teaching targets like 语法 (语 + 法 are both known).
    """
    if len(word) < 2:
        return word in known
    return all(c in known for c in word)
