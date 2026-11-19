from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from functools import lru_cache
from typing import Any, TypedDict


DEFAULT_TOKENIZER_MODEL = "WUJUNCHAO/DetectRL-X-XLM-RoBERTa-Detector-All"
DEFAULT_MAX_INPUT_TOKENS = 512
TARGET_INPUT_TOKENS = 256
DEFAULT_SHORT_SEGMENT_VISIBLE_CHARS = 40
DETECTABLE_STATUS = "detectable"
TOO_SHORT_STATUS = "too_short"

_SENTENCE_SUFFIX = r"""[”’」』）】》〉"'»)\]}]*"""
_CJK_SENTENCE_BOUNDARY_PATTERN = re.compile(r"[。！？][。！？.!?]*" + _SENTENCE_SUFFIX)
_ENGLISH_SENTENCE_BOUNDARY_PATTERN = re.compile(
    r"(?:(?P<word>\b(?:[a-z]\.)*[a-z]+))?(?P<mark>[.!?]+)"
    + _SENTENCE_SUFFIX
    + r"(?=\s|$)",
    re.IGNORECASE,
)
# shortcut: abbreviations are fixed; use language-specific models when ambiguous sentence boundaries need resolving.
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "e.g", "i.e", "fig", "u.s", "u.k"}


class ParagraphPart(TypedDict):
    index: int
    text: str
    weight: float


class TokenSegment(TypedDict):
    start: int
    end: int
    text: str
    detect_text: str
    visible_chars: int
    token_count: int
    weight: int
    status: str
    truncated: bool
    paragraph_parts: list[ParagraphPart]


@lru_cache(maxsize=4)
def get_tokenizer(model_name: str = DEFAULT_TOKENIZER_MODEL) -> Any:
    from transformers import AutoTokenizer

    try:
        return AutoTokenizer.from_pretrained(model_name, use_fast=True)
    except (ImportError, ValueError):
        return AutoTokenizer.from_pretrained(model_name, use_fast=False)


def count_visible_chars(text: str) -> int:
    return len("".join(str(text or "").split()))


def count_tokens(
    text: str,
    *,
    add_special_tokens: bool = True,
    tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
) -> int:
    tokenizer = get_tokenizer(tokenizer_model)
    return len(tokenizer.encode(str(text or ""), add_special_tokens=add_special_tokens))


def split_sentence_like_chunks(text: str) -> list[str]:
    if not text.strip():
        return []

    ends = {match.end() for match in _CJK_SENTENCE_BOUNDARY_PATTERN.finditer(text)}
    ends.update(_english_sentence_ends(text))
    ends.add(len(text))
    chunks: list[str] = []
    start = 0
    for end in sorted(ends):
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
            start = end
    if start < len(text):
        chunks[-1] += text[start:]
    return chunks


def _english_sentence_ends(text: str) -> list[int]:
    try:
        from nltk.tokenize.punkt import PunktParameters, PunktSentenceTokenizer

        parameters = PunktParameters()
        parameters.abbrev_types = _ABBREVIATIONS
        return [end for _, end in PunktSentenceTokenizer(parameters).span_tokenize(text)]
    except (ImportError, LookupError, ValueError):
        return [
            match.end()
            for match in _ENGLISH_SENTENCE_BOUNDARY_PATTERN.finditer(text)
            if match["mark"] != "." or (match["word"] or "").lower() not in _ABBREVIATIONS
        ]


