"""The +1 sentence miner.

Takes raw pasted text (dialogue or prose), cleans it, splits it into candidate
units, and keeps only the units containing EXACTLY ONE word the learner doesn't
know — the "i+1" sweet spot. Those units become flashcards.

Design notes that matter:

* Chinese is not space-delimited, so "word" boundaries come from jieba.
* DIALOGUE is mined at the EXCHANGE level by default: short lines like 好。 carry
  no context, so we also consider 2-line (and optionally 3-line) windows and keep
  whichever window has exactly one unknown. This is what makes the new word
  guessable from context — the entire point of i+1.
* One card per target word: if a word shows up as the single unknown in 40
  units, we keep the shortest, cleanest one.
* Ranked by how often the target word appears in the text, so the flashcards go
  to the vocabulary that actually matters for this piece of material.
"""

from __future__ import annotations

import html
import re
from collections import Counter

import jieba

from dictionary import is_headword, lookup
from known import charwise_known
from pinyin_util import to_pinyin

CJK_RANGE = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ffff"
_CJK_RE = re.compile(f"[{CJK_RANGE}]")

# 00:01:23,000 / 1:02:03 / 12:34 — subtitle timestamps.
_TS_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?\b")
# Leading speaker labels: "A:", "小明:", "Speaker 1:", "主持人：".
_LABEL_RE = re.compile(
    r"^\s*(?:[A-Za-z][A-Za-z0-9_]{0,15}(?:\s+\d{1,2})?|\d{1,2}|[\u4e00-\u9fff]{1,6})\s*[:：]\s*"
)
# Sentence-final punctuation used to split prose into sentences.
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?…；;])")


def cjk_len(s: str) -> int:
    return len(_CJK_RE.findall(s))


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s))


def clean_lines(text: str) -> list[str]:
    """Normalise raw pasted text into clean, CJK-bearing lines.

    Strips HTML entities, timestamps, speaker labels and blank/pure-English
    lines. Keeps line structure (dialogue turns stay separate).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for raw in text.split("\n"):
        s = html.unescape(raw).replace("\u3000", " ").strip()
        if not s:
            continue
        if _TS_RE.fullmatch(s):
            continue
        s = _TS_RE.sub(" ", s)
        s = _LABEL_RE.sub("", s).strip()
        s = re.sub(r"\s{2,}", " ", s).strip()
        if not _has_cjk(s):
            continue
        out.append(s)
    return out


def tokenize(s: str) -> list[str]:
    return [t.strip() for t in jieba.lcut(s) if t.strip()]


def unknown_tokens(tokens: list[str], known: set[str]) -> list[str]:
    """Tokens that are CJK words the learner does not know yet."""
    return [t for t in tokens if _has_cjk(t) and t not in known]


def units_prose(lines: list[str]) -> list[str]:
    text = "\n".join(lines)
    parts = re.split(r"\n+", text)
    sentences: list[str] = []
    for p in parts:
        for s in _SENT_SPLIT_RE.split(p):
            s = s.strip()
            if s:
                sentences.append(s)
    return sentences


def units_dialogue(lines: list[str], window: int = 2) -> list[str]:
    """Sliding windows over consecutive dialogue turns (1..window lines)."""
    units: list[str] = []
    for i in range(len(lines)):
        for w in range(1, window + 1):
            chunk = lines[i : i + w]
            if len(chunk) < w:
                break
            units.append(" ".join(chunk))
    return units


def mine(
    text: str,
    known: set[str],
    mode: str = "dialogue",
    max_unknown: int = 1,
    min_len: int = 4,
    max_len: int = 60,
    window: int = 2,
    max_cards: int | None = None,
    strict: bool = False,
) -> dict:
    """Mine i+1 cards from raw text. Returns {cards, stats}.

    strict=True drops "complex-form" candidates (a token not in the word list
    whose characters are all individually known, e.g. 你好, 有意思) — useful
    when the known-set is known to be incomplete, but it can hide real targets.
    """
    lines = clean_lines(text)
    units = (
        units_dialogue(lines, window=window)
        if mode == "dialogue"
        else units_prose(lines)
    )

    corpus = [t for t in tokenize(" ".join(lines)) if _has_cjk(t)]
    freq = Counter(corpus)

    best: dict[str, str] = {}  # target word -> best (shortest) unit
    scanned = 0
    for unit in units:
        n = cjk_len(unit)
        if n < min_len or n > max_len:
            continue
        unk = unknown_tokens(tokenize(unit), known)
        if len(unk) != max_unknown:
            continue
        scanned += 1
        target = unk[0]
        cur = best.get(target)
        if cur is None or len(unit) < len(cur):
            best[target] = unit

    cards = []
    for target, unit in best.items():
        cw = charwise_known(target, known)
        if strict and cw:
            continue
        cards.append(
            {
                "sentence": unit,
                "target": target,
                "defs": lookup(target),
                "in_dict": is_headword(target),
                "corpus_freq": freq.get(target, 0),
                "charwise_known": cw,
            }
        )

    # Rank: genuinely-new words first, then words this text leans on, then real
    # headwords, then shorter cards. "charwise_known" targets (compound forms
    # whose characters the learner already knows) sink to the bottom instead of
    # being dropped, so they're visible but not in the way.
    cards.sort(
        key=lambda c: (
            not c["in_dict"],       # real dictionary words first (absent = usually a jieba artifact)
            c["charwise_known"],    # then genuinely-new over complex forms
            -c["corpus_freq"],      # then words this text leans on
            len(c["sentence"]),     # then shorter cards
        )
    )
    if max_cards:
        cards = cards[:max_cards]

    stats = {
        "lines": len(lines),
        "units": len(units),
        "candidates": scanned,
        "mined": len(cards),
        "complex_forms": sum(1 for c in cards if c["charwise_known"]),
    }
    return {"cards": cards, "stats": stats}


def add_pinyin(cards: list[dict]) -> list[dict]:
    """Attach tone-marked pinyin for the target word and the whole sentence."""
    for c in cards:
        c["target_pinyin"] = to_pinyin(c["target"])
        c["sentence_pinyin"] = to_pinyin(c["sentence"])
    return cards
