"""End-to-end tests for the app routes, against a real PostgreSQL database.

Requires DATABASE_URL (defaults to a local `hanzi` database):

    . .venv/bin/activate
    dropdb --if-exists hanzi && createdb hanzi
    DATABASE_URL=postgresql://localhost/hanzi DO_INFERENCE_KEY=... python tests/test_app.py

State is seeded once in setup() and every test is independent of run order
(this bit me: the first version relied on alphabetical ordering and half of it
failed when the seed happened later than the assertions).

The generation test is skipped unless DO_INFERENCE_KEY is set.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/hanzi")
os.environ.setdefault("DO_INFERENCE_KEY", "")

import db  # noqa: E402
import app as app_mod  # noqa: E402

SAMPLE = """小明：你好！你今天怎么样？
小红：我很好，谢谢。你今天去哪儿了？
小明：我去图书馆看书了。那里很安静。
小红：你在看什么书？有意思吗？
小明：我在看一本关于中国历史的书。虽然有点儿难，但是很有趣。
小红：真的吗？我也很喜欢历史。我可以借你的书看看吗？
小明：当然可以。不过我还没看完，你等我两天行吗？
小红：没问题。对了，下周末你打算做什么？
小明：我想去爬山。天气预报说那天会晴天。
小红：听起来不错！如果我有时间，我也可以一起去吗？
小明：太好了！我们早上七点在公园门口见面吧。
小红：好的，别忘了带水和一点吃的。
小明：放心吧，我会准备好的。回头见！
"""

client = app_mod.app.test_client()
SEED: dict = {}

# pypinyin emits tone-marked vowels; a pinyin string has those or plain Latin.
_TONE = set("āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜÜü")


def _looks_like_pinyin(s: str) -> bool:
    return bool(s) and (
        any(c.isascii() and c.isalpha() for c in s) or any(c in _TONE for c in s)
    )


def _j(resp):
    return resp.get_json()


def seed():
    """Wipe the schema and save the sample once, so tests start from known state."""
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS sentences, words, texts CASCADE")
        conn.commit()
    db._schema_ready = False  # force the next connect() to recreate the schema

    mined = _j(client.post("/api/mine", json={"text": SAMPLE, "mode": "dialogue", "level": 3}))
    assert mined["cards"], "seed mining produced nothing"
    saved = _j(client.post("/api/save", json={
        "text": SAMPLE, "cards": mined["cards"], "mode": "dialogue", "level": 3,
        "title": "Library chat"}))
    SEED["mined"] = mined
    SEED["saved"] = saved
    SEED["words"] = [c["target"] for c in mined["cards"]]


# ------------------------------------------------------------------ mining


def test_mine_returns_pinyin_for_every_card():
    r = _j(client.post("/api/mine", json={"text": SAMPLE, "mode": "dialogue", "level": 3}))
    assert r["stats"]["mined"] > 0, r
    assert all(c["target_pinyin"] for c in r["cards"]), "pinyin missing"


def test_empty_input_guards():
    assert client.post("/api/save", json={"text": "", "cards": []}).status_code == 400
    assert client.post("/api/deck", json={"text": ""}).status_code == 400


def test_deck_download_is_a_valid_apkg():
    res = client.post("/api/deck", json={"text": SAMPLE, "mode": "dialogue", "level": 3})
    assert res.status_code == 200
    data = res.get_data()
    assert data[:2] == b"PK", "apkg must be a zip"
    import io
    import sqlite3
    import tempfile
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    name = [n for n in z.namelist() if n.endswith(".anki2")][0]
    path = tempfile.mktemp()
    open(path, "wb").write(z.read(name))
    n = sqlite3.connect(path).execute("select count(*) from notes").fetchone()[0]
    assert n == SEED["mined"]["stats"]["mined"], (n, SEED["mined"]["stats"])


# ------------------------------------------------------------------ save / bank


def test_seed_saved_all_words_as_new():
    assert SEED["saved"]["words"] == len(SEED["words"])
    assert SEED["saved"]["new_words"] == SEED["saved"]["words"], SEED["saved"]


def test_stats_match_the_saved_words():
    """Order-independent: other tests legitimately add words, so assert containment."""
    s = _j(client.get("/api/stats"))
    assert s["words"] >= len(SEED["words"]), s
    assert s["texts"] >= 1
    bank = [w["word"] for w in _j(client.get("/api/words?limit=500"))["words"]]
    missing = set(SEED["words"]) - set(bank)
    assert not missing, f"seeded words missing from the bank: {missing}"


def test_resaving_does_not_duplicate_words():
    before = _j(client.get("/api/stats"))["words"]
    mined = _j(client.post("/api/mine", json={"text": SAMPLE, "mode": "dialogue", "level": 3}))
    saved = _j(client.post("/api/save", json={
        "text": SAMPLE, "cards": mined["cards"], "mode": "dialogue", "level": 3}))
    after = _j(client.get("/api/stats"))["words"]
    assert saved["new_words"] == 0, saved
    assert after == before, (before, after)


def test_word_filters_and_search():
    new = _j(client.get("/api/words?filter=new"))["words"]
    assert new, "expected new words"
    assert all(w["reviews"] == 0 for w in new)
    assert any(w["sentences"] for w in new), "words should carry their context sentence"

    target = new[0]["word"]
    hit = _j(client.get(f"/api/words?q={target}"))["words"]
    assert any(w["word"] == target for w in hit)


# ------------------------------------------------------------------ review


def test_review_flow_reschedules_and_lapses():
    queue = _j(client.get("/api/review?limit=5"))["queue"]
    assert queue, "queue should contain the new words"
    assert queue[0]["sentence"], "a review card needs a sentence for context"

    word = queue[0]["word"]
    good = _j(client.post("/api/review", json={"word": word, "result": "good"}))
    assert good["box"] == 1 and good["reviews"] == 1, good
    assert word not in [w["word"] for w in _j(client.get("/api/words?filter=due"))["words"]], \
        "a word graded 'good' is not due again today"

    again = _j(client.post("/api/review", json={"word": word, "result": "again"}))
    assert again["box"] == 0, again
    assert word in [w["word"] for w in _j(client.get("/api/words?filter=due"))["words"]], \
        "a lapsed word must come back up"

    assert client.post("/api/review", json={"word": word, "result": "nonsense"}).status_code == 400
    assert client.post("/api/review", json={"word": "不存在", "result": "good"}).status_code == 404


# ------------------------------------------------------------------ library


def test_library_lists_texts_and_single_text_loads():
    lib = _j(client.get("/api/library"))
    assert lib["texts"], "library should list saved texts"
    tid = lib["texts"][0]["id"]
    one = _j(client.get(f"/api/texts/{tid}"))
    assert one["body"].strip()
    assert one["targets"], "a text should record the words mined from it"
    assert client.get("/api/texts/999999").status_code == 404


# ------------------------------------------------------------------ generate


def test_generate_uses_bank_words_and_saves():
    if not os.environ.get("DO_INFERENCE_KEY"):
        print("     (skipped: DO_INFERENCE_KEY not set)")
        return
    import generate as gen_mod

    words = db.pick_words(n=10)
    assert words, "word bank should be populated by the seed"

    out = gen_mod.generate_dialogue(words=words, level=3, turns=8)
    assert out["body"], out
    assert len(out["words_used"]) >= max(3, len(words) // 2), out

    r = _j(client.post("/api/generate", json={"count": 8, "level": 3, "turns": 8, "save": True}))
    assert r["dialogue"]["body"], r
    assert r["saved"] and r["saved"]["text_id"], r.get("saved")
    assert r["mined"] and r["mined"]["stats"]["lines"] > 0
    lines = r["dialogue"]["lines"]
    assert lines, "generated dialogue should expose per-line rows"
    assert all(l["speaker"] for l in lines), "every generated turn has a speaker"
    assert all(_looks_like_pinyin(l["pinyin"]) for l in lines), lines[0]


def test_review_cards_carry_sentence_pinyin():
    q = _j(client.get("/api/review?limit=5"))["queue"]
    assert q
    with_sent = [c for c in q if c["sentence"]]
    assert with_sent, "review cards should carry a context sentence"
    assert all(_looks_like_pinyin(c["sentence_pinyin"]) for c in with_sent), with_sent[0]


def test_quiz_mode_builds_multiple_choice():
    r = _j(client.get("/api/review?limit=5&mode=quiz"))
    assert r["mode"] == "quiz"
    assert r["queue"], "quiz queue should not be empty"
    assert r["quizable"], "the seeded bank is big enough for 4 options"
    for c in r["queue"]:
        assert len(c["options"]) == 4, c
        assert len(set(c["options"])) == 4, f"options must be distinct: {c['options']}"
        assert c["options"][c["answer"]] == c["answer_text"]
        assert c["sentence_pinyin"], "quiz cards keep the pinyin hint too"


def test_default_review_mode_is_self_graded():
    r = _j(client.get("/api/review?limit=3"))
    assert r["mode"] == "self"
    assert "quizable" not in r


def test_text_lines_include_pinyin():
    lib = _j(client.get("/api/library"))
    assert lib["texts"]
    t = _j(client.get(f"/api/texts/{lib['texts'][0]['id']}"))
    assert t["lines"], "a text should expose per-line rows"
    assert all("text" in l and "pinyin" in l for l in t["lines"])
    assert _looks_like_pinyin(" ".join(l["pinyin"] for l in t["lines"]))


def test_delete_word_removes_it_and_its_sentences():
    # Make our OWN word so the seeded words stay put for the other tests.
    # Level 3 matters: at level 2 this sentence has TWO unknown words (中文 and
    # 语法), so it yields no i+1 card at all.
    text = "我喜欢学习中文语法。"
    mined = _j(client.post("/api/mine", json={"text": text, "mode": "prose", "level": 3}))
    assert mined["cards"], mined
    _j(client.post("/api/save", json={"text": text, "cards": mined["cards"],
                                      "mode": "prose", "level": 3, "title": "delete-test"}))
    word = mined["cards"][0]["target"]
    before = _j(client.get("/api/stats"))["words"]
    assert any(w["word"] == word for w in _j(client.get(f"/api/words?q={word}"))["words"])

    r = _j(client.post("/api/words/delete", json={"word": word}))
    assert r["deleted"] and r["word"] == word, r
    assert r["sentences_removed"] >= 1, r
    assert _j(client.get("/api/stats"))["words"] == before - 1
    assert not any(w["word"] == word for w in _j(client.get(f"/api/words?q={word}"))["words"])

    # re-deleting is a clean 404, and a missing word is a 400 — not a 500
    assert client.post("/api/words/delete", json={"word": word}).status_code == 404
    assert client.post("/api/words/delete", json={"word": ""}).status_code == 400


def test_clean_drops_reasoning_and_self_corrections():
    """generate._clean is pure — test it directly, no network needed.

    Every rule here exists because a real generation leaked the thing it rejects.
    """
    import generate as gen_mod

    raw = """<dialogue>
