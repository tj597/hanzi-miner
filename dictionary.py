"""CC-CEDICT loader + definition lookup.

CC-CEDICT is a public-domain-ish (CC BY-SA 4.0) Chinese->English dictionary
of ~120k entries. Line format:

    Traditional Simplified [pin1 yin1] /def 1/def 2/

We key on the SIMPLIFIED form (what you'll meet in modern usage) and keep the
list of English definitions. Pinyin for display is produced by pypinyin, so we
don't parse CEDICT's tone-numbered pinyin here.
"""

from __future__ import annotations

import os
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CEDICT_PATH = os.path.join(DATA_DIR, "cedict.txt")

_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")

_cedict: dict[str, list[str]] | None = None


def load_cedict(path: str | None = None) -> dict[str, list[str]]:
    """Load CC-CEDICT into {simplified_word: [definitions]} (cached)."""
    global _cedict
    if _cedict is not None:
        return _cedict

    path = path or CEDICT_PATH
    table: dict[str, list[str]] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            m = _LINE_RE.match(line.strip())
            if not m:
                continue
            _trad, simp, _pinyin, defs = m.groups()
            defs_list = [d.strip() for d in defs.split("/") if d.strip()]
            if simp not in table:
                table[simp] = defs_list

    _cedict = table
    return table


def lookup(word: str) -> list[str]:
    """Return English definitions for a word, or [] if not found."""
    return load_cedict().get(word, [])


def is_headword(word: str) -> bool:
    """True if the word exists as a CEDICT headword.

    Words absent from CEDICT are disproportionately proper nouns, names and
    OCR/segmentation noise, so the miner uses this to deprioritise them.
    """
    return word in load_cedict()
