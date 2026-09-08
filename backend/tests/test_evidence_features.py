import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from app.services import evidence_features as features


GOLDENS = json.loads(
    Path(__file__).with_name("feature_goldens_v1.json").read_text(encoding="utf-8")
)
LANGUAGES = {"ar", "zh", "en", "fr", "de", "pt", "ru", "es"}


def test_multilingual_feature_goldens_v1():
    assert GOLDENS["feature_schema_version"] == features.FEATURE_SCHEMA_VERSION == 1
    assert set(GOLDENS["cases"]) == set(features.LANGUAGE_PROFILES) == LANGUAGES
    for language, case in GOLDENS["cases"].items():
        expected = case["features"]
        raw = features.extract_document_features(case["text"], language)
        actual, reasons = features.normalize_features(raw, language)
        assert len(actual) == 37
        assert tuple(raw) == tuple(actual) == tuple(expected)
        for key, value in expected.items():
            if value == "NaN":
                assert math.isnan(raw[key]), (language, key)
                assert actual[key] is None, (language, key)
            elif type(value) is float:
                assert type(actual[key]) is float, (language, key)
                assert actual[key] == pytest.approx(value, rel=1e-12, abs=1e-12), (
                    language,
                    key,
                )
            else:
                assert type(actual[key]) is type(value), (language, key)
                assert actual[key] == value, (language, key)
        assert reasons == (
            {}
            if language == "zh"
            else {
                "char_mattr_zh": "not_applicable_for_language",
                "repeat_char_ngram_coverage_zh": "not_applicable_for_language",
            }
        )
        assert json.loads(json.dumps(actual, allow_nan=False)) == actual


@pytest.mark.parametrize(
    "language,text,surfaces",
    [
        ("fr", "😀\r\nCafe\u0301\tnoir. Café blanc.", ["Cafe\u0301", "Café"]),
        (
            "fr",
            "😀 co\u00adoperate vite. cooperate encore.",
            ["co\u00adoperate", "cooperate"],
        ),
        ("de", "😀 Straße breit. STRASSE kurz.", ["Straße", "STRASSE"]),
        ("pt", "😀 d’água limpa. d'água fria.", ["d’água", "d'água"]),
        (
            "ar",
            "😀 كلمة\u200cعربية هنا. كلمةعربية هناك.",
            ["كلمة\u200cعربية", "كلمةعربية"],
        ),
        ("zh", "😀\r\n研究方法。研究结果。", ["研究", "研究"]),
        ("en", "😀\r\nＡｌｐｈａ beta. Alpha gamma.", ["Ａｌｐｈａ", "Alpha"]),
    ],
)
def test_pattern_offsets_are_original_code_points(language, text, surfaces):
    patterns = features.extract_document_patterns(text, language)
    item = next(
        item
        for item in patterns["descriptive_top_tokens"]
        if text[item["offsets"][0]["start"] : item["offsets"][0]["end"]] == surfaces[0]
    )
    starts = [text.index(surfaces[0]), text.rindex(surfaces[1])]
    assert item == {
        "count": 2,
        "offsets": [
            {"start": start, "end": start + len(surface)}
            for start, surface in zip(starts, surfaces)
        ],
    }


def test_patterns_keep_caps_counts_privacy_and_exclusion_barriers():
    text = "alpha beta gamma delta. " * 25
    patterns = features.extract_document_patterns(text, "en")
    assert patterns == features.extract_document_patterns(text, "en")
    assert set(patterns) == {
        "descriptive_top_tokens",
        "repeated_phrases",
        "sentence_start_templates",
    }
    for items in patterns.values():
        assert items and len(items) <= 10
        assert items[0]["count"] == 25
        for item in items:
            assert set(item) == {"count", "offsets"}
            assert len(item["offsets"]) == 20
            for offset in item["offsets"]:
                assert set(offset) == {"start", "end"}
                assert all(type(value) is int for value in offset.values())
                assert 0 <= offset["start"] < offset["end"] <= len(text)
    for barrier in ("2020", "https://example.com", "10.1234/ABC", "[1]", "\x00"):
        text = f"alpha beta {barrier} gamma. alpha beta {barrier} gamma."
        patterns = features.extract_document_patterns(text, "en")
        assert patterns["repeated_phrases"] == []
        assert patterns["sentence_start_templates"] == []


