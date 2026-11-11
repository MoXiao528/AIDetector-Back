"""Print the saved writing-characteristic comparison, without recalculating it."""

from __future__ import annotations

import math
from html import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle

from app.schemas.evidence import EvidenceResult, EvidenceSignal


# Keep the PDF summary aligned with evidencePresentation.ts, not the 22-row data table.
SUMMARY_METRICS = {
    "lexical": ("mattr",),
    "phrase_template": ("repeat_ngram_coverage", "sentence_start_repeat"),
    "rhythm": ("sentence_length_median", "sentence_length_cv"),
    "discourse": ("transition_per_1k", "paragraph_adjacent_jaccard"),
}
METRIC_NAMES = {
    "mattr": ("词汇多样性", "Lexical diversity"),
    "repeat_ngram_coverage": ("词组重复", "Repeated phrases"),
    "sentence_start_repeat": ("句首重复", "Repeated sentence openings"),
    "sentence_length_median": ("句子长度", "Sentence length"),
    "sentence_length_cv": ("句长变化程度", "Sentence-length variation"),
    "transition_per_1k": ("连接词使用", "Connective use"),
    "paragraph_adjacent_jaccard": ("段间用词重合", "Vocabulary shared by paragraphs"),
}
DIMENSION_NAMES = {
    "lexical": ("词汇多样性", "Lexical diversity"),
    "phrase_template": ("重复表达", "Repetition"),
    "rhythm": ("句子节奏", "Sentence rhythm"),
    "discourse": ("衔接特点", "Connections"),
}
KNOWN_REFERENCES = {
    "8abe24fc7e7747f4e2e9b90a80bf26b7999726373fa80e8519a97dffbe7014b2",
    "f04fee602a09850adae31e5e78ea9926a27cd96663aeeeaeaafb11d8a959fb0e",
}
HUMAN_COLOR = colors.HexColor("#93C5FD")
AI_COLOR = colors.HexColor("#C4B5FD")
OVERLAP_COLOR = colors.HexColor("#CBD5E1")
TEXT_COLOR = colors.HexColor("#526174")


def _valid_range(value) -> bool:
    return (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and all(math.isfinite(item) for item in value)
        and value[0] <= value[1]
    )


def _reference_overlap(signal: EvidenceSignal) -> float | None:
    ranges = signal.referenceRanges
    if ranges is None or not _valid_range(ranges.human) or not _valid_range(ranges.ai):
        return None
    human, ai = ranges.human, ranges.ai
    start, end = max(human[0], ai[0]), min(human[1], ai[1])
    if start > end:
        return 0
    width = min(human[1] - human[0], ai[1] - ai[0])
    if width == 0:
        return 1
    if math.isfinite(width):
        return min(1, (end - start) / width)
    scale = max(*(abs(value) for value in human), *(abs(value) for value in ai))
    return (end / scale - start / scale) / min(
        human[1] / scale - human[0] / scale, ai[1] / scale - ai[0] / scale
    )


def _has_distinct_reference(signal: EvidenceSignal) -> bool:
    overlap = _reference_overlap(signal)
    return (
        signal.observed is not None
        and math.isfinite(signal.observed)
        and overlap is not None
        and overlap < 0.8 - 1e-12
        and signal.relation is not None
        and ((signal.relation.human == "within") != (signal.relation.ai == "within"))
    )


def _format_number(value: float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".") if value else "0"


def _comparison_text(signal: EvidenceSignal, zh: bool) -> str:
    human = signal.relation.human == "within"
    other = signal.relation.ai if human else signal.relation.human
    direction = ("低于" if other == "below" else "高于") if zh else other
    if zh:
        return (
            f"落在人工常见范围内，且{direction} AI 常见范围。"
            if human
            else f"落在 AI 常见范围内，且{direction}人工常见范围。"
        )
    return (
        f"Within the typical human range and {direction} the typical AI range."
        if human
        else f"Within the typical AI range and {direction} the typical human range."
    )


