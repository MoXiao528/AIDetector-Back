"""Optional brief-text references: bounded inputs, validated cells, unchanged V1."""

import copy
import hashlib
import os
from pathlib import Path

import pytest

from app.schemas.evidence import EvidenceResult
from app.services.evidence_engine import DOMAINS, METRICS, EvidenceEngine
from app.services.short_text_reference import LENGTH_BANDS, METHOD
import test_evidence_engine as v1


artifacts = v1.artifacts
comparison_input = v1.comparison_input
ready_engine = v1.ready_engine


@pytest.fixture
def short_reference(artifacts):
    manifest, reference = artifacts
    return {
        "short_text_reference_schema_version": 1,
        "feature_schema_version": 1,
        "source": {
            "feature_sha256": "a" * 64,
            "base_bundle_sha256": "b" * 64,
            "config_sha256": "c" * 64,
            "pair_cache_sha256": "d" * 64,
            "runtime_features_sha256": "e" * 64,
            "base_manifest_sha256": hashlib.sha256(v1.encoded(manifest)).hexdigest(),
            "base_reference_sha256": hashlib.sha256(v1.encoded(reference)).hexdigest(),
        },
        "method": copy.deepcopy(METHOD),
        "summary": {
            "cell_status_counts": {"ready": 14},
            "metric_status_counts": {"ready": 14 * 22},
            "cohort": {
                "input_pairs": 1800,
                "input_rows": 3600,
                "original_test_pairs": 600,
                "original_train_pairs": 1200,
                "selected_pairs": 1800,
                "selected_source_groups": 1800,
                "split_leakage_groups": 0,
            },
            "corrected_exclusion_audit": {
                "audited_pairs": 1800,
                "changed_document_ratios": 0,
                "documents_over_40pct": 0,
            },
        },
        "cells": [
            {
                "scope": scope,
                "language": "zh",
                "domain": domain,
                "length_band": band,
                "train_source_group_count": 100,
                "test_source_group_count": 50,
                "gated_pair_count": 150,
                "status": "ready",
                "reason": None,
                "metrics": [
                    {
                        "dimension": dimension,
                        "feature": feature,
                        "status": "ready",
                        "reason": None,
                        "sample_count": 100,
                        "test_sample_count": 50,
                        "valid_pair_count": 150,
                        "human_test_coverage": 0.8,
                        "ai_test_coverage": 0.9,
                        "human_quantiles": [index / 100 for index in range(101)],
                        "ai_quantiles": [index / 50 for index in range(101)],
                    }
                    for feature, dimension in METRICS.items()
                ],
            }
            for band in LENGTH_BANDS
            for scope, domains in (("exact", DOMAINS), ("language_length", (None,)))
            for domain in domains
        ],
    }


def load_engine(tmp_path, artifacts, short_reference):
    path, digest = v1.write_bundle(
        tmp_path,
        artifacts,
        members=[
            ("manifest.json", v1.encoded(artifacts[0])),
            ("reference.json", v1.encoded(artifacts[1])),
            ("short_text_reference.json", v1.encoded(short_reference)),
        ],
    )
    return EvidenceEngine(mode="shadow", bundle_path=str(path), bundle_sha256=digest)


@pytest.fixture
def brief_engine(tmp_path, artifacts, short_reference):
    engine = load_engine(tmp_path, artifacts, short_reference)
    assert engine.status == "ready", engine.reason
    return engine


def chinese_response():
    value = v1.response()
    value["route"]["language"] = "zh"
    return value


def brief_input(comparison_input, length=400, sentences=2, paragraphs=3):
    comparison_input["raw"].update(
        effective_length=length,
        length_band="below_minimum" if length < 600 else "short",
        eligible_directional=length >= 600 and sentences >= 10,
        eligibility_reason=";".join(
            reason
            for condition, reason in (
                (length < 600, "below_minimum_length"),
                (sentences < 10, "fewer_than_10_sentences"),
            )
            if condition
        )
        or "eligible",
        sentence_count=sentences,
        paragraph_count=paragraphs,
    )
    return comparison_input["text"]


