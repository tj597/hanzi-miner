"""Verify the deployed app end-to-end from outside.

Run after every deploy — a green health check only proves the container booted.
This exercises the real features over HTTPS and prints what actually came back.

    python scripts/verify_live.py https://<app>.ondigitalocean.app
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

SAMPLE = open("samples/dialogue_office.txt", encoding="utf-8").read()
PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  — {detail}" if detail else ""))


def call(base: str, path: str, payload: dict | None = None, raw: bool = False):
    req = urllib.request.Request(base + path)
    if payload is not None:
        req.data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=200) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def main(base: str) -> int:
    base = base.rstrip("/")

    status, body = call(base, "/", raw=True)
    page = body.decode("utf-8", "replace")
    check("GET / renders", status == 200 and len(page) > 5000, f"{len(page)} bytes")
    for tab in ("tab-review", "tab-library", "tab-generate"):
        check(f"page has {tab}", tab in page)
    check("page says DB is on", "window.HAS_DB = true" in page)
    check("page says generation is on", "window.CAN_GENERATE = true" in page)

    status, body = call(base, "/healthz", raw=True)
    check("GET /healthz", status == 200 and body.strip() == b"ok")

    status, mined = call(base, "/api/mine", {"text": SAMPLE, "mode": "dialogue", "level": 3})
    check("POST /api/mine", status == 200 and mined.get("cards"),
          f"{mined.get('stats', {}).get('mined')} cards")
    check("cards carry pinyin", all(c.get("target_pinyin") for c in mined.get("cards", [])))

    status, saved = call(base, "/api/save", {
        "text": SAMPLE, "cards": mined["cards"], "mode": "dialogue", "level": 3,
        "title": "Live verify — office"})
    check("POST /api/save", status == 200 and saved.get("text_id"), json.dumps(saved.get("stats", {})))

    status, stats = call(base, "/api/stats")
    check("GET /api/stats", status == 200 and "words" in stats, json.dumps(stats))

    status, words = call(base, "/api/words?filter=new")
    check("GET /api/words", status == 200 and words.get("words"), f"{len(words.get('words', []))} new")
    check("words carry context sentences",
          any(w.get("sentences") for w in words.get("words", [])))

    status, q = call(base, "/api/review?limit=5")
    check("GET /api/review", status == 200 and q.get("queue"), f"{len(q.get('queue', []))} queued")
    if q.get("queue"):
        w = q["queue"][0]["word"]
        status, graded = call(base, "/api/review", {"word": w, "result": "good"})
        check("POST /api/review (good)", status == 200 and graded.get("box") == 1, f"{w} -> box {graded.get('box')}")
        status, graded = call(base, "/api/review", {"word": w, "result": "again"})
        check("POST /api/review (again)", status == 200 and graded.get("box") == 0, f"{w} -> box {graded.get('box')}")

    status, lib = call(base, "/api/library")
    check("GET /api/library", status == 200 and lib.get("texts"), f"{len(lib.get('texts', []))} texts")
    if lib.get("texts"):
        tid = lib["texts"][0]["id"]
        status, one = call(base, f"/api/texts/{tid}")
        check("GET /api/texts/<id>", status == 200 and one.get("targets"))

    status, gen = call(base, "/api/generate", {"count": 10, "level": 3, "turns": 8, "save": True})
    if status == 200:
        d = gen["dialogue"]
        lines = d["body"].splitlines()
        junk = [ln for ln in lines if any(c.isascii() and c.isalnum() for c in ln)]
        check("POST /api/generate", bool(lines) and not junk,
              f"{len(lines)} turns, used {len(d['words_used'])}/{len(d['words_requested'])}, junk={len(junk)}")
        print("\n  generated dialogue:")
        for ln in lines:
            print("   ", ln)
        check("generated text was saved", bool(gen.get("saved", {}).get("text_id")))
    else:
        check("POST /api/generate", False, f"HTTP {status}: {gen.get('error')}")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
