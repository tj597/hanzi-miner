"""Hanzi Miner — paste Chinese, mine i+1 cards, review them, generate new ones.

Endpoints
  GET  /                    the app (tabs: Mine / Review / Library / Generate)
  GET  /healthz             cheap liveness probe (no DB, no dictionary loading)
  POST /api/mine            {text, mode, level, max_unknown} -> {cards, stats}
  POST /api/deck            same body -> .apkg download
  POST /api/save            {text, cards, mode, level, title} -> persist to the bank
  GET  /api/stats           counts for the header
  GET  /api/words           ?filter=all|due|new|learning|known&q=
  GET  /api/review          ?limit=20 -> the review queue
  POST /api/review          {word, result: again|good|easy}
  GET  /api/library         pasted/generated texts
  GET  /api/texts/<id>      one text with its mined targets
  POST /api/generate        {count, level, topic, turns, save} -> new dialogue

Run locally:  python app.py      Deploy: gunicorn app:app (see .do/app.yaml)
"""

from __future__ import annotations

import io
import os
import re

from flask import Flask, jsonify, render_template, request, send_file

import db
import deck as deck_mod
import generate as gen_mod
import prosody
from known import build_known
from miner import add_pinyin, mine
from pinyin_util import to_pinyin

app = Flask(__name__)


def _mine(payload: dict) -> dict:
    text = (payload.get("text") or "").strip()
    level = int(payload.get("level", 2) or 2)
    if not text:
        return {"cards": [], "stats": {"lines": 0, "units": 0, "candidates": 0, "mined": 0}}
    result = mine(
        text=text,
        known=build_known(level=level, extra=payload.get("extra_known", "") or ""),
        mode="prose" if payload.get("mode") == "prose" else "dialogue",
        max_unknown=int(payload.get("max_unknown", 1) or 1),
        window=int(payload.get("window", 2) or 2),
    )
    result["cards"] = add_pinyin(result["cards"])
    return result


def _no_db(what: str):
    return (
        jsonify({"error": f"{what} needs a database", "detail": "DATABASE_URL is not configured"}),
        503,
    )


_LINE_RE = re.compile(r"^\s*([^：:\n]{1,10})\s*[：:]\s*(\S.*?)\s*$")


def _dialogue_lines(body: str) -> list[dict]:
    """Split a stored text into speaker/text/pinyin rows for display.

    Used by the generated-dialogue view and the library's text expander, so both
    can show an optional pinyin line without re-deriving it in the browser.
    """
    out = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        speaker, text = (m.group(1), m.group(2)) if m else ("", line)
        out.append({"speaker": speaker, "text": text, "pinyin": to_pinyin(text)})
    return out


# ------------------------------------------------------------------ the app


@app.get("/")
def index():
    return render_template(
        "index.html",
        can_generate=gen_mod.available(),
        has_db=db.available(),
        model=gen_mod.MODEL,
    )


@app.get("/healthz")
def healthz():
    """Deliberately trivial: must not touch the dictionary, jieba, or the DB."""
    return "ok", 200


# ------------------------------------------------------------------ mining


@app.post("/api/mine")
def api_mine():
    return jsonify(_mine(request.get_json(force=True, silent=True) or {}))


@app.post("/api/deck")
def api_deck():
    payload = request.get_json(force=True, silent=True) or {}
    result = _mine(payload)
    if not result["cards"]:
        return jsonify({"error": "no cards to export", "stats": result["stats"]}), 400
    data = deck_mod.build_deck(result["cards"])
    name = (payload.get("deck_name") or "Hanzi Miner").strip() or "Hanzi Miner"
    return send_file(
        io.BytesIO(data),
        mimetype="application/apkg",
        as_attachment=True,
        download_name=f"{name}.apkg",
    )


@app.post("/api/save")
def api_save():
    if not db.available():
        return _no_db("Saving")
    payload = request.get_json(force=True, silent=True) or {}
    cards = payload.get("cards") or []
    text = (payload.get("text") or "").strip()
    if not text or not cards:
        return jsonify({"error": "nothing to save — mine some cards first"}), 400
    try:
        saved = db.save_mined(
            text=text,
            cards=cards,
            mode=payload.get("mode", "dialogue"),
            level=int(payload.get("level", 2) or 2),
            title=payload.get("title"),
        )
    except Exception as exc:  # surface DB errors rather than a bare 500
        return jsonify({"error": "could not save", "detail": str(exc)[:300]}), 500
    return jsonify({**saved, "stats": db.stats()})


# ------------------------------------------------------------------ the bank


@app.get("/api/stats")
def api_stats():
    if not db.available():
        return jsonify({"error": "no database"}), 503
    try:
        return jsonify(db.stats())
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500