def _reference_chart(
    signal: EvidenceSignal, width: float, font: str, zh: bool
) -> Drawing | None:
    ranges, extent = signal.referenceRanges, signal.referenceExtent
    if ranges is None or not _valid_range(extent):
        return None
    low, high = extent
    if low > min(ranges.human[0], ranges.ai[0]) or high < max(
        ranges.human[1], ranges.ai[1]
    ):
        return None
    span, scale = high - low, max(abs(low), abs(high))

    def position(value: float) -> float:
        if value < low:
            return 0
        if value > high:
            return width
        if low == high:
            return width / 2
        fraction = (
            (value - low) / span
            if math.isfinite(span)
            else ((value / scale - low / scale) / (high / scale - low / scale))
        )
        return fraction * width

    overlap_start = max(ranges.human[0], ranges.ai[0])
    overlap_end = min(ranges.human[1], ranges.ai[1])
    legend = [
        (HUMAN_COLOR, "人工参考" if zh else "Human reference"),
        (AI_COLOR, "AI 参考" if zh else "AI reference"),
    ]
    if overlap_start <= overlap_end:
        legend.append((OVERLAP_COLOR, "两类共同范围" if zh else "Shared range"))
    legend.append((colors.HexColor("#182230"), "本次值" if zh else "This value"))
    legend_positions = []
    x, row = 0, 0
    for color, label in legend:
        label_width = stringWidth(label, font, 8)
        if x + 14 + label_width > width:
            row += 1
            x = 0
        legend_positions.append((x, row, color, label))
        x += 25 + label_width
    chart = Drawing(width, 61 + row * 13)
    for x, row, color, label in legend_positions:
        y = chart.height - 10 - row * 13
        chart.add(Rect(x, y, 7, 7, fillColor=color, strokeColor=None))
        chart.add(
            String(x + 12, y, label, fontName=font, fontSize=8, fillColor=TEXT_COLOR)
        )
    track_y = 24
    chart.add(
        Rect(
            0, track_y, width, 8, fillColor=colors.HexColor("#F1F4F8"), strokeColor=None
        )
    )
    for bounds, color in [(ranges.human, HUMAN_COLOR), (ranges.ai, AI_COLOR)]:
        start, end = position(bounds[0]), position(bounds[1])
        chart.add(
            Rect(
                min(start, width - 1),
                track_y,
                max(1, end - start),
                8,
                fillColor=color,
                strokeColor=None,
            )
        )
    if overlap_start <= overlap_end:
        start, end = position(overlap_start), position(overlap_end)
        chart.add(
            Rect(
                min(start, width - 1),
                track_y,
                max(1, end - start),
                8,
                fillColor=OVERLAP_COLOR,
                strokeColor=None,
            )
        )
    marker = position(signal.observed)
    chart.add(
        Line(
            marker,
            track_y - 5,
            marker,
            track_y + 13,
            strokeWidth=2,
            strokeColor=colors.HexColor("#182230"),
        )
    )
    if signal.observed < low or signal.observed > high:
        direction = -1 if signal.observed < low else 1
        tip = marker
        tail = marker - direction * 10
        arrow_y = track_y + 17
        for x1, y1, x2, y2 in [
            (tail, arrow_y, tip, arrow_y),
            (tip - direction * 4, arrow_y - 3, tip, arrow_y),
            (tip - direction * 4, arrow_y + 3, tip, arrow_y),
        ]:
            chart.add(Line(x1, y1, x2, y2, strokeColor=TEXT_COLOR, strokeWidth=1))
    if low != high:
        chart.add(
            String(
                0,
                7,
                "低" if zh else "Low",
                fontName=font,
                fontSize=8,
                fillColor=TEXT_COLOR,
            )
        )
        chart.add(
            String(
                width,
                7,
                "高" if zh else "High",
                textAnchor="end",
                fontName=font,
                fontSize=8,
                fillColor=TEXT_COLOR,
            )
        )
    else:
        chart.add(
            String(
                0,
                7,
                "整体参考范围为单点"
                if zh
                else "The overall reference extent is a single value",
                fontName=font,
                fontSize=8,
                fillColor=TEXT_COLOR,
            )
        )
    return chart


