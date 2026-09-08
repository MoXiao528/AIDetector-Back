"""Frozen Evidence V1 feature extraction, migrated from research src/features.py.

Source: AIDetector-evidence-research at 074fe307108d3ba3e3826062be836800cbd61ec3.
Extraction algorithms and language resources are unchanged. The JSON normalizer
below adapts build_detectrl_x_features.normalize_features without importing Arrow.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

import jieba
import numpy as np


FEATURE_SCHEMA_VERSION = 1

EN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
ZH_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
INLINE_CITATION_RE = re.compile(
    r"(?:\[[0-9,;\-– ]+\]|\([^()]{1,50}\b(?:19|20)\d{2}[a-z]?\))"
)
NUMBER_RE = re.compile(r"\b\d+(?:[.,:/-]\d+)*\b")
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])(?:[\"'”’）】》]*)\s+")
ZH_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?])")
EN_TRANSITIONS = frozenset(
    {
        "however",
        "therefore",
        "moreover",
        "furthermore",
        "consequently",
        "nevertheless",
        "additionally",
        "firstly",
        "secondly",
        "finally",
        "in conclusion",
        "on the other hand",
        "for example",
        "for instance",
        "as a result",
    }
)
ZH_TRANSITIONS = frozenset(
    {
        "然而",
        "因此",
        "此外",
        "而且",
        "同时",
        "首先",
        "其次",
        "最后",
        "综上",
        "总之",
        "另一方面",
        "例如",
        "比如",
        "由此可见",
    }
)
AR_TRANSITIONS = frozenset(
    {
        "لكن",
        "مع ذلك",
        "لذلك",
        "بالتالي",
        "علاوة على ذلك",
        "إضافة إلى ذلك",
        "رغم ذلك",
        "فضلا عن ذلك",
        "أولا",
        "ثانيا",
        "أخيرا",
        "في الختام",
        "من ناحية أخرى",
        "من جهة أخرى",
        "على سبيل المثال",
        "مثلا",
    }
)
FR_TRANSITIONS = frozenset(
    {
        "cependant",
        "donc",
        "de plus",
        "en outre",
        "par conséquent",
        "néanmoins",
        "également",
        "premièrement",
        "deuxièmement",
        "enfin",
        "en conclusion",
        "d'autre part",
        "en revanche",
        "par exemple",
        "ainsi",
        "de ce fait",
    }
)
DE_TRANSITIONS = frozenset(
    {
        "jedoch",
        "daher",
        "darüber hinaus",
        "ferner",
        "folglich",
        "dennoch",
        "zusätzlich",
        "erstens",
        "zweitens",
        "schliesslich",
        "abschliessend",
        "zusammenfassend",
        "andererseits",
        "zum beispiel",
        "beispielsweise",
        "infolgedessen",
        "aus diesem grund",
    }
)
PT_TRANSITIONS = frozenset(
    {
        "no entanto",
        "portanto",
        "além disso",
        "ademais",
        "consequentemente",
        "contudo",
        "adicionalmente",
        "primeiramente",
        "em primeiro lugar",
        "em segundo lugar",
        "finalmente",
        "em conclusão",
        "por outro lado",
        "por exemplo",
        "como resultado",
    }
)
RU_TRANSITIONS = frozenset(
    {
        "однако",
        "поэтому",
        "кроме того",
        "более того",
        "следовательно",
        "тем не менее",
        "дополнительно",
        "во-первых",
        "во-вторых",
        "наконец",
        "в заключение",
        "с другой стороны",
        "например",
        "в результате",
        "таким образом",
    }
)
ES_TRANSITIONS = frozenset(
    {
        "sin embargo",
        "por lo tanto",
        "además",
        "asimismo",
        "en consecuencia",
        "no obstante",
        "adicionalmente",
        "en primer lugar",
        "en segundo lugar",
        "finalmente",
        "en conclusión",
        "por otro lado",
        "por ejemplo",
        "como resultado",
        "por consiguiente",
    }
)

EN_REFERENCE_HEADINGS = frozenset({"references", "bibliography", "works cited"})
ZH_REFERENCE_HEADINGS = frozenset({"参考文献", "引用文献"})
AR_REFERENCE_HEADINGS = frozenset({"المراجع", "قائمة المراجع", "المصادر والمراجع"})
FR_REFERENCE_HEADINGS = frozenset(
    {
        "références",
        "références bibliographiques",
        "bibliographie",
    }
)
DE_REFERENCE_HEADINGS = frozenset(
    {"literatur", "literaturverzeichnis", "quellenverzeichnis"}
)
PT_REFERENCE_HEADINGS = frozenset(
    {
        "referências",
        "referências bibliográficas",
        "bibliografia",
    }
)
RU_REFERENCE_HEADINGS = frozenset({"литература", "список литературы", "библиография"})
ES_REFERENCE_HEADINGS = frozenset(
    {
        "referencias",
        "referencias bibliográficas",
        "bibliografía",
    }
)

EN_SECTION_HEADINGS = EN_REFERENCE_HEADINGS | {
    "abstract",
    "introduction",
    "method",
    "methods",
    "result",
    "results",
    "discussion",
    "conclusion",
    "reference",
}
ZH_SECTION_HEADINGS = ZH_REFERENCE_HEADINGS | {
    "摘要",
    "引言",
    "绪论",
    "方法",
    "实验",
    "结果",
    "讨论",
    "结论",
}
AR_SECTION_HEADINGS = AR_REFERENCE_HEADINGS | {
    "ملخص",
    "الملخص",
    "مقدمة",
    "المقدمة",
    "منهج",
    "المنهج",
    "منهجية",
    "المنهجية",
    "طرق",
    "الطرق",
    "أساليب",
    "الأساليب",
    "نتائج",
    "النتائج",
    "مناقشة",
    "المناقشة",
    "خاتمة",
    "الخاتمة",
    "استنتاج",
    "الاستنتاج",
    "استنتاجات",
    "الاستنتاجات",
}
FR_SECTION_HEADINGS = FR_REFERENCE_HEADINGS | {
    "résumé",
    "introduction",
    "méthode",
    "méthodes",
    "méthodologie",
    "résultat",
    "résultats",
    "discussion",
    "conclusion",
    "conclusions",
}
DE_SECTION_HEADINGS = DE_REFERENCE_HEADINGS | {
    "zusammenfassung",
    "einleitung",
    "methode",
    "methoden",
    "methodik",
    "ergebnis",
    "ergebnisse",
    "diskussion",
    "fazit",
    "schlussfolgerung",
    "schlussfolgerungen",
}
PT_SECTION_HEADINGS = PT_REFERENCE_HEADINGS | {
    "resumo",
    "introdução",
    "método",
    "métodos",
    "metodologia",
    "resultado",
    "resultados",
    "discussão",
    "conclusão",
    "conclusões",
}
RU_SECTION_HEADINGS = RU_REFERENCE_HEADINGS | {
    "аннотация",
    "введение",
    "метод",
    "методы",
    "методика",
    "методология",
    "результат",
    "результаты",
    "обсуждение",
    "заключение",
    "вывод",
    "выводы",
}
ES_SECTION_HEADINGS = ES_REFERENCE_HEADINGS | {
    "resumen",
    "introducción",
    "método",
    "métodos",
    "metodología",
    "resultado",
    "resultados",
    "discusión",
    "conclusión",
    "conclusiones",
}
PUNCTUATION = [
    ",",
    ";",
    ":",
    "(",
    ")",
    "—",
    "-",
    "?",
    "!",
    "，",
    "；",
    "：",
    "（",
    "）",
    "？",
    "！",
]
WORD_LENGTH_BREAKS = (300, 700, 1800, 4000)
WORD_SENTENCE_TERMINATORS = ".!?。！？"
ALL_SENTENCE_TERMINATORS = WORD_SENTENCE_TERMINATORS + "؟"
SENTENCE_CLOSERS = "\"'“”‘’«»‹›)]}）】》」』"
IGNORABLE_FORMAT_CONTROLS = frozenset(
    "\u00ad\u061c\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\u2066\u2067\u2068\u2069\ufeff"
)
IGNORABLE_WORD_CHARACTERS = IGNORABLE_FORMAT_CONTROLS | {"\u0640"}
DICTIONARY_CHARACTER_TRANSLATION = str.maketrans(
    {
        **{char: None for char in IGNORABLE_WORD_CHARACTERS},
        "’": "'",
        "ʼ": "'",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
    }
)
HEADING_PREFIX_RE = re.compile(
    r"""^(?:
        (?:\(|\[)\d+(?:\.\d+)*(?:\)|\])\s* |
        \d+(?:\.\d+)*\s+ |
        \d+(?:\.\d+)*[.、:]\s* |
        第?[一二三四五六七八九十百零〇\d]+[章节篇部][、.:]?\s* |
        [一二三四五六七八九十百零〇]+[、.)]\s*
    )""",
    re.VERBOSE,
)
PATTERN_ITEM_LIMIT = 10
PATTERN_OFFSET_LIMIT = 20


@dataclass(frozen=True)
class CleanedText:
    text: str
    excluded_fraction: float
    reference_section_removed: bool


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    uses_han_units: bool
    length_breaks: tuple[int, int, int, int]
    mattr_window: int
    top_token_count: int
    repeat_ngram_size: int
    token_rule: str
    sentence_terminators: str
    transitions: frozenset[str]
    section_headings: frozenset[str]
    reference_headings: frozenset[str]
    extra_punctuation: tuple[str, ...] = ()


def _unicode_word_profile(
    code: str,
    *,
    transitions: frozenset[str],
    section_headings: frozenset[str],
    reference_headings: frozenset[str],
    sentence_terminators: str = WORD_SENTENCE_TERMINATORS,
    extra_punctuation: tuple[str, ...] = (),
) -> LanguageProfile:
    return LanguageProfile(
        code,
        False,
        WORD_LENGTH_BREAKS,
        50,
        10,
        3,
        "unicode_words",
        sentence_terminators,
        transitions,
        section_headings,
        reference_headings,
        extra_punctuation,
    )


LANGUAGE_PROFILES = {
    "ar": _unicode_word_profile(
        "ar",
        transitions=AR_TRANSITIONS,
        section_headings=AR_SECTION_HEADINGS,
        reference_headings=AR_REFERENCE_HEADINGS,
        sentence_terminators=ALL_SENTENCE_TERMINATORS,
        extra_punctuation=("،", "؛", "؟"),
    ),
    "zh": LanguageProfile(
        "zh",
        True,
        (600, 1400, 3600, 8000),
        100,
        20,
        4,
        "jieba",
        "。！？!?",
        ZH_TRANSITIONS,
        ZH_SECTION_HEADINGS,
        ZH_REFERENCE_HEADINGS,
    ),
    "en": LanguageProfile(
        "en",
        False,
        WORD_LENGTH_BREAKS,
        50,
        10,
        3,
        "ascii_words",
        WORD_SENTENCE_TERMINATORS,
        EN_TRANSITIONS,
        EN_SECTION_HEADINGS,
        EN_REFERENCE_HEADINGS,
    ),
    "fr": _unicode_word_profile(
        "fr",
        transitions=FR_TRANSITIONS,
        section_headings=FR_SECTION_HEADINGS,
        reference_headings=FR_REFERENCE_HEADINGS,
    ),
    "de": _unicode_word_profile(
        "de",
        transitions=DE_TRANSITIONS,
        section_headings=DE_SECTION_HEADINGS,
        reference_headings=DE_REFERENCE_HEADINGS,
    ),
    "pt": _unicode_word_profile(
        "pt",
        transitions=PT_TRANSITIONS,
        section_headings=PT_SECTION_HEADINGS,
        reference_headings=PT_REFERENCE_HEADINGS,
    ),
    "ru": _unicode_word_profile(
        "ru",
        transitions=RU_TRANSITIONS,
        section_headings=RU_SECTION_HEADINGS,
        reference_headings=RU_REFERENCE_HEADINGS,
    ),
    "es": _unicode_word_profile(
        "es",
        transitions=ES_TRANSITIONS,
        section_headings=ES_SECTION_HEADINGS,
        reference_headings=ES_REFERENCE_HEADINGS,
        extra_punctuation=("¿", "¡"),
    ),
}

REFERENCE_HEADINGS = frozenset().union(
    *(profile.reference_headings for profile in LANGUAGE_PROFILES.values())
)


def _normalize_dictionary_text(text: str, *, strip_marks: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    normalized = normalized.translate(DICTIONARY_CHARACTER_TRANSLATION)
    if strip_marks:
        normalized = "".join(
            char
            for char in normalized
            if not unicodedata.category(char).startswith("M")
        )
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_heading(text: str) -> str:
    normalized = _normalize_dictionary_text(text, strip_marks=True)
    while match := HEADING_PREFIX_RE.match(normalized):
        normalized = normalized[match.end() :].lstrip()
    return normalized


def _normalize_reference_heading(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(r"[^\w ]", "", normalized)
    return _normalize_heading(normalized)


def _bounded_transition_count(text: str, phrase: str, language: str) -> int:
    escaped = re.escape(phrase)
    if language == "ar":
        if phrase == "إضافة إلى ذلك":
            escaped = rf"(?:بال)?{escaped}"
        escaped = rf"(?:[وف])?{escaped}"
    return len(re.findall(rf"(?<!\w){escaped}(?!\w)", text))


def resolve_language_profile(language: str) -> LanguageProfile:
    try:
        profile = LANGUAGE_PROFILES[language]
    except KeyError:
        raise ValueError(f"unsupported language profile: {language!r}") from None
    if profile.token_rule not in {"ascii_words", "jieba", "unicode_words"}:
        raise ValueError(f"invalid token rule for language profile: {language}")
    for resource_name, resources in (
        ("transition", profile.transitions),
        ("heading", profile.section_headings),
        ("reference heading", profile.reference_headings),
    ):
        if not resources:
            raise NotImplementedError(
                f"{resource_name} resources are not implemented: {language}"
            )
    if not profile.reference_headings <= profile.section_headings:
        raise ValueError(f"invalid reference heading resources: {language}")
    return profile


def _nfkc_units(text: str) -> list[tuple[str, int, int]]:
    segments: list[tuple[str, int, int]] = []
    cluster_start = 0
    for index in range(1, len(text) + 1):
        if index < len(text) and unicodedata.category(text[index]).startswith("M"):
            continue
        start = cluster_start
        normalized = unicodedata.normalize("NFKC", text[start:index])
        while segments:
            previous, previous_start, _ = segments[-1]
            combined = unicodedata.normalize("NFKC", text[previous_start:index])
            if combined == previous + normalized:
                break
            segments.pop()
            start = previous_start
            normalized = combined
        segments.append((normalized, start, index))
        cluster_start = index
    return [
        (char, start, end) for normalized, start, end in segments for char in normalized
    ]


def _normalize_with_offsets(text: str) -> tuple[str, list[tuple[int, int]]]:
    units = _nfkc_units(text or "")
    line_normalized: list[tuple[str, int, int]] = []
    index = 0
    while index < len(units):
        char, start, end = units[index]
        if char == "\r":
            if index + 1 < len(units) and units[index + 1][0] == "\n":
                end = units[index + 1][2]
                index += 1
            line_normalized.append(("\n", start, end))
        elif char != "\x00":
            line_normalized.append((char, start, end))
        index += 1

    collapsed: list[tuple[str, int, int]] = []
    index = 0
    while index < len(line_normalized):
        char, start, end = line_normalized[index]
        if char in {" ", "\t"}:
            index += 1
            while index < len(line_normalized) and line_normalized[index][0] in {
                " ",
                "\t",
            }:
                end = line_normalized[index][2]
                index += 1
            collapsed.append((" ", start, end))
            continue
        if char == "\n":
            run_start = index
            while index < len(line_normalized) and line_normalized[index][0] == "\n":
                index += 1
            run = line_normalized[run_start:index]
            if len(run) >= 4:
                collapsed.extend(
                    [
                        ("\n", run[0][1], run[0][2]),
                        ("\n", run[1][1], run[-1][2]),
                    ]
                )
            else:
                collapsed.extend(run)
            continue
        collapsed.append((char, start, end))
        index += 1

    left = 0
    while left < len(collapsed) and collapsed[left][0].isspace():
        left += 1
    right = len(collapsed)
    while right > left and collapsed[right - 1][0].isspace():
        right -= 1
    stripped = collapsed[left:right]
    return (
        "".join(char for char, _, _ in stripped),
        [(start, end) for _, start, end in stripped],
    )


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\x00", "")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{4,}", "\n\n", normalized)
    return normalized.strip()


def _citation_like(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    signals = sum(
        (
            bool(re.search(r"\b(?:19|20)\d{2}[a-z]?\b", stripped)),
            bool(DOI_RE.search(stripped)),
            bool(
                re.match(
                    r"^\s*(?:\[?\d+\]?|[^\W\d_]+(?:[-'’ʼ][^\W\d_]+)*[,،，])",
                    stripped,
                )
            ),
            "http" in stripped.lower(),
        )
    )
    return signals >= 2


def _reference_start_offset(text: str) -> int | None:
    lines = text.split("\n")
    scan_start = max(0, int(len(lines) * 0.6))
    offset = 0
    line_offsets = []
    for line in lines:
        line_offsets.append(offset)
        offset += len(line) + 1

    for index in range(scan_start, len(lines)):
        heading = _normalize_reference_heading(lines[index])
        if heading not in REFERENCE_HEADINGS:
            continue
        following = [line for line in lines[index + 1 : index + 21] if line.strip()]
        density = sum(_citation_like(line) for line in following) / max(
            1, len(following)
        )
        if density >= 0.3:
            return line_offsets[index]
    return None


def clean_for_analysis(text: str) -> CleanedText:
    normalized = normalize_text(text)
    if not normalized:
        return CleanedText("", 0.0, False)

    reference_start = _reference_start_offset(normalized)
    clipped = (
        normalized[:reference_start] if reference_start is not None else normalized
    )
    clipped = URL_RE.sub(" <URL> ", clipped)
    clipped = DOI_RE.sub(" <DOI> ", clipped)
    clipped = INLINE_CITATION_RE.sub(" <CIT> ", clipped)
    clipped = NUMBER_RE.sub(" <NUM> ", clipped)
    clipped = re.sub(r"[ \t]+", " ", clipped)
    clipped = re.sub(r"\n{3,}", "\n\n", clipped).strip()
    excluded_fraction = 1.0 - (len(clipped) / max(1, len(normalized)))
    return CleanedText(
        clipped,
        float(np.clip(excluded_fraction, 0.0, 1.0)),
        reference_start is not None,
    )


def _english_token_spans(text: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(0).lower().replace("’", "'"), match.start(), match.end())
        for match in EN_WORD_RE.finditer(text)
    ]


def english_tokens(text: str) -> list[str]:
    return [token for token, _, _ in _english_token_spans(text)]


def _normalized_units(text: str) -> list[tuple[str, int, int]]:
    return [
        (folded, start, end)
        for char, start, end in _nfkc_units(text)
        for folded in char.casefold()
    ]


def _casefold_units(text: str) -> list[tuple[str, int, int]]:
    return [
        (folded, index, index + 1)
        for index, char in enumerate(text)
        for folded in char.casefold()
    ]


def _unicode_word_spans_from_units(
    units: Sequence[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    current: list[str] = []
    current_start: int | None = None
    current_end = 0
    apostrophes = {"'", "’", "ʼ"}

    def finish_token() -> None:
        nonlocal current_start, current_end
        if current and current_start is not None:
            spans.append(("".join(current), current_start, current_end))
        current.clear()
        current_start = None
        current_end = 0

    for index, (char, raw_start, raw_end) in enumerate(units):
        if char in IGNORABLE_WORD_CHARACTERS:
            continue
        if char in apostrophes:
            next_index = index + 1
            while (
                next_index < len(units)
                and units[next_index][0] in IGNORABLE_WORD_CHARACTERS
            ):
                next_index += 1
            if current and next_index < len(units) and units[next_index][0].isalpha():
                current.append("'")
                current_end = raw_end
            else:
                finish_token()
        elif char.isalpha():
            if current_start is None:
                current_start = raw_start
            current.append(char)
            current_end = raw_end
        elif (
            unicodedata.category(char).startswith("M")
            and current
            and current[-1] != "'"
        ):
            current.append(char)
            current_end = raw_end
        else:
            finish_token()

    finish_token()
    return spans


def _unicode_word_spans(text: str) -> list[tuple[str, int, int]]:
    return _unicode_word_spans_from_units(_normalized_units(text or ""))


def unicode_word_tokens(text: str) -> list[str]:
    return [token for token, _, _ in _unicode_word_spans(text)]


def chinese_chars(text: str) -> list[str]:
    return ZH_CHAR_RE.findall(text)


def _chinese_token_spans(text: str) -> list[tuple[str, int, int]]:
    spans = []
    for token, start, end in jieba.tokenize(text, mode="default"):
        stripped = token.strip()
        if not stripped or not ZH_CHAR_RE.search(stripped):
            continue
        leading = len(token) - len(token.lstrip())
        trailing = len(token) - len(token.rstrip())
        spans.append((stripped, start + leading, end - trailing))
    return spans


def chinese_tokens(text: str) -> list[str]:
    return [token for token, _, _ in _chinese_token_spans(text)]


def _analysis_token_spans(
    text: str,
    language: str,
    *,
    already_nfkc: bool = False,
) -> list[tuple[str, int, int]]:
    profile = resolve_language_profile(language)
    if profile.token_rule == "jieba":
        return _chinese_token_spans(text)
    if profile.token_rule == "ascii_words":
        return _english_token_spans(text)
    if profile.token_rule == "unicode_words":
        if already_nfkc:
            return _unicode_word_spans_from_units(_casefold_units(text))
        return _unicode_word_spans(text)
    raise AssertionError("unreachable token rule")


def analysis_tokens(text: str, language: str) -> list[str]:
    return [token for token, _, _ in _analysis_token_spans(text, language)]


def _is_sentence_suffix(char: str) -> bool:
    return (
        char in SENTENCE_CLOSERS
        or char in IGNORABLE_FORMAT_CONTROLS
        or unicodedata.category(char) in {"Mn", "Me", "Pe", "Pf"}
    )


def _unicode_sentences(text: str, terminators: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] not in terminators:
            index += 1
            continue

        end = index + 1
        while end < len(text) and text[end] in terminators:
            end += 1
        while end < len(text) and _is_sentence_suffix(text[end]):
            end += 1
        if end < len(text) and not (text[end].isspace() or text[end] == "\u200b"):
            index = end
            continue

        sentence = text[start:end].strip()
        if sentence:
            sentences.append(sentence)
        while end < len(text) and (text[end].isspace() or text[end] == "\u200b"):
            end += 1
        start = end
        index = end

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def split_sentences(text: str, language: str) -> list[str]:
    profile = resolve_language_profile(language)
    if not text:
        return []
    if profile.token_rule == "jieba":
        parts = ZH_SENTENCE_BOUNDARY_RE.split(text)
        return [part.strip() for part in parts if part.strip()]
    if profile.token_rule == "ascii_words":
        parts = SENTENCE_BOUNDARY_RE.split(text)
        return [part.strip() for part in parts if part.strip()]
    if profile.token_rule == "unicode_words":
        return _unicode_sentences(text, profile.sentence_terminators)
    raise AssertionError("unreachable token rule")


def split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) > 1:
        return paragraphs

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) <= 1:
        return paragraphs

    # RAID/arXiv 等来源常把正文按固定列宽硬换行。只有长行且大多数行以
    # 句末标点结束时，才把单换行视为真实段落边界；否则先解除软换行。
    line_lengths = [len(line) for line in lines]
    terminal_lines = []
    for line in lines:
        end = len(line)
        while end and _is_sentence_suffix(line[end - 1]):
            end -= 1
        terminal_lines.append(line[end - 1 : end] in ALL_SENTENCE_TERMINATORS)
    terminal_rate = np.mean(terminal_lines)
    if np.median(line_lengths) >= 160 and terminal_rate >= 0.6:
        return lines
    return [" ".join(lines)]


def effective_length(text: str, language: str) -> int:
    profile = resolve_language_profile(language)
    return (
        len(chinese_chars(text))
        if profile.uses_han_units
        else len(analysis_tokens(text, language))
    )


def length_band(length: int, language: str) -> str:
    profile = resolve_language_profile(language)
    minimum, short_maximum, medium_maximum, long_maximum = profile.length_breaks
    if length < minimum:
        return "below_minimum"
    if length < short_maximum:
        return "short"
    if length < medium_maximum:
        return "medium"
    if length <= long_maximum:
        return "long"
    return "above_long"


def shannon_entropy(values: Sequence[str]) -> float:
    if not values:
        return float("nan")
    counts = np.asarray(list(Counter(values).values()), dtype=float)
    probabilities = counts / counts.sum()
    return float(-(probabilities * np.log2(probabilities)).sum())


def mattr(tokens: Sequence[str], window: int) -> float:
    size = len(tokens)
    if size == 0:
        return float("nan")
    if size <= window:
        return len(set(tokens)) / size
    starts = np.linspace(0, size - window, num=min(200, size - window + 1), dtype=int)
    return float(
        np.mean([len(set(tokens[start : start + window])) / window for start in starts])
    )


def distribution_stats(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return {
            "median": float("nan"),
            "q10": float("nan"),
            "q90": float("nan"),
            "iqr": float("nan"),
            "mad": float("nan"),
            "cv": float("nan"),
        }
    median = float(np.median(array))
    q10, q25, q75, q90 = np.quantile(array, [0.1, 0.25, 0.75, 0.9])
    mean = float(array.mean())
    return {
        "median": median,
        "q10": float(q10),
        "q90": float(q90),
        "iqr": float(q75 - q25),
        "mad": float(np.median(np.abs(array - median))),
        "cv": float(array.std(ddof=0) / mean) if mean else float("nan"),
    }


def repeat_coverage(tokens: Sequence[str], n: int) -> float:
    if len(tokens) < n:
        return 0.0
    ngrams = [tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1)]
    repeated = {ngram for ngram, count in Counter(ngrams).items() if count >= 2}
    if not repeated:
        return 0.0
    covered = np.zeros(len(tokens), dtype=bool)
    for index, ngram in enumerate(ngrams):
        if ngram in repeated:
            covered[index : index + n] = True
    return float(covered.mean())


def lag_one_autocorrelation(values: Sequence[float]) -> float:
    array = np.asarray(values, dtype=float)
    if array.size < 3 or np.std(array[:-1]) == 0 or np.std(array[1:]) == 0:
        return float("nan")
    return float(np.corrcoef(array[:-1], array[1:])[0, 1])


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set, right_set = set(left), set(right)
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else float("nan")


def paragraph_cohesion(paragraphs: Sequence[str], language: str) -> dict[str, float]:
    profile = resolve_language_profile(language)
    token_sets = []
    for paragraph in paragraphs[:100]:
        tokens = analysis_tokens(paragraph, language)
        token_sets.append(
            {token for token in tokens if len(token) > 1 or profile.uses_han_units}
        )
    adjacent = [
        jaccard(token_sets[i], token_sets[i + 1]) for i in range(len(token_sets) - 1)
    ]
    nonadjacent = []
    for index in range(max(0, len(token_sets) - 2)):
        for other in range(index + 2, min(len(token_sets), index + 8)):
            nonadjacent.append(jaccard(token_sets[index], token_sets[other]))
    adjacent = [value for value in adjacent if not math.isnan(value)]
    nonadjacent = [value for value in nonadjacent if not math.isnan(value)]
    return {
        "paragraph_adjacent_jaccard": float(np.median(adjacent))
        if adjacent
        else float("nan"),
        "paragraph_nonadjacent_jaccard_q90": float(np.quantile(nonadjacent, 0.9))
        if nonadjacent
        else float("nan"),
        "intro_conclusion_jaccard": (
            jaccard(token_sets[0], token_sets[-1])
            if len(token_sets) >= 2
            else float("nan")
        ),
    }


def transition_density(
    text: str, tokens: Sequence[str], language: str
) -> tuple[float, float]:
    profile = resolve_language_profile(language)
    if profile.code in {"en", "zh"}:
        lowered = text.lower()
        counts = [lowered.count(phrase) for phrase in profile.transitions]
    else:
        normalized = _normalize_dictionary_text(text, strip_marks=profile.code == "ar")
        counts = [
            _bounded_transition_count(normalized, phrase, profile.code)
            for phrase in profile.transitions
        ]
    total = sum(counts)
    density = total / max(1, len(tokens)) * 1000
    diversity = sum(count > 0 for count in counts) / len(profile.transitions)
    return density, diversity


def sentence_start_repeat(sentences: Sequence[str], language: str) -> float:
    resolve_language_profile(language)
    starts: list[tuple[str, ...]] = []
    for sentence in sentences:
        tokens = analysis_tokens(sentence, language)
        if len(tokens) >= 2:
            starts.append(tuple(tokens[: min(4, len(tokens))]))
    if not starts:
        return float("nan")
    repeated = sum(count for count in Counter(starts).values() if count >= 2)
    return repeated / len(starts)


def _merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _raw_span(
    raw_spans: Sequence[tuple[int, int]],
    start: int,
    end: int,
) -> tuple[int, int]:
    return raw_spans[start][0], raw_spans[end - 1][1]


def _pattern_excluded_ranges(
    text: str,
    normalized: str,
    raw_spans: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    reference_start = _reference_start_offset(normalized)
    content_end = reference_start if reference_start is not None else len(normalized)
    ranges = [
        _raw_span(raw_spans, *match.span())
        for pattern in (URL_RE, DOI_RE, INLINE_CITATION_RE, NUMBER_RE)
        for match in pattern.finditer(normalized, 0, content_end)
    ]
    ranges.extend(
        (index, index + 1) for index, char in enumerate(text) if char == "\x00"
    )
    if reference_start is not None:
        ranges.append((raw_spans[reference_start][0], len(text)))
    return _merge_ranges(ranges)


def runtime_excluded_fraction(text: str) -> float:
    """Measure the union before placeholder expansion, in normalized code points."""
    normalized = normalize_text(text)
    # Identity offsets reuse the pattern exclusions in this normalized coordinate space.
    spans = [(index, index + 1) for index in range(len(normalized))]
    excluded = _pattern_excluded_ranges(normalized, normalized, spans)
    return sum(end - start for start, end in excluded) / max(1, len(normalized))


def _visible_token_spans(
    normalized: str,
    raw_spans: Sequence[tuple[int, int]],
    language: str,
    excluded: Sequence[tuple[int, int]],
) -> list[tuple[str, int, int, int]]:
    visible = []
    exclusion_index = 0
    block = 0
    for token, normalized_start, normalized_end in _analysis_token_spans(
        normalized,
        language,
        already_nfkc=True,
    ):
        start, end = _raw_span(raw_spans, normalized_start, normalized_end)
        while exclusion_index < len(excluded) and excluded[exclusion_index][1] <= start:
            exclusion_index += 1
            block += 1
        if exclusion_index < len(excluded) and excluded[exclusion_index][0] < end:
            continue
        visible.append((token, start, end, block))
    return visible


def _sentence_spans(text: str, language: str) -> list[tuple[int, int]]:
    spans = []
    cursor = 0
    for sentence in split_sentences(text, language):
        start = text.find(sentence, cursor)
        if start < 0:
            raise AssertionError("sentence is not a slice of the input text")
        end = start + len(sentence)
        spans.append((start, end))
        cursor = end
    return spans


def _pattern_items(
    groups: dict[str | tuple[str, ...], list[tuple[int, int]]],
    limit: int,
) -> list[dict[str, object]]:
    repeated = [item for item in groups.items() if len(item[1]) >= 2]
    repeated.sort(key=lambda item: (-len(item[1]), item[1][0][0], item[0]))
    return [
        {
            "count": len(offsets),
            "offsets": [
                {"start": start, "end": end}
                for start, end in offsets[:PATTERN_OFFSET_LIMIT]
            ],
        }
        for _, offsets in repeated[:limit]
    ]


def extract_document_patterns(
    text: str,
    language: str,
) -> dict[str, list[dict[str, object]]]:
    value = text or ""
    profile = resolve_language_profile(language)
    normalized, raw_spans = _normalize_with_offsets(value)
    excluded = _pattern_excluded_ranges(value, normalized, raw_spans)
    tokens = _visible_token_spans(normalized, raw_spans, language, excluded)

    token_groups: dict[str | tuple[str, ...], list[tuple[int, int]]] = {}
    for token, start, end, _ in tokens:
        token_groups.setdefault(token, []).append((start, end))

    phrase_groups: dict[str | tuple[str, ...], list[tuple[int, int]]] = {}
    n = profile.repeat_ngram_size
    for index in range(len(tokens) - n + 1):
        window = tokens[index : index + n]
        if len({item[3] for item in window}) != 1:
            continue
        key = tuple(item[0] for item in window)
        phrase_groups.setdefault(key, []).append((window[0][1], window[-1][2]))

    start_groups: dict[str | tuple[str, ...], list[tuple[int, int]]] = {}
    token_index = 0
    for normalized_start, normalized_end in _sentence_spans(normalized, language):
        sentence_start, sentence_end = _raw_span(
            raw_spans,
            normalized_start,
            normalized_end,
        )
        while token_index < len(tokens) and tokens[token_index][2] <= sentence_start:
            token_index += 1
        end_index = token_index
        while end_index < len(tokens) and tokens[end_index][1] < sentence_end:
            end_index += 1
        sentence_tokens = tokens[token_index:end_index]
        if len(sentence_tokens) < 2:
            continue
        template = sentence_tokens[: min(4, len(sentence_tokens))]
        if any(
            start < template[0][1] and sentence_start <= end for start, end in excluded
        ):
            continue
        if len({item[3] for item in template}) != 1:
            continue
        key = tuple(item[0] for item in template)
        start_groups.setdefault(key, []).append((template[0][1], template[-1][2]))

    return {
        "descriptive_top_tokens": _pattern_items(
            token_groups,
            profile.top_token_count,
        ),
        "repeated_phrases": _pattern_items(phrase_groups, PATTERN_ITEM_LIMIT),
        "sentence_start_templates": _pattern_items(
            start_groups,
            PATTERN_ITEM_LIMIT,
        ),
    }


def punctuation_features(
    text: str, denominator: int, language: str
) -> tuple[float, float]:
    profile = resolve_language_profile(language)
    marks = PUNCTUATION + list(profile.extra_punctuation)
    counts = [text.count(mark) for mark in marks]
    density = sum(counts) / max(1, denominator) * 1000
    expanded = [mark for mark, count in zip(marks, counts) for _ in range(count)]
    return density, shannon_entropy(expanded)


def section_heading_count(text: str, language: str) -> int:
    profile = resolve_language_profile(language)
    count = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        if _normalize_heading(stripped) in profile.section_headings:
            count += 1
    return count


def extract_document_features(
    text: str, language: str
) -> dict[str, float | int | str | bool]:
    profile = resolve_language_profile(language)
    cleaned = clean_for_analysis(text)
    tokens = analysis_tokens(cleaned.text, language)
    chars = chinese_chars(cleaned.text)
    sentences = split_sentences(cleaned.text, language)
    paragraphs = split_paragraphs(cleaned.text)
    unit_length = len(chars) if profile.uses_han_units else len(tokens)
    band = length_band(unit_length, language)
    sentence_lengths = [effective_length(sentence, language) for sentence in sentences]
    sentence_lengths = [value for value in sentence_lengths if value > 0]
    paragraph_lengths = [
        effective_length(paragraph, language) for paragraph in paragraphs
    ]
    paragraph_lengths = [value for value in paragraph_lengths if value > 0]
    token_counts = Counter(tokens)
    top_n = profile.top_token_count
    top_concentration = sum(
        count for _, count in token_counts.most_common(top_n)
    ) / max(1, len(tokens))
    hapax = sum(count == 1 for count in token_counts.values()) / max(
        1, len(token_counts)
    )
    token_entropy = shannon_entropy(tokens)
    sentence_stats = distribution_stats(sentence_lengths)
    paragraph_stats = distribution_stats(paragraph_lengths)
    adjacent_changes = [
        abs(left - right) for left, right in zip(sentence_lengths, sentence_lengths[1:])
    ]
    transition_per_1k, transition_diversity = transition_density(
        cleaned.text, tokens, language
    )
    punctuation_per_1k, punctuation_entropy = punctuation_features(
        cleaned.text, unit_length, language
    )
    cohesion = paragraph_cohesion(paragraphs, language)
    minimum = profile.length_breaks[0]
    eligibility_reasons = []
    if unit_length < minimum:
        eligibility_reasons.append("below_minimum_length")
    if len(sentences) < 10:
        eligibility_reasons.append("fewer_than_10_sentences")
    if cleaned.excluded_fraction > 0.4:
        eligibility_reasons.append("excluded_content_over_40pct")

    return {
        "analysis_char_count": len(cleaned.text),
        "effective_length": unit_length,
        "length_band": band,
        "sentence_count": len(sentences),
        "paragraph_count": len(paragraphs),
        "excluded_fraction": cleaned.excluded_fraction,
        "reference_section_removed": cleaned.reference_section_removed,
        "eligible_directional": not eligibility_reasons,
        "eligibility_reason": ";".join(eligibility_reasons)
        if eligibility_reasons
        else "eligible",
        "mattr": mattr(tokens, profile.mattr_window),
        "char_mattr_zh": mattr(chars, 200) if profile.uses_han_units else float("nan"),
        "token_entropy": token_entropy,
        "entropy_per_log_vocab": (
            token_entropy / math.log2(len(token_counts))
            if len(token_counts) > 1
            else float("nan")
        ),
        "hapax_type_ratio": hapax,
        "top_token_concentration": top_concentration,
        "repeat_ngram_coverage": repeat_coverage(tokens, profile.repeat_ngram_size),
        "repeat_char_ngram_coverage_zh": (
            repeat_coverage(chars, 6) if profile.uses_han_units else float("nan")
        ),
        "sentence_length_median": sentence_stats["median"],
        "sentence_length_q10": sentence_stats["q10"],
        "sentence_length_q90": sentence_stats["q90"],
        "sentence_length_iqr": sentence_stats["iqr"],
        "sentence_length_mad": sentence_stats["mad"],
        "sentence_length_cv": sentence_stats["cv"],
        "sentence_adjacent_change_median": float(np.median(adjacent_changes))
        if adjacent_changes
        else float("nan"),
        "sentence_length_lag1": lag_one_autocorrelation(sentence_lengths),
        "paragraph_length_median": paragraph_stats["median"],
        "paragraph_length_iqr": paragraph_stats["iqr"],
        "paragraph_length_cv": paragraph_stats["cv"],
        "sentence_start_repeat": sentence_start_repeat(sentences, language),
        "transition_per_1k": transition_per_1k,
        "transition_diversity": transition_diversity,
        "punctuation_per_1k": punctuation_per_1k,
        "punctuation_entropy": punctuation_entropy,
        "section_heading_count": section_heading_count(text, language),
        **cohesion,
    }


# EV2-06's 37-field schema, expressed without a research/Arrow dependency.
_NON_FLOAT_TYPES = {
    "analysis_char_count": int,
    "effective_length": int,
    "length_band": str,
    "sentence_count": int,
    "paragraph_count": int,
    "reference_section_removed": bool,
    "eligible_directional": bool,
    "eligibility_reason": str,
    "section_heading_count": int,
}
_REQUIRED_FLOATS = frozenset(
    "excluded_fraction hapax_type_ratio top_token_concentration repeat_ngram_coverage "
    "transition_per_1k transition_diversity punctuation_per_1k".split()
)
_NULLABLE_FLOATS = frozenset(
    "mattr char_mattr_zh token_entropy entropy_per_log_vocab repeat_char_ngram_coverage_zh "
    "sentence_length_median sentence_length_q10 sentence_length_q90 sentence_length_iqr "
    "sentence_length_mad sentence_length_cv sentence_adjacent_change_median sentence_length_lag1 "
    "paragraph_length_median paragraph_length_iqr paragraph_length_cv sentence_start_repeat "
    "punctuation_entropy paragraph_adjacent_jaccard paragraph_nonadjacent_jaccard_q90 "
    "intro_conclusion_jaccard".split()
)
_ZH_ONLY_FEATURES = {"char_mattr_zh", "repeat_char_ngram_coverage_zh"}


def normalize_features(
    values: dict[str, float | int | str | bool], language: str
) -> tuple[dict[str, float | int | str | bool | None], dict[str, str]]:
    """Preserve V1 scalars and encode only permitted NaNs as null plus a reason."""
    resolve_language_profile(language)
    if set(values) != _NON_FLOAT_TYPES.keys() | _REQUIRED_FLOATS | _NULLABLE_FLOATS:
        raise ValueError("feature schema does not match FEATURE_SCHEMA_VERSION")
    normalized: dict[str, float | int | str | bool | None] = {}
    missing_reasons: dict[str, str] = {}
    for name, value in values.items():
        if name in _NON_FLOAT_TYPES:
            if type(value) is not _NON_FLOAT_TYPES[name]:
                raise ValueError(f"invalid feature type: {name}")
            normalized[name] = value
            continue
        if type(value) not in (int, float):
            raise ValueError(f"invalid numeric feature: {name}")
        numeric = float(value)
        if math.isnan(numeric) and name in _NULLABLE_FLOATS:
            normalized[name] = None
            missing_reasons[name] = (
                "not_applicable_for_language"
                if name in _ZH_ONLY_FEATURES and language != "zh"
                else "insufficient_observations"
            )
        elif not math.isfinite(numeric):
            raise ValueError(f"invalid non-finite feature: {name}")
        else:
            normalized[name] = numeric
    return normalized, missing_reasons