@app.get("/api/words")
def api_words():
    if not db.available():
        return _no_db("The word bank")
    try:
        return jsonify(
            {
                "words": db.list_words(
                    filter=request.args.get("filter", "all"),
                    q=request.args.get("q", ""),
                    limit=int(request.args.get("limit", 200)),
                )
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500


@app.post("/api/words/delete")
def api_word_delete():
    """Remove a word from the bank. POST (not DELETE) because the word is CJK and
    a URL path segment would need encoding for no benefit."""
    if not db.available():
        return _no_db("The word bank")
    payload = request.get_json(force=True, silent=True) or {}
    word = (payload.get("word") or "").strip()
    if not word:
        return jsonify({"error": "no word supplied"}), 400
    try:
        out = db.delete_word(word)
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500
    if not out["deleted"]:
        return jsonify(out), 404
    return jsonify({**out, "stats": db.stats()})


@app.get("/api/library")
def api_library():
    if not db.available():
        return _no_db("The library")
    try:
        return jsonify({"texts": db.list_texts(), "stats": db.stats()})
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500


@app.get("/api/texts/<int:text_id>")
def api_text(text_id: int):
    if not db.available():
        return _no_db("The library")
    t = db.get_text(text_id)
    if not t:
        return jsonify({"error": "not found"}), 404
    t["lines"] = _dialogue_lines(t["body"])
    return jsonify(t)


@app.post("/api/texts/delete")
def api_text_delete():
    if not db.available():
        return _no_db("The library")
    payload = request.get_json(force=True, silent=True) or {}
    try:
        text_id = int(payload.get("id"))
    except (TypeError, ValueError):
        return jsonify({"error": "no text id supplied"}), 400
    try:
        out = db.delete_text(text_id)
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500
    if not out["deleted"]:
        return jsonify(out), 404
    return jsonify({**out, "stats": db.stats()})


@app.post("/api/prosody")
def api_prosody():
    """Annotate lines with pause and intonation marks for speaking practice."""
    payload = request.get_json(force=True, silent=True) or {}
    lines = payload.get("lines")
    if lines is None:
        text = payload.get("text") or ""
        lines = text.splitlines()
    if not isinstance(lines, list):
        return jsonify({"error": "lines must be a list"}), 400
    lines = [str(x) for x in lines]
    if not any(ln.strip() for ln in lines if ln):
        return jsonify({"error": "nothing to annotate"}), 400
    try:
        out = prosody.annotate_lines(lines, level=int(payload.get("level", 3) or 3))
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500
    return jsonify(out)


# ------------------------------------------------------------------ review


@app.get("/api/review")
def api_review_queue():
    """mode=self (default) for self-graded cards, mode=quiz for multiple choice."""
    if not db.available():
        return _no_db("Review")
    mode = request.args.get("mode", "self")
    limit = int(request.args.get("limit", 20))
    try:
        queue = db.quiz_queue(limit) if mode == "quiz" else db.review_queue(limit)
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500
    out = {"queue": queue, "mode": "quiz" if mode == "quiz" else "self"}
    if mode == "quiz":
        out["quizable"] = all(q.get("quizable", False) for q in queue) if queue else False
    return jsonify(out)


@app.post("/api/review")
def api_review():
    if not db.available():
        return _no_db("Review")
    payload = request.get_json(force=True, silent=True) or {}
    result = payload.get("result", "good")
    if result not in ("again", "good", "easy"):
        return jsonify({"error": "result must be again|good|easy"}), 400
    try:
        out = db.review_word(payload.get("word", ""), result)
    except Exception as exc:
        return jsonify({"error": str(exc)[:300]}), 500
    if out.get("error"):
        return jsonify(out), 404
    return jsonify({**out, "stats": db.stats()})


# ------------------------------------------------------------------ generate


@app.post("/api/generate")
def api_generate():
    payload = request.get_json(force=True, silent=True) or {}
    if not gen_mod.available():
        return jsonify({"error": "Dialogue generation needs DO_INFERENCE_KEY on the server"}), 503

    level = int(payload.get("level", 3) or 3)
    words = payload.get("words") or []
    if not words and db.available():
        try:
            words = db.pick_words(n=int(payload.get("count", 15) or 15))
        except Exception as exc:
            return jsonify({"error": f"could not read the word bank: {exc}"[:300]}), 500
    if not words:
        return jsonify({"error": "no words to build a dialogue from — mine and save some first"}), 400

    try:
        out = gen_mod.generate_dialogue(
            words=words,
            level=level,
            topic=payload.get("topic", "") or "",
            turns=int(payload.get("turns", 10) or 10),
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)[:400]}), 502

    # One click: a generated dialogue lands in the library with its words mined,
    # so it enters the review queue immediately.
    saved = None
    mined = None
    if payload.get("save", True) and db.available():
        mined = _mine({"text": out["body"], "mode": "dialogue", "level": level})
        try:
            saved = db.save_mined(
                text=out["body"],
                cards=mined["cards"],
                mode="dialogue",
                level=level,
                title=out["title"],
                source="generated",
            )
            saved["stats"] = db.stats()
        except Exception as exc:
            saved = {"error": str(exc)[:300]}

    out["lines"] = _dialogue_lines(out["body"])
    return jsonify({"dialogue": out, "mined": mined, "saved": saved})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8080)), debug=True)