def build_evidence_section(
    evidence: EvidenceResult | None,
    *,
    locale: str,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> list[Flowable]:
    """Present saved comparisons with the same summary filter and scale as the UI."""
    zh = str(locale).startswith("zh")
    language = 0 if zh else 1
    heading = ParagraphStyle(
        "ReportEvidenceHeading", parent=styles["body"], spaceAfter=4, keepWithNext=True
    )
    story: list[Flowable] = [
        Paragraph("写作特点" if zh else "Writing characteristics", styles["section"])
    ]
    disclaimer = (
        "写作特点仅作辅助参考，不代表 AI 生成概率。"
        if zh
        else "Writing characteristics are supporting references, not AI generation probabilities."
    )
    if evidence is None or evidence.status not in {"ready", "partial"}:
        messages = {
            "missing": (
                "本次未提供写作特点。",
                "No writing characteristics are available for this result.",
            ),
            "failed": (
                "本次未能生成写作特点，主检测已完成。",
                "Writing characteristics could not be generated. Main detection is complete.",
            ),
            "unsupported": (
                "此文本语言暂不支持写作特点分析，主检测已完成。",
                "Writing characteristics are not supported for this language. Main detection is complete.",
            ),
            "insufficient": (
                "本次写作特点暂无可用的参考比较。",
                "No reference comparison is available for this result's writing characteristics.",
            ),
        }
        story.append(
            Paragraph(
                messages[evidence.status if evidence else "missing"][language],
                styles["body"],
            )
        )
    else:
        recommended = {
            signal.metric: signal
            for signal in evidence.signals
            if _has_distinct_reference(signal)
        }
        cards: list[list[Flowable]] = []
        card_width = (width - 18) / 2
        for dimension, metrics in SUMMARY_METRICS.items():
            for metric in metrics:
                signal = recommended.get(metric)
                if signal is None:
                    continue
                metric_name, dimension_name = (
                    METRIC_NAMES[metric][language],
                    DIMENSION_NAMES[dimension][language],
                )
                title = (
                    metric_name
                    if metric_name == dimension_name
                    else f"{dimension_name} · {metric_name}"
                )
                content: list[Flowable] = [
                    Paragraph(f"<b>{escape(title)}</b>", heading),
                    Paragraph(_comparison_text(signal, zh), styles["body"]),
                    Spacer(1, 6),
                ]
                chart = _reference_chart(
                    signal, card_width, styles["muted"].fontName, zh
                )
                if chart is not None:
                    content.append(chart)
                    if (
                        signal.observed < signal.referenceExtent[0]
                        or signal.observed > signal.referenceExtent[1]
                    ):
                        below = signal.observed < signal.referenceExtent[0]
                        outside = (
                            (
                                "本次值低于图示范围，以左侧箭头标示。"
                                if below
                                else "本次值高于图示范围，以右侧箭头标示。"
                            )
                            if zh
                            else (
                                "This value falls below the chart range and is marked with a left arrow."
                                if below
                                else "This value exceeds the chart range and is marked with a right arrow."
                            )
                        )
                        content.append(Paragraph(outside, styles["muted"]))
                else:
                    ranges = signal.referenceRanges
                    values = (
                        f"本次值 {_format_number(signal.observed)}；人工参考 {_format_number(ranges.human[0])} - {_format_number(ranges.human[1])}；AI 参考 {_format_number(ranges.ai[0])} - {_format_number(ranges.ai[1])}。"
                        if zh
                        else f"This value: {_format_number(signal.observed)}; human reference: {_format_number(ranges.human[0])} - {_format_number(ranges.human[1])}; AI reference: {_format_number(ranges.ai[0])} - {_format_number(ranges.ai[1])}."
                    )
                    content.extend(
                        [
                            Paragraph(values, styles["muted"]),
                            Paragraph(
                                "此结果未保存可用的整体参考范围，保留原值和区间说明。"
                                if zh
                                else "This result has no usable overall reference extent. Original values and range descriptions are retained.",
                                styles["muted"],
                            ),
                        ]
                    )
                cards.append(content)
        for index in range(0, len(cards), 2):
            row = Table(
                [
                    [
                        cards[index],
                        "",
                        cards[index + 1] if index + 1 < len(cards) else "",
                    ]
                ],
                colWidths=[card_width, 18, card_width],
                hAlign="LEFT",
            )
            row.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            story.extend([row, Spacer(1, 8)])
        if not cards:
            text = (
                (
                    "本次区间差异位于其他指标，可在工作台查看详细数据。"
                    if zh
                    else "Range differences were found in other metrics. Detailed data are available in the workspace."
                )
                if recommended
                else (
                    "本次写作特点未提供清晰的区间差异，暂不能为来源判断提供补充依据。"
                    if zh
                    else "These writing characteristics show no clear range differences and provide no additional evidence for judging authorship."
                )
            )
            story.append(Paragraph(text, styles["body"]))
        elif evidence.artifactVersion in KNOWN_REFERENCES:
            story.append(
                Paragraph(
                    "参考样本：DetectRL-X · Binary General Open。"
                    if zh
                    else "Reference samples: DetectRL-X · Binary General Open.",
                    styles["muted"],
                )
            )
    story.extend([Spacer(1, 5), Paragraph(disclaimer, styles["muted"])])
    return story