@pytest.mark.parametrize(
    "length,band",
    [
        (199, "below_minimum"),
        (200, "brief_200_399"),
        (399, "brief_200_399"),
        (400, "brief_400_599"),
        (599, "brief_400_599"),
        (600, "short"),
    ],
)
def test_length_boundaries_never_use_a_different_length_reference(
    brief_engine, comparison_input, length, band
):
    text = brief_input(comparison_input, length=length, sentences=10)
    result = brief_engine.analyze(text, chinese_response(), main_label="AI")
    EvidenceResult.model_validate(result)
    assert result["route"]["lengthBucket"] == band
    assert result["quality"]["coverage"] == (
        0 if length == 199 else 19 / 22 if length == 600 else 1
    )
    if length != 600:
        assert all(
            signal["sampleCount"] == (None if length == 199 else 100)
            for signal in result["signals"]
        )


@pytest.mark.parametrize(
    "sentences,paragraphs,missing",
    [
        (
            1,
            1,
            {
                "sentence_start_repeat",
                "sentence_length_iqr",
                "sentence_length_cv",
                "sentence_adjacent_change_median",
                "paragraph_length_iqr",
                "paragraph_length_cv",
                "paragraph_adjacent_jaccard",
                "intro_conclusion_jaccard",
                "paragraph_nonadjacent_jaccard_q90",
            },
        ),
        (2, 2, {"paragraph_nonadjacent_jaccard_q90"}),
        (2, 3, set()),
    ],
)
def test_short_policy_blocks_only_metrics_without_required_observations(
    brief_engine, comparison_input, sentences, paragraphs, missing
):
    text = brief_input(comparison_input, sentences=sentences, paragraphs=paragraphs)
    result = brief_engine.analyze(text, chinese_response(), main_label="AI")
    EvidenceResult.model_validate(result)
    assert result["quality"]["coverage"] == (22 - len(missing)) / 22
    for signal in result["signals"]:
        assert signal["observed"] == comparison_input["raw"][signal["metric"]]
        assert signal["reasons"] == (
            ["insufficient_observations"] if signal["metric"] in missing else []
        )
        assert (signal["relation"] is None) == (signal["metric"] in missing)


@pytest.mark.parametrize(
    "status,reason,count,coverage",
    [
        ("no_data", "no_valid_source_groups", 0, None),
        ("insufficient_n", "reference_metrics_unavailable", 99, None),
        ("validation_failed", "reference_validation_failed", 100, 0.79),
    ],
)
def test_unaccepted_metric_keeps_true_cause_and_never_uses_pooled_replacement(
    brief_engine, comparison_input, status, reason, count, coverage
):
    cell = next(
        cell
        for cell in brief_engine.short_reference["cells"]
        if cell["domain"] == "news" and cell["length_band"] == "brief_400_599"
    )
    cell["metrics"][0].update(
        status=status,
        reason="short_reference_insufficient_n"
        if status == "insufficient_n"
        else reason,
        sample_count=count,
        test_sample_count=0 if status == "no_data" else 50,
        valid_pair_count=0 if status == "no_data" else count + 50,
        human_quantiles=None,
        ai_quantiles=None,
        human_test_coverage=coverage,
        ai_test_coverage=0.9 if status == "validation_failed" else None,
    )
    result = brief_engine.analyze(
        brief_input(comparison_input), chinese_response(), main_label="AI"
    )
    assert result["quality"]["coverage"] == 21 / 22
    assert result["quality"]["reasons"] == ["reference_metrics_unavailable"] + (
        ["reference_validation_failed"] if status == "validation_failed" else []
    )
    signal = v1.signal_for(result, "mattr")
    assert signal["observed"] == 0.5 and signal["sampleCount"] == count
    assert signal["referenceRanges"] is None and signal["reasons"] == [reason]


def test_no_valid_brief_cell_never_uses_other_band_or_v1(
    brief_engine, comparison_input
):
    for cell in brief_engine.short_reference["cells"]:
        if cell["length_band"] == "brief_400_599":
            cell["status"] = "insufficient_n"
    result = brief_engine.analyze(
        brief_input(comparison_input), chinese_response(), main_label="human"
    )
    assert result["status"] == "insufficient"
    assert result["quality"]["reasons"] == ["reference_cell_unavailable"]
    assert all(
        signal["observed"] is not None and signal["sampleCount"] is None
        for signal in result["signals"]
    )


