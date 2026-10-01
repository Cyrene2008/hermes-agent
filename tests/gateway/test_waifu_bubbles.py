"""Tests for the Waifu-Mode breath splitter (气泡节奏／分句).

Contract under test — one natural sentence / one breath per bubble, target roughly 5–45 chars:
  * a TEXT atom over ``WAIFU_BUBBLE_SENTENCE_MAX`` (45) is cut at sentence enders, enders kept;
  * an atom at or under 45 is left byte-for-byte alone;
  * fenced code and tables are never cut (the atom guards win over the length trigger);
  * a single sentence over ``WAIFU_BUBBLE_SENTENCE_HARD_MAX`` (60) falls back to commas, then
    whitespace; a sentence with neither stays whole and the platform cap is the last net;
  * the explicit ``---`` mode stays fully model-controlled (no breath splitting at all);
  * inline code spans and still-open quotes are opaque — their enders are not breath points;
  * sub-``WAIFU_BUBBLE_MIN`` sentences fold into the previous bubble when the pair still fits;
  * the platform-cap re-chunk still runs last, and the emoji/punctuation helpers are unaffected.
"""

from __future__ import annotations

from gateway.platforms.helpers import (
    WAIFU_BUBBLE_MIN,
    WAIFU_BUBBLE_SENTENCE_HARD_MAX,
    WAIFU_BUBBLE_SENTENCE_MAX,
    WAIFU_EMOJI_TOKEN_RE,
    split_waifu_bubbles,
    strip_waifu_trailing_punctuation,
    waifu_emoji_send,
)

#: 50 chars, six sentences each 8–9 — the shape the native mode used to ship as ONE bubble.
LONG_CJK = "。".join([
    "今天真的好开心呀", "我们一起去公园玩", "晚上要吃火锅哦",
    "明天还要上班呢", "周末再出去玩吧", "记得早点休息呀"]) + "。"

#: Fenced code / table with enders INSIDE them, so a missing guard would visibly tear them apart.
LONG_FENCE = ("```py\n"
              "x = 1  # 这是很长很长很长很长很长很长的一行注释。后面还有很长很长很长很多的一句话。\n"
              "```")
LONG_TABLE = "| 列一 | 列二 |\n| --- | --- |\n| 数据数据数据。数据数据数据数据 | 值 |"


def test_long_paragraph_splits_into_sentence_breaths():
    assert len(LONG_CJK) > WAIFU_BUBBLE_SENTENCE_MAX
    bubbles = split_waifu_bubbles(LONG_CJK)
    assert bubbles == [
        "今天真的好开心呀。", "我们一起去公园玩。", "晚上要吃火锅哦。",
        "明天还要上班呢。", "周末再出去玩吧。", "记得早点休息呀。"]
    assert all(WAIFU_BUBBLE_MIN <= len(b) <= WAIFU_BUBBLE_SENTENCE_MAX for b in bubbles)
    assert "".join(bubbles) == LONG_CJK


def test_short_paragraph_is_left_untouched():
    assert split_waifu_bubbles("好呀。") == ["好呀。"]
    short = "短句一。短句二。"
    assert len(short) <= WAIFU_BUBBLE_SENTENCE_MAX
    assert split_waifu_bubbles(short) == [short]


def test_fence_and_table_atoms_are_never_breath_split():
    assert len(LONG_FENCE) > WAIFU_BUBBLE_SENTENCE_MAX
    assert len(LONG_TABLE) > WAIFU_BUBBLE_SENTENCE_MAX
    assert split_waifu_bubbles(
        LONG_FENCE + "\n\n" + LONG_TABLE) == [LONG_FENCE, LONG_TABLE]


def test_delimiter_mode_is_model_controlled():
    first = "第一句话很长。" * 8
    second = "第二句话也很长。" * 7
    assert len(first) > WAIFU_BUBBLE_SENTENCE_MAX
    assert len(second) > WAIFU_BUBBLE_SENTENCE_MAX
    # Both are long enough to be breath-split — but the explicit delimiter wins and nothing is cut.
    assert len(split_waifu_bubbles(first + second)) > 2   # without the delimiter it DOES split
    assert split_waifu_bubbles(first + "\n---\n" + second) == [first, second]


