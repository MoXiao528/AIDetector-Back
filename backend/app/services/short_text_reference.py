"""Validated optional Chinese brief-text references; frozen V1 features stay intact."""

from __future__ import annotations

from collections import Counter
from typing import Any


LENGTH_BANDS = {"brief_200_399": [200, 399], "brief_400_599": [400, 599]}
MINIMUM_OBSERVATIONS = {
    **dict.fromkeys(
        (
            "sentence_start_repeat",
            "sentence_length_iqr",
            "sentence_length_cv",
            "sentence_adjacent_change_median",
        ),
        {"sentence_count": 2},
    ),
    **dict.fromkeys(
        (
            "paragraph_length_iqr",
            "paragraph_length_cv",
            "paragraph_adjacent_jaccard",
            "intro_conclusion_jaccard",
        ),
        {"paragraph_count": 2},
    ),
    "paragraph_nonadjacent_jaccard_q90": {"paragraph_count": 3},
}
METHOD = {
    "fit_split": "fit",
    "validation_split": "validation",
    "split_policy": {
        "kind": "source_group_hash_internal_holdout",
        "hash": "sha256",
        "prefix": "short-text-reference-v1:",
        "prefix_bytes": 8,
        "byteorder": "big",
        "modulus": 5,
        "validation_remainder": 0,
        "scope": "internal_holdout_not_external_confirmation",
    },
    "minimum_train_groups": 100,
    "minimum_test_groups": 50,
    "minimum_side_coverage": 0.8,
    "length_bands": LENGTH_BANDS,
    "length_ratio_inclusive": [0.8, 1.25],
    "maximum_excluded_fraction": 0.4,
    "excluded_fraction_method": "normalized_pre_placeholder_exclusion_span_union",
    "source_group_reducer": "median",
    "quantile_method": "inverted_cdf",
    "percentile_grid": list(range(101)),
    "minimum_observations": MINIMUM_OBSERVATIONS,
    "fallback_chain": ["exact", "language_length", "unavailable"],
    "metric_fallback_after_cell_selection": False,
}


def length_band(language: str, length: int) -> str | None:
    if language == "zh":
        for band, (minimum, maximum) in LENGTH_BANDS.items():
            if minimum <= length <= maximum:
                return band
    return None


def has_observations(feature: str, values: dict) -> bool:
    return all(
        values[field] >= minimum
        for field, minimum in MINIMUM_OBSERVATIONS.get(feature, {}).items()
    )