def test_short_reference_pools_only_same_band(brief_engine, comparison_input):
    for cell in brief_engine.short_reference["cells"]:
        if cell["scope"] == "exact" and cell["length_band"] == "brief_400_599":
            cell["status"] = "insufficient_n"
    result = brief_engine.analyze(
        brief_input(comparison_input), chinese_response(), main_label="AI"
    )
    assert result["route"]["fallbackLevel"] == "language_length"
    assert result["quality"]["reasons"] == ["reference_fallback_language_length"]
    assert result["quality"]["coverage"] == 1


def test_two_member_bundle_and_other_languages_keep_v1_rules(
    ready_engine, brief_engine, comparison_input
):
    text = brief_input(comparison_input)
    old = ready_engine.analyze(text, chinese_response(), main_label="AI")
    assert old["status"] == "insufficient"
    assert "below_minimum_length" in old["quality"]["reasons"]
    assert old["route"]["lengthBucket"] == "below_minimum"
    for length in (400, 600):
        text = brief_input(comparison_input, length=length, sentences=10)
        for payload in (
            (v1.response(), chinese_response()) if length == 600 else (v1.response(),)
        ):
            expected = ready_engine.analyze(text, payload, main_label="AI")
            actual = brief_engine.analyze(text, payload, main_label="AI")
            actual["artifactVersion"] = expected["artifactVersion"]
            assert actual == expected


def test_short_reference_extrema_extend_chinese_axis_only(
    tmp_path, artifacts, short_reference, comparison_input
):
    metric = next(
        metric
        for metric in short_reference["cells"][0]["metrics"]
        if metric["feature"] == "token_entropy"
    )
    metric["ai_quantiles"][-1] = 8.0
    engine = load_engine(tmp_path, artifacts, short_reference)
    assert engine.status == "ready"
    assert engine.reference_extents[("zh", "token_entropy")] == (0.0, 8.0)
    assert engine.reference_extents[("en", "token_entropy")] == (0.0, 2.0)
    for length in (200, 400, 600):
        text = brief_input(comparison_input, length=length, sentences=10)
        result = engine.analyze(text, chinese_response(), main_label="AI")
        signal = v1.signal_for(result, "token_entropy")
        assert signal["referenceExtent"] == [0.0, 8.0]
        assert signal["referenceRanges"] == {"human": [0.05, 0.95], "ai": [0.1, 1.9]}
        EvidenceResult.model_validate(result)


def test_short_text_main_label_only_changes_notice(brief_engine, comparison_input):
    text = brief_input(comparison_input)
    comparison_input["raw"]["mattr"] = 1.5
    ai = brief_engine.analyze(text, chinese_response(), main_label="AI")
    human = brief_engine.analyze(text, chinese_response(), main_label="human")
    assert v1.signal_for(human, "mattr")["notice"] == "reference_mismatch"
    for result in (ai, human):
        for signal in result["signals"]:
            signal["notice"] = None
    assert ai == human


@pytest.mark.parametrize("fraction", [0.4, 0.400001])
def test_brief_policy_preserves_strict_original_exclusion_gate(
    brief_engine, comparison_input, monkeypatch, fraction
):
    from app.services import evidence_features

    monkeypatch.setattr(
        evidence_features, "runtime_excluded_fraction", lambda _: fraction
    )
    text = brief_input(comparison_input)
    result = brief_engine.analyze(text, chinese_response(), main_label="AI")
    assert result["quality"]["coverage"] == (1 if fraction == 0.4 else 0)
    assert ("excluded_content_over_40pct" in result["quality"]["reasons"]) == (
        fraction > 0.4
    )
    assert all(signal["observed"] is not None for signal in result["signals"])


def test_brief_gate_uses_corrected_exclusion_ratio_not_old_placeholder_length(
    brief_engine, comparison_input, monkeypatch
):
    from app.services import evidence_features

    text = brief_input(comparison_input)
    comparison_input["raw"]["eligibility_reason"] += ";excluded_content_over_40pct"
    monkeypatch.setattr(evidence_features, "runtime_excluded_fraction", lambda _: 0.4)
    result = brief_engine.analyze(text, chinese_response(), main_label="AI")
    assert result["quality"]["coverage"] == 1


