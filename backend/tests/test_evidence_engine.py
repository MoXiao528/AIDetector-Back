import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.services.evidence_engine import (
    EvidenceEngine,
    _approximate_percentile,
    _reference_position,
    _select_reference_cell,
)


ROUTER_SHA = "1" * 64
REAL_BUNDLE_SHA = "8abe24fc7e7747f4e2e9b90a80bf26b7999726373fa80e8519a97dffbe7014b2"
LANGUAGES = ["ar", "de", "en", "es", "fr", "pt", "ru", "zh"]
DOMAINS = ["academic", "news", "novel", "seo", "webtext", "wiki"]
BANDS = ["short", "medium", "long"]
FEATURES = {
    "lexical": [
        "mattr",
        "token_entropy",
        "entropy_per_log_vocab",
        "hapax_type_ratio",
        "top_token_concentration",
    ],
    "phrase_template": ["repeat_ngram_coverage", "sentence_start_repeat"],
    "rhythm": [
        "sentence_length_median",
        "sentence_length_iqr",
        "sentence_length_cv",
        "sentence_adjacent_change_median",
        "paragraph_length_median",
        "paragraph_length_iqr",
        "paragraph_length_cv",
        "punctuation_per_1k",
        "punctuation_entropy",
    ],
    "discourse": [
        "transition_per_1k",
        "transition_diversity",
        "paragraph_adjacent_jaccard",
        "paragraph_nonadjacent_jaccard_q90",
        "intro_conclusion_jaccard",
        "section_heading_count",
    ],
}
NO_DATA_FEATURES = {
    "paragraph_adjacent_jaccard",
    "paragraph_nonadjacent_jaccard_q90",
    "intro_conclusion_jaccard",
}


