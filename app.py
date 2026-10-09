"""Hanzi Miner — paste Chinese, get i+1 flashcards.

Flask app with two endpoints:
  POST /api/mine   {text, mode, level, extra_known, max_unknown} -> {cards, stats}
  POST /api/deck   same body -> .apkg download

Run locally:   python app.py            (http://127.0.0.1:8080)
Deploy:        App Platform; it runs `gunicorn app:app --bind 0.0.0.0:$PORT`
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request, send_file

import deck as deck_mod
from known import build_known
from miner import add_pinyin, mine

app = Flask(__name__)


def _params(payload: dict) -> dict:
    return dict(
        text=payload.get("text", "") or "",
        known=build_known(
            level=int(payload.get("level", 2) or 2),
            extra=payload.get("extra_known", "") or "",
        ),
        mode="dialogue" if payload.get("mode", "dialogue") == "dialogue" else "prose",
        max_unknown=int(payload.get("max_unknown", 1) or 1),
        window=int(payload.get("window", 2) or 2),
    )


def _run(payload: dict) -> dict:
    r = _params(payload)
    if not r["text"].strip():
        return {"cards": [], "stats": {"lines": 0, "units": 0, "candidates": 0, "mined": 0}}
    result = mine(
        text=r["text"],
        known=r["known"],
        mode=r["mode"],
        max_unknown=r["max_unknown"],
        window=r["window"],
    )
    result["cards"] = add_pinyin(result["cards"])
    return result


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return "ok", 200


@app.post("/api/mine")
def api_mine():
    return jsonify(_run(request.get_json(force=True, silent=True) or {}))


@app.post("/api/deck")
def api_deck():
    payload = request.get_json(force=True, silent=True) or {}
    result = _run(payload)
    if not result["cards"]:
        return jsonify({"error": "no cards to export", "stats": result["stats"]}), 400
    data = deck_mod.build_deck(result["cards"])
    name = (payload.get("deck_name") or "Hanzi Miner").strip() or "Hanzi Miner"
    return send_file(
        __import__("io").BytesIO(data),
        mimetype="application/apkg",
        as_attachment=True,
        download_name=f"{name}.apkg",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="127.0.0.1", port=port, debug=True)
