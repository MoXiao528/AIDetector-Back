"""Optional Evidence V1 routing and local analysis; never changes the main result."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import logging
import math
import re
import zipfile
from bisect import bisect_left, bisect_right
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.repre_guard_client import RepreGuardClient

logger = logging.getLogger(__name__)


MAX_BUNDLE_BYTES = 32 * 1024 * 1024
LANGUAGES = ("ar", "de", "en", "es", "fr", "pt", "ru", "zh")
DOMAINS = ("academic", "news", "novel", "seo", "webtext", "wiki")
LENGTH_BANDS = ("short", "medium", "long")
METRICS = {
    **dict.fromkeys(
        (
            "mattr",
            "token_entropy",
            "entropy_per_log_vocab",
            "hapax_type_ratio",
            "top_token_concentration",
        ),
        "lexical",
    ),
    **dict.fromkeys(
        ("repeat_ngram_coverage", "sentence_start_repeat"), "phrase_template"
    ),
    **dict.fromkeys(
        (
            "sentence_length_median",
            "sentence_length_iqr",
            "sentence_length_cv",
            "sentence_adjacent_change_median",
            "paragraph_length_median",
            "paragraph_length_iqr",
            "paragraph_length_cv",
            "punctuation_per_1k",
            "punctuation_entropy",
        ),
        "rhythm",
    ),
    **dict.fromkeys(
        (
            "transition_per_1k",
            "transition_diversity",
            "paragraph_adjacent_jaccard",
            "paragraph_nonadjacent_jaccard_q90",
            "intro_conclusion_jaccard",
            "section_heading_count",
        ),
        "discourse",
    ),
}
FAILED_ROUTER_REASONS = {
    "model_unavailable",
    "model_failure",
    "busy",
    "timeout",
    "language_undetermined",
}


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Invalid Evidence contract")


def _keys(value: Any, names: str) -> None:
    _require(type(value) is dict and set(value) == set(names.split()))


def _matches(value: Any, expected: Any) -> bool:
    """Exact JSON types, including rejecting bool where an integer is required."""
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return value.keys() == expected.keys() and all(
            _matches(value[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            _matches(a, b) for a, b in zip(value, expected)
        )
    return value == expected


def _sha(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def _parse_float(value: str) -> float:
    parsed = float(value)
    _require(math.isfinite(parsed))
    return parsed


def _read_json(encoded: bytes) -> dict:
    value = json.loads(
        encoded.decode("utf-8"),
        object_pairs_hook=_json_object,
        parse_constant=_reject_constant,
        parse_float=_parse_float,
    )
    _require(type(value) is dict)
    return value


def _validate_manifest(manifest: dict) -> str:
    _keys(manifest, "schema_version kind reference router governance privacy")
    _require(
        type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
    )
    _require(manifest["kind"] == "evidence_v1_reference_bundle")
    _require(
        _matches(
            manifest["reference"],
            {
                "path": "reference.json",
                "reference_schema_version": 1,
                "feature_schema_version": 1,
            },
        )
    )
    router = manifest["router"]
    _keys(
        router,
        "artifact_sha256 contract_kind distribution format legacy_joblib_allowed model_file_count routing",
    )
    _require(_sha(router["artifact_sha256"]))
    _require(
        _matches(
            {
                k: v
                for k, v in router.items()
                if k not in {"artifact_sha256", "routing"}
            },
            {
                "contract_kind": "production_final_always_route",
                "distribution": "external_directory",
                "format": "xlm_roberta_safetensors_directory",
                "legacy_joblib_allowed": False,
                "model_file_count": 6,
            },
        )
    )
    _require(
        _matches(
            router["routing"],
            {
                "languages": list(LANGUAGES),
                "domains": list(DOMAINS),
                "classes": [
                    f"{lang}:{domain}" for lang, domain in product(LANGUAGES, DOMAINS)
                ],
                "temperature": 1.0,
                "abstention_enabled": False,
                "always_route_supported_8x6": True,
                "confidence_role": "diagnostic_only_not_probability_of_correctness",
                "domain_selection": "argmax_within_selected_language",
                "global_joint_argmax_is_route": False,
                "language_selection": "argmax_of_domain_aggregated_language_mass",
            },
        )
    )
    _require(
        _matches(
            manifest["privacy"],
            dict.fromkeys(
                (
                    "absolute_paths_included",
                    "document_text_included",
                    "generator_included",
                    "raw_corpus_included",
                    "record_identifiers_included",
                ),
                False,
            ),
        )
    )
    # These are immutable EV3 facts, not authorization to enable production serving.
    _require(
        _matches(
            manifest["governance"],
            {
                "candidate_under_waiver": True,
                "classifier_release_basis_passed": True,
                "owner_approved": True,
                "release_disposition": "APPROVED_WITH_WAIVERS",
                "serve_eligible": False,
                "methodology_status": {
                    "abstain": {
                        "requirement_disposition": "WAIVED_BY_OWNER",
                        "status": "NOT_CERTIFIED",
                    },
                    "fresh_external_or_prospective_confirmation": {
                        "requirement_disposition": "WAIVED_FOR_V1_PRELAUNCH",
                        "status": "NOT_PERFORMED",
                    },
                },
                "production_fit_evaluation": {
                    "accuracy_evaluated": False,
                    "gate_evaluated": False,
                    "macro_f1_evaluated": False,
                    "reason": "all_eligible_documents_are_fit_data",
                },
            },
        )
    )
    return router["artifact_sha256"]


def _validate_count_status(value: dict, count: int) -> None:
    _require(type(count) is int and count >= 0)
    status, reason = (
        ("no_data", "no_valid_source_groups")
        if count == 0
        else ("insufficient_n", "fewer_than_10_source_groups")
        if count < 10
        else ("ready", None)
    )
    _require(value["status"] == status and value["reason"] == reason)


def _validate_reference(reference: dict) -> None:
    _keys(
        reference,
        "cells feature_schema_version reference_schema_version method source summary",
    )
    for key in ("feature_schema_version", "reference_schema_version"):
        _require(type(reference[key]) is int and reference[key] == 1)
    # Source SHA and offline summaries remain producer evidence, not Runtime pins.
    _require(type(reference["source"]) is dict and type(reference["summary"]) is dict)
    _require(
        _matches(
            reference["method"],
            {
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
                    "population": "paired_common_valid_human_ai_source_groups",
                    "quantile_method": "inverted_cdf",
                    "source_group_reducer": "median",
                    "runtime_percentile_semantics": "approximate_midrank_from_1pp_inverse_ecdf_knots",
                    "use": "descriptive_percentiles_only_no_direction_or_product_relation",
                },
                "status_contract": {
                    "insufficient_n": "1_to_9_source_groups",
                    "no_data": "zero_valid_source_groups",
                    "ready": "at_least_10_source_groups",
                },
                "strict_length_gate": {
                    "length_bands": list(LENGTH_BANDS),
                    "length_ratio_inclusive": [0.8, 1.25],
                    "requires_both_directional_eligible": True,
                    "requires_same_length_band": True,
                    "unit": "pair_before_source_group_aggregation",
                },
            },
        )
    )
    expected_cells = {
        ("exact", *parts) for parts in product(LANGUAGES, DOMAINS, LENGTH_BANDS)
    }
    expected_cells.update(
        ("language_length", lang, None, band)
        for lang, band in product(LANGUAGES, LENGTH_BANDS)
    )
    expected_cells.update(("language", lang, None, None) for lang in LANGUAGES)
    _require(
        type(reference["cells"]) is list
        and len(reference["cells"]) == len(expected_cells)
    )
    for cell in reference["cells"]:
        _keys(
            cell,
            "scope language domain length_band source_group_count gated_pair_count status reason metrics",
        )
        key = tuple(cell[k] for k in ("scope", "language", "domain", "length_band"))
        _require(key in expected_cells)
        expected_cells.remove(key)
        count, pairs = cell["source_group_count"], cell["gated_pair_count"]
        _validate_count_status(cell, count)
        _require(type(pairs) is int and pairs >= count)
        _require(type(cell["metrics"]) is list and len(cell["metrics"]) == len(METRICS))
        remaining = set(METRICS)
        for metric in cell["metrics"]:
            _keys(
                metric,
                "feature dimension sample_count valid_pair_count status reason human_quantiles ai_quantiles",
            )
            feature = metric["feature"]
            _require(feature in remaining and metric["dimension"] == METRICS[feature])
            remaining.remove(feature)
            samples, valid_pairs = metric["sample_count"], metric["valid_pair_count"]
            _validate_count_status(metric, samples)
            _require(
                samples <= count
                and type(valid_pairs) is int
                and samples <= valid_pairs <= pairs
            )
            for name in ("human_quantiles", "ai_quantiles"):
                quantiles = metric[name]
                if samples < 10:
                    _require(quantiles is None)
                else:
                    _require(type(quantiles) is list and len(quantiles) == 101)
                    _require(all(_number(value) for value in quantiles))
                    _require(all(a <= b for a, b in zip(quantiles, quantiles[1:])))


def _failure(reason: str) -> dict:
    return {
        "schemaVersion": 1,
        "status": "failed",
        "routerArtifactSha256": None,
        "route": None,
        "reason": reason,
    }


def _select_reference_cell(
    cells: list[dict], language: str, domain: str, length_band: str
) -> dict | None:
    """Preserve the producer's cell-atomic fallback, including its exact-key gate."""
    index = {
        (cell["scope"], cell["language"], cell["domain"], cell["length_band"]): cell
        for cell in cells
    }
    exact = ("exact", language, domain, length_band)
    if exact not in index:
        return None
    for key in (
        exact,
        ("language_length", language, None, length_band),
        ("language", language, None, None),
    ):
        cell = index.get(key)
        if cell is not None and cell["source_group_count"] >= 10:
            return cell
    return None