def build_token_aware_segments(
    paragraphs: list[str],
    *,
    short_visible_chars: int = DEFAULT_SHORT_SEGMENT_VISIBLE_CHARS,
    max_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
) -> list[TokenSegment]:
    text = "\n".join(paragraphs)
    visible_chars = count_visible_chars(text)
    if not visible_chars:
        return []

    paragraph_starts = [0]
    for paragraph in paragraphs[:-1]:
        paragraph_starts.append(paragraph_starts[-1] + len(paragraph) + 1)

    status = TOO_SHORT_STATUS if visible_chars < short_visible_chars else DETECTABLE_STATUS
    ranges = (
        [(0, len(text))]
        if status == TOO_SHORT_STATUS
        else list(_detectable_ranges(text, max_tokens=max_tokens, tokenizer_model=tokenizer_model))
    )
    if len(ranges) >= 2:
        tail_start, tail_end = ranges[-1]
        previous_start, _ = ranges[-2]
        tail_tokens = count_tokens(text[tail_start:tail_end].strip(), tokenizer_model=tokenizer_model)
        if tail_tokens < TARGET_INPUT_TOKENS:
            merged_tokens = count_tokens(text[previous_start:tail_end].strip(), tokenizer_model=tokenizer_model)
            if merged_tokens <= max_tokens:
                ranges[-2:] = [(previous_start, tail_end)]

    segments: list[TokenSegment] = []
    for start, end in ranges:
        original_text = text[start:end]
        detect_text = original_text.strip()
        weight = (
            0
            if status == TOO_SHORT_STATUS
            else max(count_tokens(detect_text, add_special_tokens=False, tokenizer_model=tokenizer_model), 1)
        )
        first_paragraph = bisect_right(paragraph_starts, start) - 1
        last_paragraph = bisect_left(paragraph_starts, end) - 1
        paragraph_parts: list[ParagraphPart] = []
        for index in range(first_paragraph, last_paragraph + 1):
            paragraph_start = paragraph_starts[index]
            part_text = text[max(start, paragraph_start) : min(end, paragraph_start + len(paragraphs[index]))]
            if part_text.strip():
                paragraph_parts.append({"index": index, "text": part_text, "weight": 0.0})

        if weight and len(paragraph_parts) == 1:
            paragraph_parts[0]["weight"] = weight
        elif weight:
            for part in paragraph_parts:
                part["weight"] = max(
                    count_tokens(part["text"].strip(), add_special_tokens=False, tokenizer_model=tokenizer_model),
                    1,
                )
            total_part_weight = sum(part["weight"] for part in paragraph_parts)
            for part in paragraph_parts:
                part["weight"] *= weight / total_part_weight

        segments.append(
            {
                "start": paragraph_parts[0]["index"],
                "end": paragraph_parts[-1]["index"],
                "text": original_text,
                "detect_text": detect_text,
                "visible_chars": count_visible_chars(detect_text),
                "token_count": count_tokens(detect_text, tokenizer_model=tokenizer_model),
                "weight": weight,
                "status": status,
                "truncated": False,
                "paragraph_parts": paragraph_parts,
            }
        )
    return segments


def _detectable_ranges(
    text: str,
    *,
    max_tokens: int,
    tokenizer_model: str,
) -> Iterator[tuple[int, int]]:
    target_tokens = min(TARGET_INPUT_TOKENS, max_tokens)
    start = end = 0
    for sentence in split_sentence_like_chunks(text):
        sentence_end = end + len(sentence)
        candidate_tokens = count_tokens(text[start:sentence_end].strip(), tokenizer_model=tokenizer_model)
        if candidate_tokens > max_tokens and start < end:
            yield start, end
            start = end
            candidate_tokens = count_tokens(sentence.strip(), tokenizer_model=tokenizer_model)
        if candidate_tokens > max_tokens:
            for start, part_end in _split_overlong_sentence(
                text, start, sentence_end, max_tokens=max_tokens, tokenizer_model=tokenizer_model
            ):
                if part_end < sentence_end:
                    yield start, part_end
            candidate_tokens = count_tokens(text[start:sentence_end].strip(), tokenizer_model=tokenizer_model)
        if candidate_tokens >= target_tokens:
            yield start, sentence_end
            start = sentence_end
        end = sentence_end

    if start < end:
        yield start, end


def _split_overlong_sentence(
    text: str,
    start: int,
    end: int,
    *,
    max_tokens: int,
    tokenizer_model: str,
) -> Iterator[tuple[int, int]]:
    while count_tokens(text[start:end].strip(), tokenizer_model=tokenizer_model) > max_tokens:
        low, high = start + 1, end - 1
        split = start
        while low <= high:
            middle = (low + high) // 2
            if count_tokens(text[start:middle].strip(), tokenizer_model=tokenizer_model) <= max_tokens:
                split = middle
                low = middle + 1
            else:
                high = middle - 1
        if not text[start:split].strip():
            raise ValueError("A character exceeds the detector token limit")
        yield start, split
        start = split
    yield start, end
