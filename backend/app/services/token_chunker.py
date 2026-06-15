from __future__ import annotations

import re
from functools import lru_cache
from typing import Any


DEFAULT_TOKENIZER_MODEL = "WUJUNCHAO/DetectRL-X-XLM-RoBERTa-Detector-All"
DEFAULT_MAX_INPUT_TOKENS = 512
DEFAULT_SHORT_SEGMENT_VISIBLE_CHARS = 40
DETECTABLE_STATUS = "detectable"
TOO_SHORT_STATUS = "too_short"

_SENTENCE_BOUNDARY_PATTERN = re.compile(
    r".+?(?:[\u3002\uff01\uff1f]+|[.!?]+)(?:\s+|$)|.+?$",
    re.S,
)
_CJK_SENTENCE_BOUNDARY_PATTERN = re.compile(r"[\u3002\uff01\uff1f]")


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


def truncate_to_token_limit(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
) -> str:
    tokenizer = get_tokenizer(tokenizer_model)
    token_ids = tokenizer.encode(
        str(text or ""),
        add_special_tokens=True,
        truncation=True,
        max_length=max_tokens,
    )
    decoded = tokenizer.decode(
        token_ids,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return decoded.strip() or str(text or "")


def split_sentence_like_chunks(text: str) -> list[str]:
    normalized = str(text or "")
    if not normalized.strip():
        return []

    nltk_chunks = _split_with_nltk(normalized)
    if len(nltk_chunks) > 1 and not _CJK_SENTENCE_BOUNDARY_PATTERN.search(normalized):
        return nltk_chunks

    regex_chunks = [
        match.group(0)
        for match in _SENTENCE_BOUNDARY_PATTERN.finditer(normalized)
        if match.group(0).strip()
    ]
    return regex_chunks or nltk_chunks or [normalized]


def _split_with_nltk(text: str) -> list[str]:
    try:
        from nltk.tokenize import PunktSentenceTokenizer
    except ImportError:
        return []

    tokenizer = PunktSentenceTokenizer()
    try:
        spans = list(tokenizer.span_tokenize(text))
    except (LookupError, ValueError):
        return []

    if not spans:
        return []

    chunks: list[str] = []
    cursor = 0
    for start, end in spans:
        adjusted_start = min(cursor, start)
        chunk = text[adjusted_start:end]
        if chunk.strip():
            chunks.append(chunk)
        cursor = end

    if cursor < len(text):
        tail = text[cursor:]
        if tail.strip():
            if chunks:
                chunks[-1] += tail
            else:
                chunks.append(tail)

    return chunks


def build_token_aware_segments(
    paragraphs: list[str],
    *,
    short_visible_chars: int = DEFAULT_SHORT_SEGMENT_VISIBLE_CHARS,
    max_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
) -> list[dict[str, int | str | bool]]:
    segments: list[dict[str, int | str | bool]] = []

    for paragraph_index, paragraph in enumerate(paragraphs):
        paragraph_text = str(paragraph or "")
        if not paragraph_text.strip():
            continue

        visible_chars = count_visible_chars(paragraph_text)
        if visible_chars < short_visible_chars:
            segments.append(
                _make_segment(
                    text=paragraph_text,
                    paragraph_index=paragraph_index,
                    visible_chars=visible_chars,
                    token_count=count_tokens(paragraph_text, tokenizer_model=tokenizer_model),
                    weight=0,
                    status=TOO_SHORT_STATUS,
                )
            )
            continue

        token_count = count_tokens(paragraph_text, tokenizer_model=tokenizer_model)
        if token_count <= max_tokens:
            segments.append(
                _make_segment(
                    text=paragraph_text,
                    paragraph_index=paragraph_index,
                    visible_chars=visible_chars,
                    token_count=token_count,
                    weight=max(count_tokens(paragraph_text, add_special_tokens=False, tokenizer_model=tokenizer_model), 1),
                    status=DETECTABLE_STATUS,
                )
            )
            continue

        segments.extend(
            _split_oversized_paragraph_by_tokens(
                paragraph_text,
                paragraph_index,
                max_tokens=max_tokens,
                tokenizer_model=tokenizer_model,
            )
        )

    return segments


def _split_oversized_paragraph_by_tokens(
    paragraph: str,
    paragraph_index: int,
    *,
    max_tokens: int,
    tokenizer_model: str,
) -> list[dict[str, int | str | bool]]:
    segments: list[dict[str, int | str | bool]] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return

        text = "".join(buffer)
        if text.strip():
            segments.append(
                _make_detectable_segment(
                    text=text,
                    paragraph_index=paragraph_index,
                    tokenizer_model=tokenizer_model,
                )
            )
        buffer = []

    for sentence in split_sentence_like_chunks(paragraph):
        sentence_token_count = count_tokens(sentence, tokenizer_model=tokenizer_model)
        if sentence_token_count > max_tokens:
            flush_buffer()
            detect_text = truncate_to_token_limit(sentence, max_tokens=max_tokens, tokenizer_model=tokenizer_model)
            segments.append(
                _make_detectable_segment(
                    text=sentence,
                    paragraph_index=paragraph_index,
                    tokenizer_model=tokenizer_model,
                    detect_text=detect_text,
                    truncated=True,
                    original_token_count=sentence_token_count,
                )
            )
            continue

        candidate = "".join([*buffer, sentence])
        candidate_token_count = count_tokens(candidate, tokenizer_model=tokenizer_model)
        if buffer and candidate_token_count > max_tokens:
            flush_buffer()

        buffer.append(sentence)

    flush_buffer()
    return segments


def _make_detectable_segment(
    *,
    text: str,
    paragraph_index: int,
    tokenizer_model: str,
    detect_text: str | None = None,
    truncated: bool = False,
    original_token_count: int | None = None,
) -> dict[str, int | str | bool]:
    text_for_detect = detect_text if detect_text is not None else text
    return _make_segment(
        text=text,
        paragraph_index=paragraph_index,
        visible_chars=count_visible_chars(text),
        token_count=count_tokens(text_for_detect, tokenizer_model=tokenizer_model),
        weight=max(count_tokens(text_for_detect, add_special_tokens=False, tokenizer_model=tokenizer_model), 1),
        status=DETECTABLE_STATUS,
        detect_text=text_for_detect,
        truncated=truncated,
        original_token_count=original_token_count,
    )


def _make_segment(
    *,
    text: str,
    paragraph_index: int,
    visible_chars: int,
    token_count: int,
    weight: int,
    status: str,
    detect_text: str | None = None,
    truncated: bool = False,
    original_token_count: int | None = None,
) -> dict[str, int | str | bool]:
    segment: dict[str, int | str | bool] = {
        "start": paragraph_index,
        "end": paragraph_index,
        "text": text,
        "visible_chars": visible_chars,
        "token_count": token_count,
        "weight": weight,
        "status": status,
        "truncated": truncated,
    }
    if detect_text is not None:
        segment["detect_text"] = detect_text
    if original_token_count is not None:
        segment["original_token_count"] = original_token_count
    return segment