def _approximate_percentile(quantiles: list[float], observed: float) -> float:
    """Midpoint of 1pp knot ranks, not an exact ECDF or source probability."""
    _require(_number(observed))
    return min(
        100.0,
        max(
            0.0,
            (bisect_left(quantiles, observed) + bisect_right(quantiles, observed) - 1)
            / 2,
        ),
    )


def _reference_position(quantiles: list[float], observed: float) -> str:
    # Compare values, not approximate ranks: ties can span either interval boundary.
    return (
        "below"
        if observed < quantiles[5]
        else "above"
        if observed > quantiles[95]
        else "within"
    )


class EvidenceEngine:
    """Explicitly constructed once by its owner; failures stay local and are cached."""

    def __init__(
        self,
        *,
        mode: str = "off",
        bundle_path: str = "",
        bundle_sha256: str = "",
        timeout_seconds: str | float = "12",
    ) -> None:
        self.status = "off"
        self.reason: str | None = None
        self.artifact_version: str | None = None
        self.router_artifact_sha256: str | None = None
        self.reference: dict | None = None
        self.timeout_seconds = 0.0
        self._task: asyncio.Task | None = None
        if mode == "off":
            return
        self.status = "failed"
        try:
            timeout = float(timeout_seconds)
            _require(type(timeout_seconds) is not bool and 0 < timeout <= 60)
        except (TypeError, ValueError, OverflowError):
            self.reason = "invalid_evidence_config"
            return
        if (
            mode not in ("shadow", "serve")
            or not bundle_path
            or not _sha(bundle_sha256)
        ):
            self.reason = "invalid_evidence_config"
            return
        try:
            with Path(bundle_path).open("rb") as source:
                encoded = source.read(MAX_BUNDLE_BYTES + 1)
            _require(len(encoded) <= MAX_BUNDLE_BYTES)
            _require(hashlib.sha256(encoded).hexdigest() == bundle_sha256)
            # Parse exactly the bytes just hashed; never extract ZIP paths to disk.
            with zipfile.ZipFile(io.BytesIO(encoded)) as archive:
                members = archive.infolist()
                _require(
                    len(members) == 2
                    and {m.filename for m in members}
                    == {"manifest.json", "reference.json"}
                )
                _require(sum(m.file_size for m in members) <= MAX_BUNDLE_BYTES)
                _require(all(not m.is_dir() and not m.flag_bits & 1 for m in members))
                manifest = _read_json(archive.read("manifest.json"))
                reference = _read_json(archive.read("reference.json"))
            router_sha = _validate_manifest(manifest)
            _validate_reference(reference)
        except Exception:
            # Never surface paths, raw JSON or library exceptions to the main result.
            self.reason = "invalid_evidence_bundle"
            return
        self.reference = reference
        self.router_artifact_sha256 = router_sha
        self.artifact_version = bundle_sha256
        self.timeout_seconds = timeout
        self.status = "ready"

    async def run(
        self, text: str, *, main_label: str, client: RepreGuardClient
    ) -> dict:
        """One whole-document call and comparison under a single deadline."""
        if self.status != "ready":
            return self._empty_result(self.reason)
        started = asyncio.get_running_loop().time()
        result = None
        try:
            # ponytail: one active task per process, no queue; revisit after shadow load data.
            if self._task is not None and not self._task.done():
                result = self._empty_result("busy")
            else:
                self._task = asyncio.create_task(
                    self._route_and_compare(
                        text,
                        main_label=main_label,
                        client=client,
                        deadline=started + self.timeout_seconds,
                    )
                )
                self._task.add_done_callback(self._release_task)
                result = await asyncio.wait_for(
                    asyncio.shield(self._task), timeout=self.timeout_seconds
                )
        except asyncio.TimeoutError:
            result = self._empty_result("timeout")
        finally:
            logger.info(
                "Evidence status=%s elapsed_ms=%.1f",
                result["status"] if result is not None else "cancelled",
                (asyncio.get_running_loop().time() - started) * 1000,
            )
        return result

    async def _route_and_compare(
        self, text: str, *, main_label: str, client: RepreGuardClient, deadline: float
    ) -> dict:
        import httpx
        from app.services.repre_guard_client import RepreGuardError

        try:
            # Bound HTTP itself too: a cancelled caller must not leave a 60s request.
            encoded = await asyncio.wait_for(
                client.route_evidence(text),
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return self._empty_result("timeout")
        except RepreGuardError as exc:
            return self._empty_result(
                "invalid_router_response"
                if exc.code == "DETECT_SERVICE_RESPONSE_TOO_LARGE"
                else "model_unavailable"
            )
        except httpx.RequestError:
            return self._empty_result("model_unavailable")
        except Exception:
            return self._empty_result("model_failure")
        try:
            payload = _read_json(encoded)
        except Exception:
            return self._empty_result("invalid_router_response")
        if asyncio.get_running_loop().time() >= deadline:
            return self._empty_result("timeout")
        try:
            return await asyncio.to_thread(
                self.analyze, text, payload, main_label=main_label
            )
        except Exception:
            return self._empty_result("comparison_failed")

    def _release_task(self, task: asyncio.Task) -> None:
        if self._task is task:
            self._task = None
        if not task.cancelled():
            task.exception()

    async def drain(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    def _empty_result(self, reason: str | None) -> dict:
        return {
            "status": "off" if self.status == "off" else "failed",
            "artifactVersion": self.artifact_version,
            "featureSchemaVersion": 1,
            "route": None,
            "quality": {
                "level": "unavailable",
                "coverage": 0.0,
                "reasons": [reason] if reason else [],
            },
            "signals": [],
            "patterns": None,
        }

    def validate_router_response(self, payload: Any) -> dict:
        if self.status != "ready":
            return _failure("bundle_unavailable")
        try:
            _keys(payload, "schemaVersion status routerArtifactSha256 route reason")
            _require(
                type(payload["schemaVersion"]) is int and payload["schemaVersion"] == 1
            )
            status, sha, route, reason = (
                payload[k]
                for k in ("status", "routerArtifactSha256", "route", "reason")
            )
            if status == "failed":
                _require(route is None and reason in FAILED_ROUTER_REASONS)
                _require(
                    sha is None or (_sha(sha) and sha == self.router_artifact_sha256)
                )
            else:
                _require(_sha(sha) and sha == self.router_artifact_sha256)
                if status == "unsupported":
                    _require(route is None and reason == "unsupported_language")
                else:
                    _require(status == "routed" and reason is None)
                    _keys(route, "language domain confidence")
                    _require(
                        route["language"] in LANGUAGES and route["domain"] in DOMAINS
                    )
                    _keys(route["confidence"], "language domain")
                    _require(
                        all(
                            _number(v) and 0 <= v <= 1
                            for v in route["confidence"].values()
                        )
                    )
            return copy.deepcopy(payload)
        except Exception:
            return _failure("invalid_router_response")

    def extract_features(self, text: Any, router_response: Any) -> dict:
        """Compute local features from the original text, without Reference comparison."""
        result = {
            "status": "off" if self.status == "off" else "failed",
            "artifactVersion": self.artifact_version,
            "featureSchemaVersion": 1,
            "route": None,
            "features": None,
            "missingReasons": {},
            "patterns": None,
            "reason": self.reason,
        }
        if self.status != "ready":
            return result
        router = self.validate_router_response(router_response)
        if router["status"] != "routed":
            result.update(status=router["status"], reason=router["reason"])
            return result
        result["route"] = router["route"]
        if type(text) is not str:
            result["reason"] = "invalid_text"
            return result
        try:
            # Keep optional feature dependencies outside startup/off/failure paths.
            from app.services import evidence_features

            _require(
                type(evidence_features.FEATURE_SCHEMA_VERSION) is int
                and evidence_features.FEATURE_SCHEMA_VERSION == 1
            )
            language = router["route"]["language"]
            features, missing_reasons = evidence_features.normalize_features(
                evidence_features.extract_document_features(text, language), language
            )
            patterns = evidence_features.extract_document_patterns(text, language)
        except Exception:
            # A request failure must not poison the loaded Bundle or leak input/errors.
            result["reason"] = "feature_extraction_failed"
            return result
        result.update(
            status="extracted",
            features=features,
            missingReasons=missing_reasons,
            patterns=patterns,
            reason=None,
        )
        return result

    def analyze(self, text: Any, router_response: Any, *, main_label: str) -> dict:
        """Describe reference positions; the supplied main label is never recomputed."""
        result = self._empty_result(self.reason)
        if self.status != "ready":
            return result
        if type(main_label) is not str or main_label.casefold() not in ("ai", "human"):
            result["quality"]["reasons"] = ["invalid_main_label"]
            return result
        main_label = main_label.casefold()
        other_label = "human" if main_label == "ai" else "ai"
        try:
            extracted = self.extract_features(text, router_response)
            if extracted["status"] != "extracted":
                result["status"] = extracted["status"]
                result["quality"]["reasons"] = (
                    [extracted["reason"]] if extracted["reason"] else []
                )
                return result
            features, patterns = extracted["features"], extracted["patterns"]
            route = {
                **extracted["route"],
                "lengthBucket": features["length_band"],
                "fallbackLevel": "unavailable",
            }
            blocked = []
            if not features["eligible_directional"]:
                blocked.extend(features["eligibility_reason"].split(";"))
            if features["length_band"] not in LENGTH_BANDS:
                blocked.append("length_out_of_range")
            cell = (
                None
                if blocked
                else _select_reference_cell(
                    self.reference["cells"],
                    route["language"],
                    route["domain"],
                    route["lengthBucket"],
                )
            )
            if cell is None and not blocked:
                blocked.append("reference_cell_unavailable")
            quality_reasons = blocked.copy()
            reference_metrics = {}
            if cell is not None:
                route["fallbackLevel"] = cell["scope"]
                reference_metrics = {
                    metric["feature"]: metric for metric in cell["metrics"]
                }
                if cell["scope"] != "exact":
                    quality_reasons.append(f"reference_fallback_{cell['scope']}")

            signals = []
            comparable = 0
            for name, dimension in METRICS.items():
                observed = features[name]
                _require(observed is None or _number(observed))
                reference = reference_metrics[name] if cell is not None else None
                reasons = blocked.copy()
                if observed is None:
                    reasons.append(extracted["missingReasons"][name])
                    quality_reasons.append("missing_observations")
                if reference is not None and reference["status"] != "ready":
                    reasons.append(reference["reason"])
                    quality_reasons.append("reference_metrics_unavailable")
                category = {
                    "repeat_ngram_coverage": "repeated_phrases",
                    "sentence_start_repeat": "sentence_start_templates",
                }.get(name)
                signal = {
                    "dimension": dimension,
                    "metric": name,
                    "observed": observed,
                    "humanPercentile": None,
                    "aiPercentile": None,
                    "referenceRanges": None,
                    "relation": None,
                    "notice": None,
                    "sampleCount": reference["sample_count"]
                    if reference is not None
                    else None,
                    "offsets": [
                        offset.copy()
                        for item in patterns[category]
                        for offset in item["offsets"]
                    ]
                    if category
                    else [],
                    "reasons": reasons,
                }
                if not reasons:
                    quantiles = {
                        side: reference[f"{side}_quantiles"] for side in ("human", "ai")
                    }
                    relation = {
                        side: _reference_position(q, observed)
                        for side, q in quantiles.items()
                    }
                    notice = None
                    if all(position != "within" for position in relation.values()):
                        notice = "outside_both"
                    elif (
                        relation[main_label] != "within"
                        and relation[other_label] == "within"
                    ):
                        notice = "reference_mismatch"
                    signal.update(
                        humanPercentile=_approximate_percentile(
                            quantiles["human"], observed
                        ),
                        aiPercentile=_approximate_percentile(quantiles["ai"], observed),
                        referenceRanges={
                            side: [q[5], q[95]] for side, q in quantiles.items()
                        },
                        relation=relation,
                        notice=notice,
                    )
                    comparable += 1
                signals.append(signal)
            level = (
                "ready"
                if comparable == len(METRICS)
                else "partial"
                if comparable
                else "insufficient"
            )
            if comparable == 0 and not blocked:
                quality_reasons.append("no_comparable_metrics")
            result.update(
                status=level,
                route=route,
                signals=signals,
                patterns=patterns,
                quality={
                    "level": level,
                    "coverage": comparable / len(METRICS),
                    "reasons": list(dict.fromkeys(quality_reasons)),
                },
            )
        except Exception:
            # Publish no partial comparisons or private exceptions on a request failure.
            result.update(
                status="failed",
                route=None,
                signals=[],
                patterns=None,
                quality={
                    "level": "unavailable",
                    "coverage": 0.0,
                    "reasons": ["comparison_failed"],
                },
            )
        return result
