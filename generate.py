"""Dialogue generation via DigitalOcean Serverless Inference.

OpenAI-compatible chat completions at https://inference.do-ai.run/v1/chat/completions,
authenticated with a Model Access Key (NOT the control-plane PAT). The key reaches
the running app as DO_INFERENCE_KEY.

Why generate dialogues at all: the miner can only work on text you already have,
so your deck can only ever cover vocabulary you happened to meet. Generating a
dialogue around the words you keep failing forces those words back into a fresh,
guessable context — the whole point of i+1.

LESSON (cost me a whole deploy): some models on this endpoint are *reasoning*
models that emit their chain of thought. With only a "no commentary" instruction
they wrote pages of English self-debate about HSK levels, interleaved with
Chinese drafts, and any line containing a Chinese character survived naive
parsing. Two defences, both applied below:
  1. ask for the dialogue wrapped in <dialogue> tags and read only inside them;
  2. accept a line ONLY when its speaker label is exactly one of the two names we
     asked for AND the turn contains no Latin letters or ASCII digits.
"""

from __future__ import annotations

import os
import re

import requests

API_URL = os.environ.get("DO_INFERENCE_URL", "https://inference.do-ai.run/v1/chat/completions")
# qwen3.8-max writes the most idiomatic spoken Chinese of the models probed.
# qwen3.5-397b-a17b is a strong writer too but timed out at 120s here.
# Run scripts/model_probe.py if you change this.
MODEL = os.environ.get("DO_INFERENCE_MODEL", "qwen3.8-max")

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN_OR_DIGIT = re.compile(r"[A-Za-z0-9]")
_LINE = re.compile(r"^\s*([^：:\n]{1,10})\s*[：:]\s*(\S.*?)\s*$")
_TAGS = re.compile(r"<dialogue>(.*?)</dialogue>", re.S | re.I)


def api_key() -> str:
    return os.environ.get("DO_INFERENCE_KEY", "").strip()


def available() -> bool:
    return bool(api_key())


SYSTEM = (
    "You are a Chinese teacher writing short practice dialogues for one specific learner. "
    "You write natural, spoken, everyday Chinese — how people actually talk, not textbook prose."
)

PROMPT = """Write a short Chinese dialogue for a learner at HSK level {level}.

Rules:
- Use EVERY one of these target words at least once, naturally, so its meaning is
  guessable from the surrounding sentences: {words}
- Keep everything else within common HSK {level} vocabulary. Nothing above HSK {level_hi}.
- Exactly {turns} turns, alternating between two speakers named {a} and {b}.
- Each turn 4-25 Chinese characters. Colloquial and spoken.
{topic_line}- Wrap the whole dialogue in <dialogue> and </dialogue> tags. Put NOTHING else
  inside those tags.
- Inside the tags, one turn per line, in exactly this format: {a}：内容
- Write NO explanation, notes, numbering, romanisation, translation, or English anywhere.
  Do not think out loud inside the tags.

Output only the tagged dialogue."""


def _clean(raw: str, a: str, b: str, max_turns: int | None = None) -> str:
    """Keep only well-formed turns spoken by `a` or `b`. Drops everything else."""
    m = _TAGS.search(raw)
    body = m.group(1) if m else raw

    out: list[str] = []
    for line in body.splitlines():
        lm = _LINE.match(line.strip())
        if not lm:
            continue
        speaker, text = lm.group(1).strip(), lm.group(2).strip()
        if speaker not in (a, b):              # kills "Turn1：…" and all reasoning labels
            continue
        if _LATIN_OR_DIGIT.search(text):       # a Chinese turn has no latin letters or digits
            continue
        if not _CJK.search(text):
            continue
        text = text.strip().strip("「」“”\"'` ")
        n = len(_CJK.findall(text))
        if not 2 <= n <= 40:
            continue
        if out and out[-1] == f"{speaker}：{text}":   # models often repeat a whole draft
            continue
        out.append(f"{speaker}：{text}")

    if max_turns:
        out = out[:max_turns]
    return "\n".join(out)


def _call(prompt: str, temperature: float, timeout: int) -> dict:
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}],
            "temperature": temperature,
            # Generous, because a reasoning model spends output tokens thinking
            # BEFORE it writes the dialogue. At 1600 a heavy reasoner can be cut
            # off mid-thought and return nothing usable.
            "max_tokens": 4000,
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"inference returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def generate_dialogue(
    words: list[str],
    level: int = 3,
    topic: str = "",
    turns: int = 10,
    names: tuple[str, str] = ("小明", "小红"),
    timeout: int = 150,
) -> dict:
    """Generate a dialogue that uses `words`. Raises RuntimeError if it can't."""
    if not available():
        raise RuntimeError("DO_INFERENCE_KEY is not configured on the server")
    if not words:
        raise RuntimeError("no words supplied to build a dialogue from")

    a, b = names
    prompt = PROMPT.format(
        level=level, level_hi=min(6, level + 2), words="、".join(words), turns=turns,
        a=a, b=b, topic_line=f"- Topic/setting: {topic}\n" if topic.strip() else "",
    )

    last: dict = {}
    for attempt, temperature in enumerate((0.8, 0.4)):
        data = _call(prompt, temperature, timeout)
        raw = data["choices"][0]["message"]["content"]
        last = data
        body = _clean(raw, a, b, max_turns=turns)
        used = [w for w in words if w in body]
        turns_ok = len(body.splitlines()) >= max(4, turns - 2)
        if used and turns_ok:
            break
    else:
        if not body:
            raise RuntimeError(
                "model returned no usable dialogue (it may have emitted only reasoning)")

    used = [w for w in words if w in body]
    return {
        "body": body,
        "title": (topic.strip() or "Generated") + " · " + "、".join(used[:3] or words[:3]),
        "model": last.get("model", MODEL),
        "usage": last.get("usage", {}),
        "words_requested": words,
        "words_used": used,
        "words_missed": [w for w in words if w not in body],
        "turns": len(body.splitlines()),
    }