@pytest.mark.parametrize(
    "path,value",
    [
        (("feature_schema_version",), True),
        (("source", "base_reference_sha256"), "a" * 64),
        (("source", "config_sha256"), "private-path"),
        (("method", "minimum_train_groups"), 99),
        (("method", "minimum_test_groups"), 49),
        (("method", "minimum_side_coverage"), 0.79),
        (
            (
                "method",
                "minimum_observations",
                "sentence_start_repeat",
                "sentence_count",
            ),
            1,
        ),
        (("method", "length_bands", "brief_200_399"), [199, 399]),
        (("cells", 0, "language"), "en"),
        (("cells", 0, "train_source_group_count"), True),
        (("cells", 0, "gated_pair_count"), 149),
        (("cells", 0, "metrics", 0, "sample_count"), 101),
        (("cells", 0, "metrics", 0, "test_sample_count"), 49),
        (("cells", 0, "metrics", 0, "human_test_coverage"), 0.79),
        (("cells", 0, "metrics", 0, "ai_test_coverage"), float("nan")),
        (("cells", 0, "metrics", 0, "human_quantiles", 50), -1),
        (("cells", 0, "metrics", 0, "ai_quantiles", 50), True),
        (("cells", 0, "metrics", 0, "status"), "validation_failed"),
        (("cells", 0, "metrics", 0, "valid_pair_count"), 151),
        (("summary", "cell_status_counts", "ready"), 13),
        (("summary", "cohort", "split_leakage_groups"), 1),
        (("summary", "corrected_exclusion_audit", "audited_pairs"), 1799),
    ],
)
def test_invalid_optional_artifact_fails_privately_before_runtime(
    tmp_path, artifacts, short_reference, path, value
):
    v1.replace_at(short_reference, path, value)
    engine = load_engine(tmp_path, artifacts, short_reference)
    assert engine.status == "failed" and engine.reason == "invalid_evidence_bundle"
    assert engine.reference is engine.short_reference is None


@pytest.mark.parametrize("duplicate", ["cell", "metric"])
def test_duplicate_reference_members_are_rejected(
    tmp_path, artifacts, short_reference, duplicate
):
    items = (
        short_reference["cells"]
        if duplicate == "cell"
        else short_reference["cells"][0]["metrics"]
    )
    items[1] = copy.deepcopy(items[0])
    assert (
        load_engine(tmp_path, artifacts, short_reference).reason
        == "invalid_evidence_bundle"
    )


def test_real_optional_bundle_smoke_without_router_model():
    bundle_path = os.environ.get("EVIDENCE_SHORT_TEST_BUNDLE")
    if not bundle_path:
        pytest.skip("explicit local brief reference bundle required")
    data = Path(bundle_path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == "f04fee602a09850adae31e5e78ea9926a27cd96663aeeeaeaafb11d8a959fb0e"
    engine = EvidenceEngine(
        mode="shadow", bundle_path=bundle_path, bundle_sha256=digest
    )
    assert engine.status == "ready", engine.reason
    payload = chinese_response()
    payload["routerArtifactSha256"] = engine.router_artifact_sha256
    payload["route"]["domain"] = "novel"
    for length in (199, 200, 399, 400, 599, 600):
        han = ("研究人员通过观察数据了解变化并逐步完善分析方法" * 40)[:length]
        text = (
            "。\n\n".join(han[index : index + 40] for index in range(0, length, 40))
            + "。"
        )
        result = engine.analyze(text, payload, main_label="AI")
        EvidenceResult.model_validate(result)
        assert result["route"]["lengthBucket"] == (
            "below_minimum"
            if length < 200
            else "brief_200_399"
            if length < 400
            else "brief_400_599"
            if length < 600
            else "short"
        )
        assert (result["quality"]["coverage"] > 0) == (length >= 200)
        if 200 <= length < 600:
            assert "fewer_than_10_sentences" not in result["quality"]["reasons"]


def test_failed_validation_is_legal_but_cannot_publish_quantiles(
    tmp_path, artifacts, short_reference
):
    metric = short_reference["cells"][0]["metrics"][0]
    metric.update(
        status="validation_failed",
        reason="reference_validation_failed",
        human_test_coverage=0.79,
        human_quantiles=None,
        ai_quantiles=None,
    )
    short_reference["summary"]["metric_status_counts"] = {
        "ready": 14 * 22 - 1,
        "validation_failed": 1,
    }
    engine = load_engine(tmp_path, artifacts, short_reference)
    assert engine.status == "ready"
    assert engine.reference_extents[("zh", "mattr")] == (0.0, 2.0)
    metric["human_quantiles"] = [0.5] * 101
    assert (
        load_engine(tmp_path, artifacts, short_reference).reason
        == "invalid_evidence_bundle"
    )