@pytest.fixture
def artifacts():
    manifest = {
        "schema_version": 1,
        "kind": "evidence_v1_reference_bundle",
        "reference": {
            "path": "reference.json",
            "reference_schema_version": 1,
            "feature_schema_version": 1,
        },
        "router": {
            "artifact_sha256": ROUTER_SHA,
            "contract_kind": "production_final_always_route",
            "distribution": "external_directory",
            "format": "xlm_roberta_safetensors_directory",
            "legacy_joblib_allowed": False,
            "model_file_count": 6,
            "routing": {
                "abstention_enabled": False,
                "always_route_supported_8x6": True,
                "classes": [
                    f"{language}:{domain}"
                    for language in LANGUAGES
                    for domain in DOMAINS
                ],
                "languages": LANGUAGES.copy(),
                "domains": DOMAINS.copy(),
                "confidence_role": "diagnostic_only_not_probability_of_correctness",
                "domain_selection": "argmax_within_selected_language",
                "global_joint_argmax_is_route": False,
                "language_selection": "argmax_of_domain_aggregated_language_mass",
                "temperature": 1.0,
            },
        },
        "privacy": {
            key: False
            for key in (
                "absolute_paths_included",
                "document_text_included",
                "generator_included",
                "raw_corpus_included",
                "record_identifiers_included",
            )
        },
        "governance": {
            "candidate_under_waiver": True,
            "classifier_release_basis_passed": True,
            "owner_approved": True,
            "release_disposition": "APPROVED_WITH_WAIVERS",
            "serve_eligible": False,
            "methodology_status": {
                "abstain": {
                    "status": "NOT_CERTIFIED",
                    "requirement_disposition": "WAIVED_BY_OWNER",
                },
                "fresh_external_or_prospective_confirmation": {
                    "status": "NOT_PERFORMED",
                    "requirement_disposition": "WAIVED_FOR_V1_PRELAUNCH",
                },
            },
            "production_fit_evaluation": {
                "accuracy_evaluated": False,
                "gate_evaluated": False,
                "macro_f1_evaluated": False,
                "reason": "all_eligible_documents_are_fit_data",
            },
        },
    }
    cells = []
    for scope, routes, count in (
        (
            "exact",
            [
                (language, domain, band)
                for language in LANGUAGES
                for domain in DOMAINS
                for band in BANDS
            ],
            10,
        ),
        (
            "language_length",
            [(language, None, band) for language in LANGUAGES for band in BANDS],
            60,
        ),
        ("language", [(language, None, None) for language in LANGUAGES], 180),
    ):
        for language, domain, band in routes:
            metrics = []
            for dimension, features in FEATURES.items():
                for feature in features:
                    missing = feature in NO_DATA_FEATURES
                    metrics.append(
                        {
                            "dimension": dimension,
                            "feature": feature,
                            "human_quantiles": None
                            if missing
                            else [value / 100 for value in range(101)],
                            "ai_quantiles": None
                            if missing
                            else [value / 50 for value in range(101)],
                            "sample_count": 0 if missing else count,
                            "valid_pair_count": 0 if missing else count,
                            "status": "no_data" if missing else "ready",
                            "reason": "no_valid_source_groups" if missing else None,
                        }
                    )
            cells.append(
                {
                    "scope": scope,
                    "language": language,
                    "domain": domain,
                    "length_band": band,
                    "gated_pair_count": count,
                    "source_group_count": count,
                    "status": "ready",
                    "reason": None,
                    "metrics": metrics,
                }
            )
    reference = {
        "reference_schema_version": 1,
        "feature_schema_version": 1,
        "cells": cells,
        "source": {
            "config_sha256": "a" * 64,
            "pair_delta_schema_version": 1,
            "pair_delta_sha256": "b" * 64,
        },
        "method": {
            "analysis_unit": "source_group",
            "sample_count_unit": "paired_common_valid_source_groups",
            "split_scope": "all",
            "fallback": {
                "chain": ["exact", "language_length", "language", "unavailable"],
                "cross_language_fallback": False,
                "metric_fallback_after_cell_selection": False,
                "minimum_source_groups": 10,
                "selection_unit": "cell",
            },
            "reference_distribution": {
                "percentile_grid": list(range(101)),
                "quantile_method": "inverted_cdf",
                "population": "paired_common_valid_human_ai_source_groups",
                "source_group_reducer": "median",
                "runtime_percentile_semantics": "approximate_midrank_from_1pp_inverse_ecdf_knots",
                "use": "descriptive_percentiles_only_no_direction_or_product_relation",
            },
            "strict_length_gate": {
                "length_bands": BANDS.copy(),
                "length_ratio_inclusive": [0.8, 1.25],
                "requires_both_directional_eligible": True,
                "requires_same_length_band": True,
                "unit": "pair_before_source_group_aggregation",
            },
            "status_contract": {
                "insufficient_n": "1_to_9_source_groups",
                "no_data": "zero_valid_source_groups",
                "ready": "at_least_10_source_groups",
            },
        },
        "summary": {
            "planned": {
                "domains": 6,
                "languages": 8,
                "length_bands": 3,
                "features": 22,
                "exact_cells": 144,
                "pooled_cells": 32,
                "total_cells": 176,
            },
            "cell_status_counts": {
                scope: {"ready": count, "insufficient_n": 0, "no_data": 0}
                for scope, count in (
                    ("exact", 144),
                    ("language_length", 24),
                    ("language", 8),
                )
            },
            "metric_status_counts": {
                "ready": 176 * 19,
                "insufficient_n": 0,
                "no_data": 176 * 3,
            },
            "fallback_resolution_counts": {
                "exact": 144,
                "language_length": 0,
                "language": 0,
                "unavailable": 0,
            },
            "cohort": {
                "input_pairs": 1440,
                "both_eligible_primary_band_pairs": 1440,
                "same_band_pairs": 1440,
                "strict_pairs": 1440,
                "strict_source_groups": 1440,
                "strict_train_pairs": 720,
                "strict_train_source_groups": 720,
                "strict_test_pairs": 720,
                "strict_test_source_groups": 720,
            },
        },
    }
    return manifest, reference


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def write_bundle(tmp_path, artifacts, *, members=None):
    manifest, reference = artifacts
    if members is None:
        members = [
            ("manifest.json", encoded(manifest)),
            ("reference.json", encoded(reference)),
        ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    data = buffer.getvalue()
    path = tmp_path / "evidence.bundle"
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


@pytest.fixture
def ready_engine(tmp_path, artifacts):
    path, digest = write_bundle(tmp_path, artifacts)
    engine = EvidenceEngine(mode="shadow", bundle_path=str(path), bundle_sha256=digest)
    assert engine.status == "ready", engine.reason
    return engine


def response(status="routed", *, sha=ROUTER_SHA, reason=None):
    return {
        "schemaVersion": 1,
        "status": status,
        "routerArtifactSha256": sha,
        "route": {
            "language": "en",
            "domain": "news",
            "confidence": {"language": 0.8, "domain": 0.7},
        }
        if status == "routed"
        else None,
        "reason": reason,
    }


def failure(reason):
    return response("failed", sha=None, reason=reason)


def replace_at(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


def test_off_does_not_read_bundle_or_validate_unused_config(monkeypatch):
    reads = []

    def forbidden_open(*args, **kwargs):
        reads.append(args)
        raise AssertionError("off must not read a bundle")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", forbidden_open)
        engine = EvidenceEngine(
            mode="off", bundle_path="missing-private-artifact", bundle_sha256="invalid"
        )
    assert reads == []
    assert (
        engine.status,
        engine.reason,
        engine.artifact_version,
        engine.reference,
    ) == ("off", None, None, None)
    assert engine.validate_router_response(response()) == failure("bundle_unavailable")
    result = engine.extract_features(None, None)
    assert result["status"] == "off" and result["reason"] is None
    assert result["features"] is None and result["patterns"] is None


@pytest.mark.parametrize("mode", ["shadow", "serve"])
def test_bundle_loads_once_and_waivers_do_not_become_a_runtime_gate(
    tmp_path, artifacts, mode
):
    path, digest = write_bundle(tmp_path, artifacts)
    engine = EvidenceEngine(mode=mode, bundle_path=str(path), bundle_sha256=digest)
    assert engine.status == "ready", engine.reason
    assert engine.reason is None
    assert engine.artifact_version == digest
    assert engine.reference == artifacts[1]
    path.unlink()
    for _ in range(2):
        assert engine.validate_router_response(response()) == response()
        assert engine.status == "ready"


@pytest.mark.parametrize(
    "mode,sha", [("unexpected", ROUTER_SHA), ("shadow", "bad-hash"), ("shadow", None)]
)
def test_invalid_configuration_is_an_evidence_failure(tmp_path, mode, sha):
    engine = EvidenceEngine(
        mode=mode, bundle_path=str(tmp_path / "private-model-path"), bundle_sha256=sha
    )
    assert engine.status == "failed"
    assert engine.reason == "invalid_evidence_config"
    assert engine.reference is None
    assert engine.artifact_version is None
    assert engine.validate_router_response(response()) == failure("bundle_unavailable")
    result = engine.extract_features("private text", response())
    assert (
        result["status"] == "failed" and result["reason"] == "invalid_evidence_config"
    )
    assert result["features"] is None and result["patterns"] is None


def test_missing_file_bad_hash_and_non_zip_fail_without_retaining_reference(
    tmp_path, artifacts
):
    path, digest = write_bundle(tmp_path, artifacts)
    bad_hash = EvidenceEngine(
        mode="shadow", bundle_path=str(path), bundle_sha256="0" * 64
    )
    path.write_bytes(b"not a zip")
    bad_zip = EvidenceEngine(
        mode="shadow",
        bundle_path=str(path),
        bundle_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    path.unlink()
    missing = EvidenceEngine(mode="shadow", bundle_path=str(path), bundle_sha256=digest)
    for engine in (bad_hash, bad_zip, missing):
        assert engine.status == "failed"
        assert engine.reason == "invalid_evidence_bundle"
        assert engine.reference is None
        assert engine.artifact_version is None
        assert (
            engine.extract_features("private text", response())["reason"]
            == "invalid_evidence_bundle"
        )


@pytest.mark.parametrize(
    "extra_name", ["router.joblib", "../reference.json", "/reference.json"]
)
def test_extra_and_traversal_zip_members_are_rejected(tmp_path, artifacts, extra_name):
    manifest, reference = artifacts
    members = [
        ("manifest.json", encoded(manifest)),
        ("reference.json", encoded(reference)),
        (extra_name, b"private"),
    ]
    path, digest = write_bundle(tmp_path, artifacts, members=members)
    engine = EvidenceEngine(mode="shadow", bundle_path=str(path), bundle_sha256=digest)
    assert engine.status == "failed"
    assert engine.reason == "invalid_evidence_bundle"
    assert engine.reference is None


def test_missing_or_duplicate_zip_member_is_rejected(tmp_path, artifacts):
    manifest, reference = artifacts
    for members in (
        [("manifest.json", encoded(manifest))],
        [
            ("manifest.json", encoded(manifest)),
            ("reference.json", encoded(reference)),
            ("reference.json", b"{}"),
        ],
    ):
        if len(members) == 3:
            with pytest.warns(UserWarning, match="Duplicate name"):
                path, digest = write_bundle(tmp_path, artifacts, members=members)
        else:
            path, digest = write_bundle(tmp_path, artifacts, members=members)
        engine = EvidenceEngine(
            mode="shadow", bundle_path=str(path), bundle_sha256=digest
        )
        assert engine.status == "failed"
        assert engine.reason == "invalid_evidence_bundle"


@pytest.mark.parametrize(
    "index,path,value",
    [
        (0, ("schema_version",), 2),
        (0, ("schema_version",), True),
        (0, ("reference", "feature_schema_version"), 2),
        (0, ("router", "distribution"), "embedded_joblib"),
        (0, ("router", "legacy_joblib_allowed"), True),
        (0, ("router", "artifact_sha256"), "not-a-sha"),
        (0, ("router", "routing", "abstention_enabled"), True),
        (0, ("router", "routing", "classes", 0), "ja:academic"),
        (1, ("reference_schema_version",), 2),
        (1, ("feature_schema_version",), 2),
        (1, ("cells", 0, "language"), "ja"),
        (1, ("cells", 0, "metrics", 0, "sample_count"), -1),
        (1, ("cells", 0, "metrics", 0, "human_quantiles"), [0.0] * 100),
        (1, ("cells", 0, "metrics", 0, "human_quantiles", 50), -1.0),
        (1, ("cells", 0, "metrics", 0, "human_quantiles", 50), float("nan")),
        (1, ("cells", 0, "metrics", 0, "human_quantiles", 50), float("inf")),
    ],
)
def test_incompatible_bundle_content_fails_after_matching_external_hash(
    tmp_path, artifacts, index, path, value
):
    replace_at(artifacts[index], path, value)
    bundle, digest = write_bundle(tmp_path, artifacts)
    engine = EvidenceEngine(
        mode="shadow", bundle_path=str(bundle), bundle_sha256=digest
    )
    assert engine.status == "failed"
    assert engine.reason == "invalid_evidence_bundle"
    assert engine.reference is None
    assert engine.artifact_version is None


@pytest.mark.parametrize("confidence", [0, 0.00001, 1])
def test_supported_route_accepts_low_confidence_and_returns_a_deep_copy(
    ready_engine, confidence
):
    payload = response()
    payload["route"]["confidence"] = {"language": confidence, "domain": confidence}
    original = copy.deepcopy(payload)
    result = ready_engine.validate_router_response(payload)
    assert result == original
    assert payload == original
    result["route"]["confidence"]["language"] = 0.3
    assert payload == original


@pytest.mark.parametrize(
    "status,reason,sha",
    [
        ("unsupported", "unsupported_language", ROUTER_SHA),
        *[
            ("failed", reason, sha)
            for reason in (
                "model_unavailable",
                "model_failure",
                "busy",
                "timeout",
                "language_undetermined",
            )
            for sha in (ROUTER_SHA, None)
        ],
    ],
)
def test_legal_router_failures_remain_explicit(ready_engine, status, reason, sha):
    payload = response(status, reason=reason, sha=sha)
    assert ready_engine.validate_router_response(payload) == payload


@pytest.mark.parametrize(
    "status,reason",
    [
        ("routed", "model_failure"),
        ("routed", "unsupported_language"),
        ("unsupported", None),
        ("unsupported", "timeout"),
        ("unsupported", "language_undetermined"),
        ("routed", "language_undetermined"),
        ("failed", None),
        ("failed", "unsupported_language"),
    ],
)
def test_router_status_reason_combinations_cannot_cross(ready_engine, status, reason):
    assert ready_engine.validate_router_response(
        response(status, reason=reason)
    ) == failure("invalid_router_response")


@pytest.mark.parametrize(
    "status,reason",
    [
        ("routed", None),
        ("unsupported", "unsupported_language"),
        ("failed", "model_failure"),
    ],
)
@pytest.mark.parametrize("sha", ["2" * 64, "not-a-sha", True])
def test_every_present_router_sha_must_match_the_loaded_bundle(
    ready_engine, status, reason, sha
):
    assert ready_engine.validate_router_response(
        response(status, reason=reason, sha=sha)
    ) == failure("invalid_router_response")


@pytest.mark.parametrize(
    "status,reason", [("routed", None), ("unsupported", "unsupported_language")]
)
def test_routed_and_unsupported_require_a_loaded_router_identity(
    ready_engine, status, reason
):
    assert ready_engine.validate_router_response(
        response(status, reason=reason, sha=None)
    ) == failure("invalid_router_response")


@pytest.mark.parametrize(
    "path,value",
    [
        (("schemaVersion",), True),
        (("schemaVersion",), 1.0),
        (("schemaVersion",), "1"),
        (("schemaVersion",), 2),
        (("status",), "ready"),
        (("route",), None),
        (("route", "language"), "it"),
        (("route", "domain"), "unknown"),
        (("route", "confidence", "language"), True),
        (("route", "confidence", "domain"), "0.7"),
        (("route", "confidence", "language"), -0.01),
        (("route", "confidence", "domain"), 1.01),
        (("route", "confidence", "language"), float("nan")),
        (("route", "confidence", "domain"), float("inf")),
    ],
)
def test_malformed_router_fields_degrade_without_leaking_the_payload(
    ready_engine, path, value
):
    payload = response()
    replace_at(payload, path, value)
    assert ready_engine.validate_router_response(payload) == failure(
        "invalid_router_response"
    )


@pytest.mark.parametrize("payload", [None, [], "private submitted text", 1])
def test_non_object_response_is_rejected(ready_engine, payload):
    assert ready_engine.validate_router_response(payload) == failure(
        "invalid_router_response"
    )


@pytest.mark.parametrize(
    "path,key",
    [((), "score"), (("route",), "text"), (("route", "confidence"), "label")],
)
def test_extra_router_fields_are_not_forwarded(ready_engine, path, key):
    payload = response()
    target = payload
    for part in path:
        target = target[part]
    target[key] = "private submitted text"
    assert ready_engine.validate_router_response(payload) == failure(
        "invalid_router_response"
    )


@pytest.mark.parametrize(
    "field", ["schemaVersion", "status", "routerArtifactSha256", "route", "reason"]
)
def test_required_router_fields_cannot_be_omitted(ready_engine, field):
    payload = response()
    del payload[field]
    assert ready_engine.validate_router_response(payload) == failure(
        "invalid_router_response"
    )


@pytest.mark.parametrize(
    "status,reason",
    [("unsupported", "unsupported_language"), ("failed", "model_failure")],
)
def test_non_routed_status_cannot_smuggle_a_route(ready_engine, status, reason):
    payload = response(status, reason=reason)
    payload["route"] = response()["route"]
    assert ready_engine.validate_router_response(payload) == failure(
        "invalid_router_response"
    )


def test_real_bundle_smoke_is_opt_in_and_checks_the_frozen_artifact():
    path = os.environ.get("EVIDENCE_TEST_BUNDLE_PATH")
    if not path:
        pytest.skip("set EVIDENCE_TEST_BUNDLE_PATH to run the real Bundle smoke")
    engine = EvidenceEngine(
        mode="shadow", bundle_path=path, bundle_sha256=REAL_BUNDLE_SHA
    )
    assert engine.status == "ready", engine.reason
    assert engine.artifact_version == REAL_BUNDLE_SHA
    assert len(engine.reference["cells"]) == 176
    assert sum(len(cell["metrics"]) for cell in engine.reference["cells"]) == 3872
    payload = response(sha=engine.router_artifact_sha256)
    result = engine.extract_features("One sentence. Another sentence.", payload)
    assert result["status"] == "extracted"
    assert result["features"]["effective_length"] == 4
    assert result["missingReasons"]["char_mattr_zh"] == "not_applicable_for_language"
    assert result["artifactVersion"] == REAL_BUNDLE_SHA
    json.dumps(result, allow_nan=False)
    analyzed = engine.analyze(
        "alpha beta gamma delta! " * 100, payload, main_label="AI"
    )
    assert analyzed["status"] == "partial"
    assert analyzed["quality"]["coverage"] == 19 / 22
    assert analyzed["route"]["lengthBucket"] == "short"
    assert len(analyzed["signals"]) == 22
    assert {
        signal["metric"] for signal in analyzed["signals"] if signal["relation"] is None
    } == NO_DATA_FEATURES
    json.dumps(analyzed, allow_nan=False)


def test_feature_dependencies_stay_lazy_and_import_failure_is_isolated(
    tmp_path, artifacts
):
    path, digest = write_bundle(tmp_path, artifacts)
    script = """
import json
import sys
sys.path.insert(0, sys.argv[1])
from app.services.evidence_engine import EvidenceEngine
payload = json.loads(sys.argv[4])
off = EvidenceEngine(bundle_path='unused', bundle_sha256='invalid')
assert off.extract_features(None, None)['status'] == 'off'
assert off.analyze(None, None, main_label=None)['status'] == 'off'
engine = EvidenceEngine(mode='shadow', bundle_path=sys.argv[2], bundle_sha256=sys.argv[3])
assert engine.status == 'ready'
assert engine.extract_features(None, payload)['reason'] == 'invalid_text'
assert engine.extract_features('private text', {})['reason'] == 'invalid_router_response'
unsupported = dict(payload, status='unsupported', route=None, reason='unsupported_language')
assert engine.extract_features('private text', unsupported)['status'] == 'unsupported'
failed = dict(payload, status='failed', route=None, reason='timeout')
assert engine.extract_features('private text', failed)['reason'] == 'timeout'
assert engine.analyze(None, payload, main_label='AI')['quality']['reasons'] == ['invalid_text']
assert engine.analyze('private text', {}, main_label='AI')['quality']['reasons'] == ['invalid_router_response']
assert engine.analyze('private text', unsupported, main_label='AI')['status'] == 'unsupported'
assert engine.analyze('private text', failed, main_label='AI')['quality']['reasons'] == ['timeout']
undetermined = dict(payload, status='failed', route=None, reason='language_undetermined')
assert engine.extract_features('private text', undetermined)['reason'] == 'language_undetermined'
for label in ('AI', 'Human'):
    result = engine.analyze('private text', undetermined, main_label=label)
    assert result['status'] == 'failed' and result['route'] is None
    assert result['quality'] == {'level': 'unavailable', 'coverage': 0.0, 'reasons': ['language_undetermined']}
    assert result['signals'] == [] and result['patterns'] is None
assert 'app.services.evidence_features' not in sys.modules
# -S deliberately removes site packages: optional dependencies cannot load.
result = engine.extract_features('private text', payload)
assert result['status'] == 'failed' and result['reason'] == 'feature_extraction_failed'
assert result['features'] is None and result['patterns'] is None
assert engine.status == 'ready'
result = engine.analyze('private text', payload, main_label='AI')
assert result['quality']['reasons'] == ['feature_extraction_failed']
assert result['signals'] == [] and result['patterns'] is None
assert not {'numpy', 'jieba', 'torch', 'transformers', 'pandas', 'pyarrow'} & sys.modules.keys()
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            str(Path(__file__).resolve().parents[1]),
            str(path),
            digest,
            json.dumps(response()),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("language", LANGUAGES)
def test_local_extraction_matches_all_language_goldens(ready_engine, language):
    case = json.loads(
        Path(__file__).with_name("feature_goldens_v1.json").read_text(encoding="utf-8")
    )["cases"][language]
    payload = response()
    payload["route"]["language"] = language
    payload["route"]["confidence"] = {"language": 0, "domain": 0}
    original = copy.deepcopy(payload)
    result = ready_engine.extract_features(case["text"], payload)
    assert result["status"] == "extracted" and result["reason"] is None
    assert result["artifactVersion"] == ready_engine.artifact_version
    assert result["featureSchemaVersion"] == 1
    assert result["route"] == payload["route"]
    assert len(result["features"]) == 37
    for key, value in case["features"].items():
        if value == "NaN":
            assert result["features"][key] is None
        elif type(value) is float:
            assert result["features"][key] == pytest.approx(value, rel=1e-12, abs=1e-12)
        else:
            assert type(result["features"][key]) is type(value)
            assert result["features"][key] == value
    assert set(result["missingReasons"]) == {
        key for key, value in result["features"].items() if value is None
    }
    assert all(result["patterns"].values())
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    result["route"]["confidence"]["language"] = 1
    assert payload == original


def test_local_extraction_uses_full_original_text_without_clamping(ready_engine):
    text = "😀\r\n" + ("alpha " * 401 + ".\r\n") * 10
    result = ready_engine.extract_features(text, response())
    assert result["status"] == "extracted"
    assert result["features"]["effective_length"] == 4010
    assert result["features"]["length_band"] == "above_long"
    assert result["features"]["eligible_directional"] is True
    assert result["patterns"]["descriptive_top_tokens"][0]["offsets"][0] == {
        "start": 3,
        "end": 8,
    }
    assert "humanPercentile" not in result and "signals" not in result


@pytest.mark.parametrize(
    "fault", ["infinity", "pattern_error", "missing_resource", "schema"]
)
def test_feature_failure_is_private_atomic_and_does_not_poison_the_engine(
    ready_engine, monkeypatch, fault
):
    from dataclasses import replace
    from app.services import evidence_features

    text = "private submitted text."
    payload = response()
    raw = evidence_features.extract_document_features(text, "en")

    def raise_private_error(*args):
        raise RuntimeError("private path and submitted text")

    with monkeypatch.context() as patch:
        if fault == "infinity":
            raw["mattr"] = float("inf")
            patch.setattr(
                evidence_features, "extract_document_features", lambda *args: raw
            )
        elif fault == "pattern_error":
            patch.setattr(
                evidence_features, "extract_document_patterns", raise_private_error
            )
        elif fault == "missing_resource":
            patch.setitem(
                evidence_features.LANGUAGE_PROFILES,
                "en",
                replace(
                    evidence_features.LANGUAGE_PROFILES["en"], transitions=frozenset()
                ),
            )
        else:
            patch.setattr(evidence_features, "FEATURE_SCHEMA_VERSION", 2)
        result = ready_engine.extract_features(text, payload)
    assert result == {
        "status": "failed",
        "artifactVersion": ready_engine.artifact_version,
        "featureSchemaVersion": 1,
        "route": payload["route"],
        "features": None,
        "missingReasons": {},
        "patterns": None,
        "reason": "feature_extraction_failed",
    }
    assert ready_engine.status == "ready"
    assert ready_engine.extract_features(text, payload)["status"] == "extracted"


@pytest.mark.parametrize(
    "quantiles,values,expected",
    [
        ([5.0] * 101, [4, 5, 6], [0, 50, 100]),
        (list(range(101)), [-1, 0, 5, 5.2, 95, 100, 101], [0, 0, 5, 5.5, 95, 100, 100]),
        (
            [0] * 11 + [n for n in range(1, 10) for _ in range(10)],
            [0, 0.5, 1, 4, 9],
            [5, 10.5, 15.5, 45.5, 95.5],
        ),
        (
            [0] * 21 + [10] * 40 + [20] * 40,
            [0, 5, 10, 15, 20],
            [10, 20.5, 40.5, 60.5, 80.5],
        ),
    ],
)
def test_approximate_percentile_literal_knots_ties_and_extremes(
    quantiles, values, expected
):
    assert [_approximate_percentile(quantiles, value) for value in values] == expected


def test_reference_position_uses_closed_value_bounds_including_ties():
    q = list(range(101))
    assert [_reference_position(q, x) for x in (4.999, 5, 50, 95, 95.001)] == [
        "below",
        "within",
        "within",
        "within",
        "above",
    ]
    assert [_reference_position([5] * 101, x) for x in (4, 5, 6)] == [
        "below",
        "within",
        "above",
    ]
    # Both endpoint ties are inside even though their midpoint ranks lie outside 5..95.
    q = [0] * 6 + list(range(1, 90)) + [100] * 6
    assert len(q) == 101
    assert _approximate_percentile(q, 0) == 2.5
    assert _approximate_percentile(q, 100) == 97.5
    assert _reference_position(q, 0) == _reference_position(q, 100) == "within"


def test_cell_selection_rejects_unknown_exact_keys_before_fallback():
    cells = [
        {
            "scope": scope,
            "language": lang,
            "domain": domain,
            "length_band": band,
            "source_group_count": 10,
        }
        for scope, lang, domain, band in (
            ("exact", "en", "news", "short"),
            ("language_length", "en", None, "short"),
            ("language", "en", None, None),
        )
    ]
    assert _select_reference_cell(cells, "en", "news", "short") is cells[0]
    for key in (
        ("ja", "news", "short"),
        ("en", "other", "short"),
        ("en", "news", "above_long"),
    ):
        assert _select_reference_cell(cells, *key) is None


@pytest.fixture
def comparison_input(monkeypatch):
    """Controlled scalars through the real normalizer; no research imports or IO."""
    from app.services import evidence_features

    text = "😀\r\nalpha beta gamma delta. alpha beta gamma delta."
    raw = evidence_features.extract_document_features(text, "en")
    raw.update({name: 0.5 for names in FEATURES.values() for name in names})
    raw.update(
        section_heading_count=1,
        effective_length=400,
        length_band="short",
        sentence_count=10,
        eligible_directional=True,
        eligibility_reason="eligible",
    )
    patterns = evidence_features.extract_document_patterns(text, "en")
    calls = []

    def extract(value, language):
        calls.append(("features", value, language))
        return raw.copy()

    def locate(value, language):
        calls.append(("patterns", value, language))
        return copy.deepcopy(patterns)

    monkeypatch.setattr(evidence_features, "extract_document_features", extract)
    monkeypatch.setattr(evidence_features, "extract_document_patterns", locate)
    return {"raw": raw, "text": text, "patterns": patterns, "calls": calls}


def reference_cell(engine, scope="exact"):
    return next(
        cell
        for cell in engine.reference["cells"]
        if cell["scope"] == scope
        and cell["language"] == "en"
        and cell["domain"] == ("news" if scope == "exact" else None)
        and cell["length_band"] == (None if scope == "language" else "short")
    )


def signal_for(result, name):
    return next(signal for signal in result["signals"] if signal["metric"] == name)


def test_analysis_preserves_all_metrics_counts_offsets_and_single_extraction(
    ready_engine, comparison_input
):
    payload = response()
    payload["route"]["confidence"] = {"language": 0, "domain": 0}
    original_payload = copy.deepcopy(payload)
    original_reference = copy.deepcopy(ready_engine.reference)
    result = ready_engine.analyze(comparison_input["text"], payload, main_label="AI")
    assert result["status"] == "partial"
    assert result["quality"] == {
        "level": "partial",
        "coverage": 19 / 22,
        "reasons": ["reference_metrics_unavailable"],
    }
    assert result["route"] == {
        **payload["route"],
        "lengthBucket": "short",
        "fallbackLevel": "exact",
    }
    assert [signal["metric"] for signal in result["signals"]] == [
        name for names in FEATURES.values() for name in names
    ]
    assert len(result["signals"]) == 22
    assert comparison_input["calls"] == [
        (kind, comparison_input["text"], "en") for kind in ("features", "patterns")
    ]
    assert result["patterns"] == comparison_input["patterns"]
    for name in NO_DATA_FEATURES:
        signal = signal_for(result, name)
        assert signal["observed"] == 0.5 and signal["sampleCount"] == 0
        assert (
            signal["humanPercentile"]
            is signal["aiPercentile"]
            is signal["relation"]
            is None
        )
        assert signal["referenceRanges"] is signal["notice"] is None
        assert signal["reasons"] == ["no_valid_source_groups"]
    assert signal_for(result, "mattr")["offsets"] == []
    for name, category in (
        ("repeat_ngram_coverage", "repeated_phrases"),
        ("sentence_start_repeat", "sentence_start_templates"),
    ):
        offsets = signal_for(result, name)["offsets"]
        assert offsets == [
            offset
            for item in result["patterns"][category]
            for offset in item["offsets"]
        ]
        assert offsets[0]["start"] == 3
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    result["route"]["confidence"]["language"] = 0.5
    result["signals"][0]["referenceRanges"]["human"][0] = -100
    assert payload == original_payload and ready_engine.reference == original_reference


@pytest.mark.parametrize(
    "available,level",
    [(0, "insufficient"), (1, "partial"), (19, "partial"), (22, "ready")],
)
def test_analysis_quality_uses_fixed_22_metric_denominator(
    ready_engine, comparison_input, available, level
):
    for index, metric in enumerate(reference_cell(ready_engine)["metrics"]):
        ready = index < available
        metric.update(
            status="ready" if ready else "no_data",
            reason=None if ready else "no_valid_source_groups",
            sample_count=10 if ready else 0,
            valid_pair_count=10 if ready else 0,
            human_quantiles=[0.5] * 101 if ready else None,
            ai_quantiles=[0.5] * 101 if ready else None,
        )
    result = ready_engine.analyze(
        comparison_input["text"], response(), main_label="human"
    )
    assert result["status"] == result["quality"]["level"] == level
    assert result["quality"]["coverage"] == available / 22
    assert (
        sum(signal["relation"] is not None for signal in result["signals"]) == available
    )
    if not available:
        assert "no_comparable_metrics" in result["quality"]["reasons"]


@pytest.mark.parametrize("scope", ["exact", "language_length", "language", None])
def test_analysis_cell_n9_n10_fallback_never_crosses_language(
    ready_engine, comparison_input, scope
):
    for candidate in ("exact", "language_length", "language"):
        if candidate == scope:
            break
        cell = reference_cell(ready_engine, candidate)
        cell.update(
            source_group_count=9,
            gated_pair_count=9,
            status="insufficient_n",
            reason="fewer_than_10_source_groups",
        )
        for metric in cell["metrics"]:
            if metric["status"] == "ready":
                metric.update(
                    sample_count=9,
                    valid_pair_count=9,
                    status="insufficient_n",
                    reason="fewer_than_10_source_groups",
                    human_quantiles=None,
                    ai_quantiles=None,
                )
    result = ready_engine.analyze(comparison_input["text"], response(), main_label="AI")
    assert result["route"]["fallbackLevel"] == (scope or "unavailable")
    assert result["status"] == ("partial" if scope else "insufficient")
    assert result["quality"]["coverage"] == (19 / 22 if scope else 0)
    if scope and scope != "exact":
        assert f"reference_fallback_{scope}" in result["quality"]["reasons"]
    if scope is None:
        assert result["quality"]["reasons"] == ["reference_cell_unavailable"]
        assert all(signal["sampleCount"] is None for signal in result["signals"])


def test_analysis_never_falls_back_for_an_individual_metric(
    ready_engine, comparison_input
):
    metric = next(
        metric
        for metric in reference_cell(ready_engine)["metrics"]
        if metric["feature"] == "punctuation_entropy"
    )
    metric.update(
        sample_count=9,
        valid_pair_count=9,
        status="insufficient_n",
        reason="fewer_than_10_source_groups",
        human_quantiles=None,
        ai_quantiles=None,
    )
    assert (
        next(
            metric
            for metric in reference_cell(ready_engine, "language_length")["metrics"]
            if metric["feature"] == "punctuation_entropy"
        )["status"]
        == "ready"
    )
    result = ready_engine.analyze(comparison_input["text"], response(), main_label="AI")
    signal = signal_for(result, "punctuation_entropy")
    assert result["route"]["fallbackLevel"] == "exact"
    assert result["quality"]["coverage"] == 18 / 22
    assert signal["sampleCount"] == 9 and signal["relation"] is None
    assert signal["reasons"] == ["fewer_than_10_source_groups"]


@pytest.mark.parametrize(
    "band,eligible,reason",
    [
        ("below_minimum", False, "below_minimum_length"),
        ("above_long", True, "eligible"),
        ("short", False, "fewer_than_10_sentences"),
        ("short", False, "excluded_content_over_40pct"),
    ],
)
def test_analysis_eligibility_blocks_comparison_but_keeps_observations(
    ready_engine, comparison_input, band, eligible, reason
):
    comparison_input["raw"].update(
        length_band=band, eligible_directional=eligible, eligibility_reason=reason
    )
    result = ready_engine.analyze(comparison_input["text"], response(), main_label="AI")
    assert result["status"] == "insufficient"
    assert result["quality"]["coverage"] == 0
    assert result["route"]["lengthBucket"] == band
    assert result["route"]["fallbackLevel"] == "unavailable"
    assert result["patterns"] == comparison_input["patterns"]
    assert len(result["signals"]) == 22
    assert signal_for(result, "mattr")["observed"] == 0.5
    assert all(
        signal["humanPercentile"] is None and signal["relation"] is None
        for signal in result["signals"]
    )
    assert ("length_out_of_range" if eligible else reason) in result["quality"][
        "reasons"
    ]


def test_analysis_preserves_both_observed_and_reference_missing_reasons(
    ready_engine, comparison_input
):
    comparison_input["raw"]["mattr"] = float("nan")
    comparison_input["raw"]["intro_conclusion_jaccard"] = float("nan")
    result = ready_engine.analyze(comparison_input["text"], response(), main_label="AI")
    assert result["quality"]["coverage"] == 18 / 22
    assert signal_for(result, "mattr")["reasons"] == ["insufficient_observations"]
    missing = signal_for(result, "intro_conclusion_jaccard")
    assert missing["observed"] is None and missing["sampleCount"] == 0
    assert missing["reasons"] == ["insufficient_observations", "no_valid_source_groups"]
    assert missing["relation"] is missing["notice"] is None
    json.dumps(result, allow_nan=False)


def test_analysis_label_changes_only_metric_notices_without_voting(
    ready_engine, comparison_input
):
    metrics = {
        metric["feature"]: metric for metric in reference_cell(ready_engine)["metrics"]
    }
    metrics["mattr"].update(human_quantiles=[0.5] * 101, ai_quantiles=[1.5] * 101)
    metrics["token_entropy"].update(
        human_quantiles=[1.5] * 101, ai_quantiles=[0.5] * 101
    )
    metrics["entropy_per_log_vocab"].update(
        human_quantiles=[1.5] * 101, ai_quantiles=[2.5] * 101
    )
    results = [
        ready_engine.analyze(comparison_input["text"], response(), main_label=label)
        for label in ("AI", "human")
    ]
    for result in results:
        assert set(result) == {
            "status",
            "artifactVersion",
            "featureSchemaVersion",
            "route",
            "quality",
            "signals",
            "patterns",
        }
        assert signal_for(result, "entropy_per_log_vocab")["notice"] == "outside_both"
        assert signal_for(result, "mattr")["referenceRanges"] == {
            "human": [0.5, 0.5],
            "ai": [1.5, 1.5],
        }
        assert signal_for(result, "mattr")["relation"] == {
            "human": "within",
            "ai": "below",
        }
    assert signal_for(results[0], "mattr")["notice"] == "reference_mismatch"
    assert signal_for(results[0], "token_entropy")["notice"] is None
    assert signal_for(results[1], "mattr")["notice"] is None
    assert signal_for(results[1], "token_entropy")["notice"] == "reference_mismatch"
    for result in results:
        for signal in result["signals"]:
            signal.pop("notice")
    assert results[0] == results[1]


@pytest.mark.parametrize("label", [None, True, "mixed", "unknown", ""])
def test_analysis_rejects_invalid_main_labels_without_computing(
    ready_engine, comparison_input, label
):
    result = ready_engine.analyze(
        comparison_input["text"], response(), main_label=label
    )
    assert result["status"] == "failed"
    assert result["quality"]["reasons"] == ["invalid_main_label"]
    assert result["signals"] == [] and result["patterns"] is None
    assert comparison_input["calls"] == []


def test_analysis_failure_discards_partial_signals_and_keeps_engine_usable(
    ready_engine, comparison_input, monkeypatch
):
    import app.services.evidence_engine as module

    original = module._approximate_percentile
    calls = []

    def broken_comparison(*args):
        calls.append(args)
        if len(calls) == 3:
            raise RuntimeError("private text and path")
        return original(*args)

    with monkeypatch.context() as patch:
        patch.setattr(module, "_approximate_percentile", broken_comparison)
        result = ready_engine.analyze(
            comparison_input["text"], response(), main_label="AI"
        )
    assert len(calls) == 3
    assert result["status"] == "failed" and result["route"] is None
    assert result["signals"] == [] and result["patterns"] is None
    assert result["quality"] == {
        "level": "unavailable",
        "coverage": 0.0,
        "reasons": ["comparison_failed"],
    }
    assert ready_engine.status == "ready"
    assert (
        ready_engine.analyze(comparison_input["text"], response(), main_label="AI")[
            "status"
        ]
        == "partial"
    )
