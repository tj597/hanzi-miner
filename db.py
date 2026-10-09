"""Persistence: the word bank, the sentence corpus, and the pasted texts.

Backed by PostgreSQL. On App Platform the connection string arrives as
DATABASE_URL (bound to the app's dev database). A dev database is 512 MiB for
$7/mo and is NOT high-availability or backed up — fine for a single-user
learning tool, wrong for anything that matters.

Review scheduling is a deliberately small SRS: each word has a "box" 0-6 and a
due date. Getting a word right promotes the box and pushes the due date out;
missing it resets the box. That is enough to stop you re-reading words you know
and to surface the ones you keep forgetting.
"""

from __future__ import annotations

import contextlib
import os
import random
from datetime import date, timedelta

import psycopg
from psycopg.rows import dict_row

from pinyin_util import to_pinyin

DATABASE_URL = os.environ.get("DATABASE_URL") or ""

# days until the next review, indexed by box
INTERVALS = {0: 0, 1: 1, 2: 2, 3: 4, 4: 8, 5: 16, 6: 32}
MAX_BOX = 6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS texts (
    id          SERIAL PRIMARY KEY,
    title       TEXT,
    body        TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'dialogue',
    level       INT,
    source      TEXT NOT NULL DEFAULT 'paste',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS words (
    id            SERIAL PRIMARY KEY,
    word          TEXT UNIQUE NOT NULL,
    pinyin        TEXT,
    defs          TEXT,
    in_dict       BOOLEAN NOT NULL DEFAULT true,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    times_seen    INT NOT NULL DEFAULT 1,
    box           INT NOT NULL DEFAULT 0,
    reviews       INT NOT NULL DEFAULT 0,
    lapses        INT NOT NULL DEFAULT 0,
    due_at        DATE NOT NULL DEFAULT CURRENT_DATE,
    last_reviewed TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sentences (
    id        SERIAL PRIMARY KEY,
    target    TEXT NOT NULL,
    sentence  TEXT NOT NULL,
    text_id   INT REFERENCES texts(id) ON DELETE SET NULL,
    UNIQUE (sentence, target)
);

CREATE INDEX IF NOT EXISTS idx_words_due   ON words (due_at);
CREATE INDEX IF NOT EXISTS idx_sent_target ON sentences (target);
"""

_schema_ready = False


def available() -> bool:
    """True when a database is configured. The app degrades gracefully without one."""
    return bool(DATABASE_URL)


@contextlib.contextmanager
def connect():
    """Yield a dict-row connection. Re-runs the (idempotent) schema once per process."""
    global _schema_ready
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        if not _schema_ready:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
            conn.commit()
            _schema_ready = True
        yield conn


def _sentences_for(conn, word: str) -> list[str]:
    cur = conn.execute(
        "SELECT sentence FROM sentences WHERE target = %s ORDER BY length(sentence) LIMIT 3",
        (word,),
    )
    return [r["sentence"] for r in cur.fetchall()]


# ---------------------------------------------------------------- write paths


def save_mined(
    text: str,
    cards: list[dict],
    mode: str = "dialogue",
    level: int = 2,
    title: str | None = None,
    source: str = "paste",
) -> dict:
    """Persist a mining result: the source text, its words, and its sentences.

    Words are upserted — mining a word you already have increments `times_seen`
    instead of duplicating it, and never touches its review schedule.
    """
    if not title:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        title = (first[:16] + "…") if len(first) > 16 else (first or "Untitled")

    new_words = 0
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO texts (title, body, mode, level, source) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (title, text, mode, level, source),
        ).fetchone()
        text_id = row["id"]

        for c in cards:
            word = c["target"]
            defs = "; ".join(c.get("defs") or [])
            res = conn.execute(
                """
                INSERT INTO words (word, pinyin, defs, in_dict)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (word) DO UPDATE SET times_seen = words.times_seen + 1
                RETURNING (xmax = 0) AS inserted
                """,
                (word, c.get("target_pinyin", ""), defs, bool(c.get("in_dict"))),
            ).fetchone()
            if res and res["inserted"]:
                new_words += 1

            conn.execute(
                "INSERT INTO sentences (target, sentence, text_id) VALUES (%s,%s,%s) "
                "ON CONFLICT (sentence, target) DO NOTHING",
                (word, c["sentence"], text_id),
            )
        conn.commit()

    return {"text_id": text_id, "words": len(cards), "new_words": new_words}


def review_word(word: str, result: str) -> dict:
    """Apply a review grade. result: again | good | easy."""
    with connect() as conn:
        cur = conn.execute("SELECT box, reviews FROM words WHERE word = %s", (word,))
        row = cur.fetchone()
        if not row:
            return {"error": "unknown word"}

        box, reviews = row["box"], row["reviews"]
        if result == "again":
            box, lapses_delta = 0, 1
        elif result == "easy":
            box, lapses_delta = min(MAX_BOX, box + 2), 0
        else:  # good
            box, lapses_delta = min(MAX_BOX, box + 1), 0

        due = date.today() + timedelta(days=INTERVALS[box])
        conn.execute(
            """
            UPDATE words
               SET box = %s, due_at = %s, reviews = reviews + 1,
                   lapses = lapses + %s, last_reviewed = now()
             WHERE word = %s
            """,
            (box, due, lapses_delta, word),
        )
        conn.commit()
        return {"word": word, "box": box, "due_at": due.isoformat(), "reviews": reviews + 1}


# ----------------------------------------------------------------- read paths


def list_words(filter: str = "all", q: str = "", limit: int = 200) -> list[dict]:
    """filter: all | due | new | learning | known"""
    where, params = [], []
    if filter == "due":
        where.append("reviews > 0 AND due_at <= CURRENT_DATE")
    elif filter == "new":
        where.append("reviews = 0")
    elif filter == "learning":
        where.append("reviews > 0 AND box < 4")
    elif filter == "known":
        where.append("box >= 4")
    if q:
        where.append("(word ILIKE %s OR defs ILIKE %s OR pinyin ILIKE %s)")
        params += [f"%{q}%"] * 3

    sql = "SELECT * FROM words"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY due_at ASC, times_seen DESC LIMIT %s"
    params.append(limit)

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        for r in rows:
            r["sentences"] = _sentences_for(conn, r["word"])
            r["due_at"] = r["due_at"].isoformat()
            r["first_seen"] = r["first_seen"].isoformat()
        return rows


def review_queue(limit: int = 20) -> list[dict]:
    """New words first, then anything overdue. Each carries its best sentence."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM words
             WHERE reviews = 0 OR due_at <= CURRENT_DATE
             ORDER BY (reviews > 0) ASC, due_at ASC, times_seen DESC
             LIMIT %s
            """,
            (limit,),
        ).fetchall()
        for r in rows:
            sents = _sentences_for(conn, r["word"])
            r["sentence"] = sents[0] if sents else ""
            r["sentence_pinyin"] = to_pinyin(r["sentence"])
            r.pop("id", None)
            r["due_at"] = r["due_at"].isoformat()
            r["first_seen"] = r["first_seen"].isoformat()
            r["last_reviewed"] = r["last_reviewed"].isoformat() if r["last_reviewed"] else None
        return rows


def list_texts(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*, COUNT(s.id) AS sentence_count
              FROM texts t LEFT JOIN sentences s ON s.text_id = t.id
             GROUP BY t.id ORDER BY t.created_at DESC LIMIT %s
            """,
            (limit,),
        ).fetchall()
        for r in rows:
            r["created_at"] = r["created_at"].isoformat()
        return rows


def get_text(text_id: int) -> dict | None:
    with connect() as conn:
        t = conn.execute("SELECT * FROM texts WHERE id = %s", (text_id,)).fetchone()
        if not t:
            return None
        sents = conn.execute(
            "SELECT target, sentence FROM sentences WHERE text_id = %s", (text_id,)
        ).fetchall()
        t["created_at"] = t["created_at"].isoformat()
        t["targets"] = sents
        return t


def stats() -> dict:
    with connect() as conn:
        r = conn.execute(
            """
            SELECT COUNT(*) AS words,
                   COUNT(*) FILTER (WHERE reviews = 0) AS new,
                   COUNT(*) FILTER (WHERE reviews > 0 AND due_at <= CURRENT_DATE) AS due,
                   COUNT(*) FILTER (WHERE box >= 4) AS known
              FROM words
            """
        ).fetchone()
        r["texts"] = conn.execute("SELECT COUNT(*) AS n FROM texts").fetchone()["n"]
        return r


def pick_words(n: int = 15, weak_first: bool = True) -> list[str]:
    """Choose words to build a generated dialogue around: weakest/most overdue first."""
    order = "box ASC, due_at ASC, times_seen DESC" if weak_first else "random()"
    with connect() as conn:
        rows = conn.execute(f"SELECT word FROM words ORDER BY {order} LIMIT %s", (n,)).fetchall()
        return [r["word"] for r in rows]


def _first_sense(defs: str | None) -> str:
    return (defs or "").split(";")[0].strip()


def quiz_queue(limit: int = 20) -> list[dict]:
    """Review queue where each item also carries multiple-choice meanings.

    Distractors are other words' first dictionary sense, sampled from the rest of
    the bank. A bank with too few usable meanings cannot make a real quiz, so each
    item carries `quizable` and the UI falls back to self-grading.
    """
    items = review_queue(limit)
    if not items:
        return items

    with connect() as conn:
        pool = conn.execute(
            "SELECT word, defs FROM words WHERE defs IS NOT NULL AND defs <> '' "
            "ORDER BY random() LIMIT 500"
        ).fetchall()

    senses = [(p["word"], _first_sense(p["defs"])) for p in pool]
    senses = [(w, s) for w, s in senses if s]

    for it in items:
        correct = _first_sense(it.get("defs")) or it["word"]
        others = [s for w, s in senses if w != it["word"] and s != correct]
        random.shuffle(others)
        options = others[:3] + [correct]
        random.shuffle(options)
        it["options"] = options
        it["answer"] = options.index(correct)
        it["answer_text"] = correct
        it["quizable"] = len(options) >= 3
    return items


def delete_word(word: str) -> dict:
    """Remove a word from the bank, together with the sentences recorded for it.

    A deliberate "I already know this" / "this is junk" action — it discards the
    review schedule and the word stops being offered for review or generation.
    Source texts are left alone, so the text it came from is still readable.
    """
    with connect() as conn:
        gone = conn.execute(
            "DELETE FROM words WHERE word = %s RETURNING word", (word,)
        ).fetchone()
        removed = conn.execute(
            "DELETE FROM sentences WHERE target = %s", (word,)
        ).rowcount
        conn.commit()
    return {"deleted": bool(gone), "word": word, "sentences_removed": removed}


def delete_text(text_id: int) -> dict:
    """Remove a saved text from the library.

    The sentences mined from it survive — the foreign key sets their text_id to
    NULL — so words keep their review context. The words are left alone too: a word
    may have come from several texts, and removing it is a separate decision.
    """
    with connect() as conn:
        gone = conn.execute(
            "DELETE FROM texts WHERE id = %s RETURNING id", (text_id,)
        ).fetchone()
        conn.commit()
    return {"deleted": bool(gone), "text_id": text_id}
