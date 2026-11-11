import sys

import pytest

from app.services import token_chunker


@pytest.fixture
def character_tokenizer(monkeypatch):
    class CharacterTokenizer:
        def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
            return [0] * (sum(not char.isspace() for char in text) + 2 * add_special_tokens)

    monkeypatch.setattr(token_chunker, "get_tokenizer", lambda _: CharacterTokenizer())


@pytest.mark.parametrize(
    ("second_size", "expected_counts"),
    [(129, [256, 256]), (128, [509])],
)
def test_flushes_at_first_sentence_end_at_or_above_target(character_tokenizer, second_size, expected_counts):
    paragraphs = ["甲" * 124 + "。", "乙" * (second_size - 1) + "！" + "尾" * 253 + "。"]
    segments = token_chunker.build_token_aware_segments(paragraphs)

    assert [segment["token_count"] for segment in segments] == expected_counts
    assert all(segment["status"] == token_chunker.DETECTABLE_STATUS for segment in segments)
    assert "".join(segment["text"] for segment in segments) == "\n".join(paragraphs)


def test_crosses_paragraph_break_inside_sentence(character_tokenizer):
    paragraphs = ["甲" * 260, "乙。尾。"]
    segments = token_chunker.build_token_aware_segments(paragraphs)
    assert len(segments) == 1
    first = segments[0]

    assert first["detect_text"] == "\n".join(paragraphs)
    assert first["token_count"] == 266
    assert (first["start"], first["end"]) == (0, 1)
    assert [(part["index"], part["text"]) for part in first["paragraph_parts"]] == list(enumerate(paragraphs))
    assert sum(part["weight"] for part in first["paragraph_parts"]) == pytest.approx(first["weight"])


@pytest.mark.parametrize(
    ("sentence_sizes", "expected_counts"),
    [
        ([254, 2], [258]),
        ([300, 210], [512]),
        ([301, 210], [303, 212]),
        ([254, 254], [256, 256]),
        ([80], [82]),
        ([254, 254, 20], [256, 276]),
    ],
)
def test_short_tail_merge_respects_target_and_hard_limit(character_tokenizer, sentence_sizes, expected_counts):
    sentences = ["甲" * (size - 1) + "。" for size in sentence_sizes]
    text = "".join(sentences)
    segments = token_chunker.build_token_aware_segments([text])

    assert [segment["token_count"] for segment in segments] == expected_counts
    assert "".join(segment["text"] for segment in segments) == text
    assert all(segment["weight"] == segment["token_count"] - 2 for segment in segments)


def test_tail_merge_retokenizes_combined_text(monkeypatch):
    first = "甲" * 299 + "。"
    tail = "乙" * 209 + "。"

    class BoundaryTokenizer:
        def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
            body_count = len(text.strip()) + (1 if "。乙" in text else 0)
            return [0] * (body_count + 2 * add_special_tokens)

    monkeypatch.setattr(token_chunker, "get_tokenizer", lambda _: BoundaryTokenizer())
    segments = token_chunker.build_token_aware_segments([first + tail])

    assert [segment["detect_text"] for segment in segments] == [first, tail]
    assert [segment["token_count"] for segment in segments] == [302, 212]
    assert token_chunker.count_tokens(first + tail) == 513


def test_flushes_before_sentence_that_would_exceed_hard_limit(character_tokenizer):
    first = "甲" * 199 + "。"
    second = "乙" * 319 + "。"
    segments = token_chunker.build_token_aware_segments([first + second])

    assert [segment["token_count"] for segment in segments] == [202, 322]
    assert [segment["detect_text"] for segment in segments] == [first, second]


def test_splits_overlong_sentence_without_losing_text(character_tokenizer):
    text = "序言。" + "长" * 1030 + "。尾"
    segments = token_chunker.build_token_aware_segments([text])

    assert [segment["token_count"] for segment in segments] == [5, 512, 512, 14]
    assert "".join(segment["text"] for segment in segments) == text
    assert all(not segment["truncated"] for segment in segments)


def test_accumulates_short_fragment_after_overlong_sentence(character_tokenizer):
    text = "甲" * 598 + "。" + "乙" * 170 + "。尾。"
    segments = token_chunker.build_token_aware_segments([text])

    assert [segment["token_count"] for segment in segments] == [512, 264]
    assert "".join(segment["text"] for segment in segments) == text


@pytest.mark.parametrize("force_fallback", [True, False], ids=["regex", "nltk"])
def test_keeps_decimal_abbreviation_and_sentence_end_quotes(monkeypatch, force_fallback):
    if force_fallback:
        monkeypatch.setitem(sys.modules, "nltk.tokenize.punkt", None)
    else:
        pytest.importorskip("nltk.tokenize.punkt")
    text = 'Dr. Lee paid $3.14. "Fine!" 他说「可以。」下一句！'

    assert token_chunker.split_sentence_like_chunks(text) == [
        "Dr. Lee paid $3.14.",
        ' "Fine!"',
        " 他说「可以。」",
        "下一句！",
    ]