def test_single_long_sentence_falls_back_to_commas():
    sentence = "，".join(["这是一段很长的文字"] * 7)
    assert len(sentence) > WAIFU_BUBBLE_SENTENCE_HARD_MAX
    assert not any(c in sentence for c in "。！？!?；;…")  # no ender to cut at
    bubbles = split_waifu_bubbles(sentence)
    assert len(bubbles) == 7
    assert all(len(b) <= WAIFU_BUBBLE_SENTENCE_HARD_MAX for b in bubbles)
    assert all(b.endswith("，") for b in bubbles[:-1])
    assert bubbles[-1] == "这是一段很长的文字"
    assert "".join(bubbles) == sentence


def test_long_sentence_without_commas_falls_back_to_whitespace():
    sentence = " ".join(["hello"] * 20)
    assert len(sentence) > WAIFU_BUBBLE_SENTENCE_HARD_MAX
    bubbles = split_waifu_bubbles(sentence)
    assert len(bubbles) == 20
    assert all(WAIFU_BUBBLE_MIN <= len(b) <= WAIFU_BUBBLE_SENTENCE_HARD_MAX for b in bubbles)


def test_unbreakable_blob_has_no_breath_and_stays_whole():
    blob = "a" * 100                       # no ender, no comma, no whitespace → nothing to cut
    assert split_waifu_bubbles(blob) == [blob]


def test_multi_sentence_cjk_and_latin_are_both_handled():
    assert len(split_waifu_bubbles(LONG_CJK)) == 6
    latin = "Hello there my friend! How are you doing today? I am doing great, thanks for asking."
    bubbles = split_waifu_bubbles(latin)
    assert bubbles == [
        "Hello there my friend!",
        "How are you doing today?",
        "I am doing great, thanks for asking."]
    assert all(WAIFU_BUBBLE_MIN <= len(b) <= WAIFU_BUBBLE_SENTENCE_MAX for b in bubbles)


def test_inline_code_and_open_quote_enders_are_not_breaths():
    text = ("他先说了一句`code。still code`然后继续往下说。"
            "接着又引用了「引用里有句号。这里」然后结束。")
    assert len(text) > WAIFU_BUBBLE_SENTENCE_MAX
    bubbles = split_waifu_bubbles(text)
    assert bubbles == [
        "他先说了一句`code。still code`然后继续往下说。",
        "接着又引用了「引用里有句号。这里」然后结束。"]
    assert "`code。still code`" in bubbles[0]
    assert "「引用里有句号。这里」" in bubbles[1]


def test_sub_min_sentence_folds_into_previous_bubble():
    text = "嗯。" + LONG_CJK              # the 2-char opener is under MIN and folds forward-safe
    assert len(text) > WAIFU_BUBBLE_SENTENCE_MAX
    bubbles = split_waifu_bubbles(text)
    assert bubbles[0] == "嗯。今天真的好开心呀。"
    assert len(bubbles) == 6
    assert all(WAIFU_BUBBLE_MIN <= len(b) <= WAIFU_BUBBLE_SENTENCE_MAX for b in bubbles)


def test_platform_cap_rechunk_remains_the_final_net():
    blob = "a" * 100
    bubbles = split_waifu_bubbles(blob, max_length=20)
    assert all(len(b) <= 20 for b in bubbles)
    assert "".join(bubbles) == blob


def test_emoji_token_and_punctuation_helpers_are_unaffected():
    assert WAIFU_EMOJI_TOKEN_RE.findall("[emoji:happy] hi [emoji:sad]") == ["happy", "sad"]
    assert waifu_emoji_send("[emoji:happy] 好呀", {"happy": "/x.png"}) == ("/x.png", "好呀")
    assert waifu_emoji_send("没有表情", {"happy": "/x.png"}) == (None, "没有表情")
    assert strip_waifu_trailing_punctuation("好呀。") == "好呀"
    assert strip_waifu_trailing_punctuation("真的吗？") == "真的吗？"

    text = "今天真的好开心呀。[emoji:happy]我们一起去公园玩。晚上要吃火锅哦。明天还要上班呢。"
    assert len(text) > WAIFU_BUBBLE_SENTENCE_MAX
    bubbles = split_waifu_bubbles(text)
    assert "".join(bubbles) == text                 # nothing torn, token included
    tagged = [b for b in bubbles if "[emoji:happy]" in b]
    assert tagged == ["[emoji:happy]我们一起去公园玩。"]