Let me think about which HSK level 语法 belongs to. 语法 is HSK 4? Let me check.
小明：跟我还客气什么，==(改：跟我还客气什么，今晚请你吃饭。)
小红：好啊，那我就不客气了！
小明：Turn1：这行是推理过程，不该保留。
小红：   明天见！
</dialogue>
trailing prose outside the tags 也应该被忽略"""
    got = gen_mod._clean(raw, "小明", "小红", max_turns=10)
    assert got == "小红：好啊，那我就不客气了！\n小红：明天见！", repr(got)


def test_clean_caps_turns_and_dedupes_repeats():
    import generate as gen_mod

    # Chinese numerals, not 1..8: Arabic digits are rejected by design, so a test
    # using them would silently assert against an empty result.
    cn = "一二三四五六七八"
    body = "\n".join(f"小明：这是第{c}句话，测试用的。" for c in cn)
    out = gen_mod._clean(body + "\n" + body, "小明", "小红", max_turns=5)
    assert len(out.splitlines()) == 5, out

    # a line repeated back-to-back (a model emitting the same turn twice) collapses
    dup = gen_mod._clean("小明：你好！\n小明：你好！\n小红：再见。", "小明", "小红")
    assert dup.count("小明：你好！") == 1, dup


if __name__ == "__main__":
    seed()
    print(f"seeded: {len(SEED['words'])} words -> {SEED['words']}\n")
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{passed}/{len(fns)} passed")
    raise SystemExit(0 if passed == len(fns) else 1)
