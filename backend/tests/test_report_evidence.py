from copy import deepcopy
from io import BytesIO

import pytest
from pypdf import PdfReader
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate

from app.schemas.evidence import EvidenceResult
from app.services.evidence_engine import METRICS
from app.services.report_evidence import (
    _has_distinct_reference,
    _reference_chart,
    build_evidence_section,
)


def _snapshot(changes=None, *, status="partial"):
    signals = []
    for metric, dimension in METRICS.items():
        signal = {
            "dimension": dimension,
            "metric": metric,
            "observed": None,
            "humanPercentile": None,
            "aiPercentile": None,
            "referenceRanges": None,
            "relation": None,
            "notice": None,
            "sampleCount": None,
            "offsets": [],
            "reasons": [],
        }
        if changes and metric in changes:
            signal.update(changes[metric])
        signals.append(signal)
    comparable = sum(item["relation"] is not None for item in signals)
    unavailable = status in {"failed", "unsupported"}
    level = (
        "unavailable" if unavailable else "partial" if comparable else "insufficient"
    )
    return EvidenceResult.model_validate(
        {
            "status": status if unavailable else level,
            "artifactVersion": "1" * 64,
            "featureSchemaVersion": 1,
            "route": None
            if unavailable
            else {
                "language": "en",
                "domain": "wiki",
                "confidence": {"language": 1.0, "domain": 1.0},
                "lengthBucket": "short",
                "fallbackLevel": "exact",
            },
            "quality": {"level": level, "coverage": comparable / 22, "reasons": []},
            "signals": [] if unavailable else signals,
            "patterns": None
            if unavailable
            else {
                "descriptive_top_tokens": [],
                "repeated_phrases": [],
                "sentence_start_templates": [],
            },
        }
    )


def _distinct(**updates):
    return {
        "observed": 0.8,
        "referenceRanges": {"human": [0.1, 0.4], "ai": [0.6, 0.9]},
        "referenceExtent": [0.0, 1.0],
        "relation": {"human": "above", "ai": "within"},
        **updates,
    }


def _styles(locale="en-US"):
    sheet = getSampleStyleSheet()
    if locale.startswith("zh"):
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        for name in ("Heading2", "BodyText"):
            sheet[name].fontName = "STSong-Light"
    return {
        "section": sheet["Heading2"],
        "body": sheet["BodyText"],
        "muted": sheet["BodyText"],
    }


def _render(evidence, locale="en-US"):
    stream = BytesIO()
    story = build_evidence_section(
        evidence, locale=locale, styles=_styles(locale), width=460
    )
    SimpleDocTemplate(stream).build(story)
    return "\n".join(page.extract_text() for page in PdfReader(stream).pages)


def test_report_uses_ui_summary_selection_and_preserves_saved_snapshot():
    snapshot = _snapshot(
        {
            "mattr": _distinct(),
            "repeat_ngram_coverage": _distinct(
                referenceRanges={"human": [0.1, 0.9], "ai": [0.2, 1.0]}
            ),
            "sentence_length_median": _distinct(),
            "transition_diversity": _distinct(),
        }
    )
    before = deepcopy(snapshot.model_dump())
    text = _render(snapshot)
    assert "Lexical diversity" in text
    assert "Sentence rhythm" in text
    assert "Repeated phrases" not in text
    assert "transition_diversity" not in text
    assert text.index("Lexical diversity") < text.index("Sentence rhythm")
    assert "Within the typical AI range and above the typical human range." in " ".join(
        text.split()
    )
    assert "Human reference" in text and "AI reference" in text
    assert "Low" in text and "High" in text
    assert "%" not in text
    assert snapshot.model_dump() == before


def test_legacy_snapshot_uses_original_values_without_inventing_plot_extent():
    change = _distinct()
    change.pop("referenceExtent")
    snapshot = _snapshot({"mattr": change})
    text = _render(snapshot)
    assert "This value: 0.8" in text
    assert "human reference: 0.1 - 0.4" in text
    assert "no usable overall reference extent" in text
    assert "Low" not in text and "High" not in text


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("missing", "No writing characteristics are available"),
        ("failed", "could not be generated"),
        ("unsupported", "not supported for this language"),
        ("insufficient", "No reference comparison is available"),
    ],
)
def test_unavailable_states_still_produce_report(kind, expected):
    snapshot = None if kind == "missing" else _snapshot(status=kind)
    assert expected in _render(snapshot)


def test_high_overlap_and_non_summary_differences_are_not_misrepresented():
    high_overlap = _snapshot(
        {
            "mattr": _distinct(
                referenceRanges={"human": [0.0, 1.0], "ai": [0.2, 1.2]},
                referenceExtent=[0.0, 1.2],
            )
        }
    )
    assert "no clear range differences" in _render(high_overlap)
    other_metric = _snapshot({"token_entropy": _distinct()})
    assert "Range differences were found in other metrics" in _render(other_metric)


def test_reference_plot_positions_use_saved_extent_and_all_four_dimensions():
    snapshot = _snapshot(
        {
            metric: _distinct()
            for metric in [
                "mattr",
                "repeat_ngram_coverage",
                "sentence_length_cv",
                "paragraph_adjacent_jaccard",
            ]
        }
    )
    text = _render(snapshot)
    assert all(
        title in text
        for title in [
            "Lexical diversity",
            "Repetition",
            "Sentence rhythm",
            "Connections",
        ]
    )
    signal = snapshot.signals[0]
    assert _has_distinct_reference(signal)
    chart = _reference_chart(signal, 460, "Helvetica", False)
    marker = next(
        shape for shape in chart.contents if shape.__class__.__name__ == "Line"
    )
    assert marker.x1 == pytest.approx(368)
    assert marker.x1 == marker.x2


def test_chinese_pdf_has_translated_ranges_and_one_disclaimer():
    snapshot = _snapshot({"mattr": _distinct(), "sentence_length_cv": _distinct()})
    text = _render(snapshot, "zh-CN")
    assert "写作特点" in text and "词汇多样性" in text and "句子节奏" in text
    assert "落在 AI 常见范围内，且高于人工常见范围。" in text
    assert "人工参考" in text and "AI 参考" in text
    assert text.count("写作特点仅作辅助参考，不代表 AI 生成概率。") == 1
    assert "Low" not in text and "High" not in text


def test_reference_chart_keeps_wrapped_legend_above_track():
    snapshot = _snapshot({"mattr": _distinct()})
    chart = _reference_chart(snapshot.signals[0], 180, "Helvetica", False)
    legend_labels = [
        shape
        for shape in chart.contents
        if shape.__class__.__name__ == "String" and shape.y > 7
    ]
    assert len(legend_labels) == 3
    assert min(shape.y for shape in legend_labels) > 37
    assert chart.height > 61
    assert all(
        0 <= shape.x
        and shape.x + pdfmetrics.stringWidth(shape.text, "Helvetica", 8) <= 180
        for shape in legend_labels
    )


def test_saved_value_outside_reference_extent_gets_edge_marker_and_arrow():
    snapshot = _snapshot({"mattr": _distinct(observed=1.2)})
    chart = _reference_chart(snapshot.signals[0], 460, "Helvetica", False)
    lines = [shape for shape in chart.contents if shape.__class__.__name__ == "Line"]
    assert len(lines) == 4
    assert lines[0].x1 == 460
    assert "exceeds the chart range" in _render(snapshot)