@pytest.mark.parametrize("language", ["en", "zh"])
def test_empty_features_have_a_reason_for_every_null(language):
    raw = features.extract_document_features("", language)
    actual, reasons = features.normalize_features(raw, language)
    assert actual["effective_length"] == 0
    assert actual["length_band"] == "below_minimum"
    assert actual["eligible_directional"] is False
    assert (
        actual["eligibility_reason"] == "below_minimum_length;fewer_than_10_sentences"
    )
    assert len(reasons) == (21 if language == "en" else 20)
    assert set(reasons) == {key for key, value in actual.items() if value is None}
    for key, reason in reasons.items():
        assert reason == (
            "not_applicable_for_language"
            if language != "zh"
            and key in {"char_mattr_zh", "repeat_char_ngram_coverage_zh"}
            else "insufficient_observations"
        )
    json.dumps(actual, allow_nan=False)


@pytest.mark.parametrize(
    "key,value",
    [
        ("mattr", float("inf")),
        ("mattr", float("-inf")),
        ("excluded_fraction", float("nan")),
        ("transition_per_1k", float("nan")),
        ("mattr", None),
        ("mattr", True),
        ("mattr", "0.5"),
        ("effective_length", True),
        ("section_heading_count", 1.0),
        ("eligible_directional", 1),
        ("length_band", None),
    ],
)
def test_invalid_feature_values_are_not_silently_converted(key, value):
    raw = features.extract_document_features("One sentence.", "en")
    raw[key] = value
    with pytest.raises(ValueError):
        features.normalize_features(raw, "en")


def test_missing_or_extra_features_fail_schema_validation():
    raw = features.extract_document_features("One sentence.", "en")
    raw["extra"] = 0
    with pytest.raises(ValueError, match="feature schema"):
        features.normalize_features(raw, "en")
    del raw["extra"], raw["mattr"]
    with pytest.raises(ValueError, match="feature schema"):
        features.normalize_features(raw, "en")


@pytest.mark.parametrize("language", sorted(LANGUAGES))
def test_length_bands_keep_both_out_of_range_states(language):
    bounds = (600, 1400, 3600, 8000) if language == "zh" else (300, 700, 1800, 4000)
    minimum, medium, long, maximum = bounds
    assert [
        features.length_band(n, language)
        for n in (
            minimum - 1,
            minimum,
            medium - 1,
            medium,
            long - 1,
            long,
            maximum,
            maximum + 1,
        )
    ] == [
        "below_minimum",
        "short",
        "short",
        "medium",
        "medium",
        "long",
        "long",
        "above_long",
    ]


@pytest.mark.parametrize(
    "resource", ["transitions", "section_headings", "reference_headings"]
)
def test_missing_language_resources_fail_closed(monkeypatch, resource):
    monkeypatch.setitem(
        features.LANGUAGE_PROFILES,
        "fr",
        replace(features.LANGUAGE_PROFILES["fr"], **{resource: frozenset()}),
    )
    for extractor in (
        features.extract_document_features,
        features.extract_document_patterns,
    ):
        with pytest.raises(
            NotImplementedError, match="resources are not implemented: fr"
        ):
            extractor("Texte.", "fr")


def test_unknown_language_has_no_fallback():
    for extractor in (
        features.extract_document_features,
        features.extract_document_patterns,
    ):
        with pytest.raises(ValueError, match="unsupported language profile: 'ja'"):
            extractor("Text.", "ja")
