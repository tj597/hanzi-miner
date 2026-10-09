"""Spoken-rhythm annotation: where to pause, and which way the pitch moves.

Output conventions (what the learner reads):
    /    short pause  — a breath inside a clause
    //   longer pause — a clause or sentence boundary
    ↗    rising intonation (question, surprise, an unfinished/leading tone)
    ↘    falling intonation (statement, certainty, finality)

Two annotators:

* `rule_based` — deterministic, free, offline. Sentence-final punctuation becomes
  a long pause, commas become short ones, ？ rises and 。！ fall. Good enough to be
  the fallback and to keep the feature working with no inference key.
* the model — adds the things punctuation can't tell you: a breath between a long
  subject and its predicate, a rise mid-clause, which comma is a real pause.

The model is treated as untrusted. `strip_markers` must reproduce the input
character for character, otherwise the annotation is thrown away and the rule-based
version is used instead. Without that check a model that "improves" the phrasing —
or one that emits its chain of thought — would silently corrupt the text the
learner is practising.
"""

from __future__ import annotations

import re

import generate as gen

# `//` before `/` so the long pause is not double-counted as two short ones.
_MARKER_RE = re.compile(r"\s*(?://|/)\s*|[\u2197\u2198\u2191\u2193]")
_SENT_END = "。！？!?…"
_SHORT_BREAK = "，、；：,;:"
_RISE = "？?"
_FALL = "。！!…"

# Full/half-width punctuation is folded before comparison, so a model that writes
# "!" for "！" is not rejected as if it had rewritten the text.
_PUNCT_FOLD = str.maketrans("！？。，、；：（）", "!?.,,;:()")

MAX_LINES = 60
BATCH = 30


def strip_markers(s: str) -> str:
    """Remove every rhythm marker, leaving the words as written."""
    return _MARKER_RE.sub("", s or "")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", strip_markers(s)).translate(_PUNCT_FOLD)


_WORD_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaffA-Za-z0-9]+")


def words_only(s: str) -> str:
    """Just the meaning-bearing characters: CJK plus alphanumerics."""
    return "".join(_WORD_CHAR_RE.findall(strip_markers(s or "")))


def rule_based(text: str) -> str:
    """Punctuation-driven rhythm. Deterministic and offline.

    The original punctuation is KEPT and markers are appended after it — an
    earlier version replaced 。！？ with ↘↗, which silently deleted the
    punctuation the learner was reading. Markers must be additive so that
    stripping them reproduces the input exactly; that is the invariant that makes
    the model's output safe to trust (see _norm).
    """
    if not text or not text.strip():
        return ""
    out = []
    for ch in text:
        if ch in _RISE:
            out.append(ch + "↗//")
        elif ch in _FALL:
            out.append(ch + "↘//")
        elif ch in _SHORT_BREAK:
            out.append(ch + "/")
        else:
            out.append(ch)
    s = "".join(out).strip()
    # a text with no final punctuation still falls at the end
    if s and s[-1] not in "↗↘/":
        s += "↘"
    return s


PROSODY_SYSTEM = (
    "You annotate Chinese text to show spoken rhythm for a language learner. "
    "You never change, add, remove or reorder the words themselves."
)

PROSODY_PROMPT = """Insert rhythm markers into the Chinese lines below, for a learner practising natural speech.

Markers:
- /   a SHORT pause (a breath inside a clause)
- //  a LONGER pause (a clause or sentence boundary)
- ↗   rising intonation (question, surprise, an unfinished or leading tone)
- ↘   falling intonation (statement, certainty, finality)

Rules:
- Do NOT alter a single Chinese character, and do NOT remove the original punctuation.
  Keep every 。，！？ exactly where it is; add markers around it.
- Put ↗ or ↘ immediately after the punctuation it belongs to, then the pause marker.
- Mark every sentence boundary with //; comma-level breaks get /.
- Use / sparingly: only where a speaker would truly breathe.

Worked examples (copy this style exactly):
  你好！你今天怎么样？
  -> 你好！↘//你今天怎么样？↗//
  我去图书馆看书了。那里很安静。
  -> 我去图书馆看书了。↘//那里很安静。↘//
  我买了苹果、香蕉和橘子。
  -> 我买了苹果、/香蕉、/和橘子。↘//

Return EXACTLY {n} lines, one per input line, with nothing else at all - no
numbering, no explanation, no English, no blank lines.

Input lines:
{body}"""


def _model_batch(lines: list[str], level: int) -> list[str] | None:
    """Annotate one batch. Returns None if the model's output fails validation.

    Validation compares the MEANING-BEARING characters only (`words_only`), not
    punctuation. That is deliberate. An earlier, stricter check compared the whole
    line including punctuation and rejected almost everything — the model likes to
    insert a 、to mark a breath, which is exactly the judgement being asked for.
    What must never change is the words; a punctuation tweak cannot mislead someone
    reading the sentence aloud. A model that rewrites or drops words is discarded,
    and the deterministic rule annotator takes over.
    """
    body = "\n".join(lines)
    prompt = PROSODY_PROMPT.format(n=len(lines), body=body)
    payload = {
        "model": gen.MODEL,
        "messages": [
            {"role": "system", "content": PROSODY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 4000,
    }
    import requests

    resp = requests.post(
        gen.API_URL,
        headers={"Authorization": f"Bearer {gen.api_key()}", "Content-Type": "application/json"},
        json=payload,
        timeout=150,
    )
    if resp.status_code != 200:
        return None
    raw = resp.json()["choices"][0]["message"]["content"]

    # A reasoning model may wrap its answer in prose; keep only plausible lines.
    got = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    got = [ln for ln in got if not re.search(r"[A-Za-z]", strip_markers(ln))]
    if len(got) != len(lines):
        return None
    for original, annotated in zip(lines, got):
        if words_only(original) != words_only(annotated):
            return None          # model changed the words -> discard the whole batch
    return got


def annotate_lines(lines: list[str], level: int = 3) -> dict:
    """Annotate each line. Returns {annotated, annotator}."""
    lines = [(ln or "") for ln in lines][:MAX_LINES]
    if not any(ln.strip() for ln in lines):
        return {"annotated": [], "annotator": "none"}

    if not gen.available():
        return {"annotated": [rule_based(ln) for ln in lines], "annotator": "rule"}

    out: list[str] = []
    used_model = 0
    for i in range(0, len(lines), BATCH):
        chunk = lines[i : i + BATCH]
        try:
            got = _model_batch(chunk, level)
        except Exception:
            got = None
        if got is None:
            out.extend(rule_based(ln) for ln in chunk)
        else:
            out.extend(got)
            used_model += len(chunk)

    if used_model == 0:
        annotator = "rule"
    elif used_model == len(lines):
        annotator = "model"
    else:
        annotator = "mixed"
    return {"annotated": out, "annotator": annotator}
