"""Tests for the miner core. Run: pytest -q  (or python tests/test_miner.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from known import build_known, charwise_known, parse_word_list  # noqa: E402
from miner import clean_lines, mine, units_dialogue, units_prose  # noqa: E402


def test_clean_lines_strips_labels_and_timestamps():
    raw = "小明：你好！\n00:01:23,000\nHello there\n\n小红：再见。"
    lines = clean_lines(raw)
    assert lines == ["你好！", "再见。"], lines


def test_units_dialogue_builds_windows():
    lines = ["甲。", "乙。", "丙。"]
    units = units_dialogue(lines, window=2)
    assert "甲。" in units and "甲。 乙。" in units
    assert "乙。 丙。" in units
    assert "丙。 甲。" not in units  # no wraparound


def test_units_prose_splits_on_punctuation():
    units = units_prose(["第一句。第二句！第三句"])
    assert len(units) == 3


def test_i1_filter_keeps_exactly_one_unknown():
    # Learner knows 我 喜欢 学习 中文 but not 语法.
    known = {"我", "喜欢", "学习", "中文"}
    text = "我喜欢学习中文语法。\n我喜欢学习。\n我喜欢这本书的语法。"
    res = mine(text, known, mode="prose", max_unknown=1)
    targets = {c["target"] for c in res["cards"]}
    assert "语法" in targets
    # 0-unknown sentence and 2-unknown sentence must not produce cards.
    assert len(res["cards"]) == 1, res["cards"]


def test_dedupes_to_one_card_per_target():
    known = {"我", "喜欢", "学习"}
    text = "我喜欢学习语法。\n我喜欢语法。\n学习语法很好。"
    res = mine(text, known, mode="prose", max_unknown=1)
    assert [c["target"] for c in res["cards"]].count("语法") == 1


def test_charwise_known_flag():
    known = {"你", "好", "有", "意", "思"}
    assert charwise_known("你好", known) is True
    assert charwise_known("有意思", known) is True
    assert charwise_known("语法", known) is False


def test_strict_drops_complex_forms():
    known = {"你", "好", "我", "叫", "小明", "喜欢", "学习", "中文"}
    text = "你好，我叫小明。我喜欢学习中文语法。"
    soft = mine(text, known, mode="prose", max_unknown=1, strict=False)
    hard = mine(text, known, mode="prose", max_unknown=1, strict=True)
    assert any(c["target"] == "你好" for c in soft["cards"])
    assert not any(c["target"] == "你好" for c in hard["cards"])
    assert any(c["target"] == "语法" for c in hard["cards"])


def test_parse_word_list_ignores_junk():
    got = parse_word_list("你好, 语法\nenglish word\n  123  ")
    assert got == {"你好", "语法"}


def test_build_known_grows_with_level():
    assert len(build_known(1)) < len(build_known(6))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