def validate_reference(
    value: Any, *, manifest_sha256: str, reference_sha256: str
) -> None:
    # Reuse the exact JSON/type checks already used by the mandatory V1 members.
    from app.services.evidence_engine import (
        DOMAINS,
        METRICS,
        _keys,
        _matches,
        _number,
        _require,
        _sha,
    )

    _keys(
        value,
        "short_text_reference_schema_version feature_schema_version source method cells summary",
    )
    for name in ("short_text_reference_schema_version", "feature_schema_version"):
        _require(type(value[name]) is int and value[name] == 1)
    _keys(
        value["source"],
        "feature_sha256 base_bundle_sha256 config_sha256 base_manifest_sha256 base_reference_sha256 pair_cache_sha256 runtime_features_sha256",
    )
    _require(all(_sha(digest) for digest in value["source"].values()))
    _require(value["source"]["base_manifest_sha256"] == manifest_sha256)
    _require(value["source"]["base_reference_sha256"] == reference_sha256)
    _require(_matches(value["method"], METHOD))
    expected = {
        (scope, "zh", domain, band)
        for band in LENGTH_BANDS
        for scope, domains in (("exact", DOMAINS), ("language_length", (None,)))
        for domain in domains
    }
    _require(type(value["cells"]) is list and len(value["cells"]) == len(expected))

    def count_status(train: int, test: int) -> tuple[str, str | None]:
        _require(type(train) is int and type(test) is int and train >= 0 and test >= 0)
        if train == 0 and test == 0:
            return "no_data", "no_valid_source_groups"
        if (
            train < METHOD["minimum_train_groups"]
            or test < METHOD["minimum_test_groups"]
        ):
            return "insufficient_n", "short_reference_insufficient_n"
        return "ready", None

    for cell in value["cells"]:
        _keys(
            cell,
            "scope language domain length_band gated_pair_count train_source_group_count test_source_group_count status reason metrics",
        )
        key = tuple(
            cell[name] for name in ("scope", "language", "domain", "length_band")
        )
        _require(key in expected)
        expected.remove(key)
        train, test = cell["train_source_group_count"], cell["test_source_group_count"]
        status, reason = count_status(train, test)
        pairs = cell["gated_pair_count"]
        _require(type(pairs) is int and pairs >= train + test)
        _require(cell["status"] == status and cell["reason"] == reason)
        _require(type(cell["metrics"]) is list and len(cell["metrics"]) == len(METRICS))
        remaining = set(METRICS)
        for metric in cell["metrics"]:
            _keys(
                metric,
                "dimension feature status reason valid_pair_count sample_count test_sample_count human_test_coverage ai_test_coverage human_quantiles ai_quantiles",
            )
            feature = metric["feature"]
            _require(feature in remaining and metric["dimension"] == METRICS[feature])
            remaining.remove(feature)
            samples, test_samples = metric["sample_count"], metric["test_sample_count"]
            status, reason = count_status(samples, test_samples)
            _require(samples <= train and test_samples <= test)
            valid_pairs = metric["valid_pair_count"]
            _require(
                type(valid_pairs) is int
                and samples + test_samples <= valid_pairs <= pairs
            )
            coverages = [metric[f"{side}_test_coverage"] for side in ("human", "ai")]
            if status == "ready":
                _require(
                    all(
                        _number(coverage) and 0 <= coverage <= 1
                        for coverage in coverages
                    )
                )
                if any(
                    coverage < METHOD["minimum_side_coverage"] for coverage in coverages
                ):
                    status, reason = "validation_failed", "reference_validation_failed"
            else:
                _require(all(coverage is None for coverage in coverages))
            _require(metric["status"] == status and metric["reason"] == reason)
            for side in ("human", "ai"):
                quantiles = metric[f"{side}_quantiles"]
                if status != "ready":
                    _require(quantiles is None)
                else:
                    _require(type(quantiles) is list and len(quantiles) == 101)
                    _require(all(_number(item) for item in quantiles))
                    _require(
                        all(
                            left <= right
                            for left, right in zip(quantiles, quantiles[1:])
                        )
                    )

    summary = value["summary"]
    _keys(
        summary,
        "cohort corrected_exclusion_audit cell_status_counts metric_status_counts",
    )
    for key, counts in (
        ("cell_status_counts", Counter(cell["status"] for cell in value["cells"])),
        (
            "metric_status_counts",
            Counter(
                metric["status"]
                for cell in value["cells"]
                for metric in cell["metrics"]
            ),
        ),
    ):
        _require(_matches(summary[key], dict(counts)))
    cohort, audit = summary["cohort"], summary["corrected_exclusion_audit"]
    _keys(
        cohort,
        "input_pairs input_rows original_test_pairs original_train_pairs selected_pairs selected_source_groups split_leakage_groups",
    )
    _keys(audit, "audited_pairs changed_document_ratios documents_over_40pct")
    _require(
        all(
            type(count) is int and count >= 0
            for count in (*cohort.values(), *audit.values())
        )
    )
    _require(cohort["input_rows"] == 2 * cohort["input_pairs"])
    _require(
        cohort["selected_source_groups"]
        <= cohort["selected_pairs"]
        <= cohort["input_pairs"]
    )
    _require(
        cohort["original_train_pairs"] + cohort["original_test_pairs"]
        == cohort["selected_pairs"]
    )
    _require(cohort["split_leakage_groups"] == 0)
    _require(
        cohort["selected_pairs"] <= audit["audited_pairs"] <= cohort["input_pairs"]
    )
    _require(audit["changed_document_ratios"] <= 2 * audit["audited_pairs"])
    _require(audit["documents_over_40pct"] <= 2 * audit["audited_pairs"])
    _require(
        sum(
            cell["gated_pair_count"]
            for cell in value["cells"]
            if cell["scope"] == "exact"
        )
        == cohort["selected_pairs"]
    )


def select_reference_cell(value: dict, domain: str, band: str) -> dict | None:
    for scope, cell_domain in (("exact", domain), ("language_length", None)):
        for cell in value["cells"]:
            if (
                cell["scope"] == scope
                and cell["domain"] == cell_domain
                and cell["length_band"] == band
                and cell["status"] == "ready"
            ):
                return cell
    return None
