"""Anki deck export via genanki.

Card shape (the classic sentence-mining layout):

  front: 我喜欢学习中文[语法]         <- target word emphasised, in context
  back:  yǔfǎ  — grammar
         full-sentence pinyin (optional)
         dictionary definitions

The generated .apkg imports into Anki desktop/mobile with no add-ons. Note that
each export produces a NEW deck id; we keep it stable so re-imports update the
same deck rather than spawning duplicates (genanki dedupes notes by guid).
"""

from __future__ import annotations

import hashlib
import io

import genanki

DECK_ID = 0x5A17ED01  # arbitrary but fixed: re-imports land in one deck
MODEL_ID = 0x5A17ED02

_MODEL = genanki.Model(
    MODEL_ID,
    "Hanzi Miner i+1",
    fields=[
        {"name": "Sentence"},
        {"name": "Target"},
        {"name": "Reading"},
        {"name": "Meaning"},
        {"name": "SentencePinyin"},
    ],
    templates=[
        {
            "name": "Mine -> Meaning",
            "qfmt": '<div class="sentence">{{Sentence}}</div>',
            "afmt": (
                '<div class="sentence">{{Sentence}}</div>'
                '<hr id=answer>'
                '<div class="target">{{Target}} <span class="reading">{{Reading}}</span></div>'
                '<div class="meaning">{{Meaning}}</div>'
                '<div class="spinyin">{{SentencePinyin}}</div>'
            ),
        }
    ],
    css="""
    .card { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
            font-size: 26px; text-align: center; color: #1a1a1a; background: #fafafa; }
    .sentence { font-size: 30px; line-height: 1.6; }
    .sentence b, .sentence strong { color: #c0392b; }
    .target { font-size: 34px; margin-top: 8px; }
    .reading { color: #7f8c8d; font-size: 22px; }
    .meaning { margin-top: 6px; color: #2c3e50; }
    .spinyin { margin-top: 10px; color: #95a5a6; font-size: 18px; }
    """,
)


def _bold_target(sentence: str, target: str) -> str:
    """Wrap the first occurrence of the target word in <b>."""
    idx = sentence.find(target)
    if idx < 0:
        return sentence
    return (
        sentence[:idx]
        + "<b>"
        + sentence[idx : idx + len(target)]
        + "</b>"
        + sentence[idx + len(target) :]
    )


def _guid(sentence: str, target: str) -> str:
    return hashlib.sha1(f"{target}|{sentence}".encode()).hexdigest()[:16]


def build_deck(cards: list[dict], deck_name: str = "Hanzi Miner") -> bytes:
    """Build an .apkg and return it as bytes."""
    deck = genanki.Deck(DECK_ID, deck_name)
    for c in cards:
        meaning = "; ".join(c.get("defs") or []) or c.get("target_pinyin", "")
        note = genanki.Note(
            model=_MODEL,
            fields=[
                _bold_target(c["sentence"], c["target"]),
                c["target"],
                c.get("target_pinyin", ""),
                meaning,
                c.get("sentence_pinyin", ""),
            ],
            guid=_guid(c["sentence"], c["target"]),
        )
        deck.add_note(note)

    buf = io.BytesIO()
    genanki.Package(deck).write_to_file(buf)
    return buf.getvalue()
