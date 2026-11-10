from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.user import User
from app.schemas.report import ReportPdfContent
from app.services.report_evidence import build_evidence_section

FONT_NAME = "NotoSansSC"
FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansSC-Regular.ttf"
INK = colors.HexColor("#182230")
MUTED = colors.HexColor("#526174")
BLUE = colors.HexColor("#2456C7")
AI_BLUE = colors.HexColor("#3478F6")
HUMAN_CYAN = colors.HexColor("#0787A5")
LINE = colors.HexColor("#DDE5EF")


def _get_copy(locale: str) -> dict[str, str]:
    if str(locale).lower().startswith("zh"):
        return {
            "title": "AI 文本检测报告", "record": "检测记录", "report_id": "报告编号",
            "detected": "检测时间", "exported": "导出时间", "characters": "字符数（不含空白）",
            "account": "账号", "model": "检测模型", "unrecorded": "未记录",
            "summary": "检测概览", "result": "疑似 AI 生成", "ai": "AI 生成", "human": "人工写作",
            "original": "原文与段落结果", "likelihood": "AI 生成可能性",
            "paragraph": "段落", "block": "检测块", "merged": "合并检测",
            "too_short": "文本过短，未检测", "truncated": "该检测块仅使用部分文本进行检测。",
            "unmatched": "该历史记录的段落结果无法完整对应原文，以下保留原文和已保存的检测结果。",
            "saved_results": "已保存的段落结果", "no_segments": "本次未提供段落结果。",
            "note": "检测结果仅供参考，请结合原文复核。",
            "page": "第 {page} 页",
        }
    return {
        "title": "AI Text Detection Report", "record": "Detection record", "report_id": "Report ID",
        "detected": "Detected at", "exported": "Exported at", "characters": "Characters (excluding whitespace)",
        "account": "Account", "model": "Detection model", "unrecorded": "Not recorded",
        "summary": "Detection overview", "result": "detected as AI", "ai": "AI-generated", "human": "Human-written",
        "original": "Original text and paragraph results", "likelihood": "AI generation likelihood",
        "paragraph": "Paragraph", "block": "Block", "merged": "Merged",
        "too_short": "Text too short; not analyzed", "truncated": "Only part of this block was used for detection.",
        "unmatched": "The saved paragraph results cannot be fully matched to this original text. Both are preserved below.",
        "saved_results": "Saved paragraph results", "no_segments": "No paragraph results were provided.",
        "note": "Detection results are for reference. Review them alongside the original text.",
        "page": "Page {page}",
    }


def _ensure_report_font() -> str:
    try:
        pdfmetrics.getFont(FONT_NAME)
        return FONT_NAME
    except KeyError:
        pass
    try:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        font_name = FONT_NAME
    except Exception:
        font_name = "STSong-Light"
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    pdfmetrics.registerFontFamily(font_name, normal=font_name, bold=font_name, italic=font_name, boldItalic=font_name)
    return font_name


def _format_datetime(value: datetime | None, time_zone: str) -> str:
    if value is None:
        return ""
    try:
        zone = ZoneInfo(time_zone)
    except (ValueError, ZoneInfoNotFoundError):
        zone = timezone.utc
    localized = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(zone)
    offset = localized.strftime("%z")
    return f"{localized:%Y-%m-%d %H:%M} UTC{offset[:3]}:{offset[3:]}"


def _escape_text(value: object) -> str:
    text = escape(str(value if value is not None else "")).replace("\t", "    ")
    # Keep indentation while retaining normal word-wrap opportunities in prose.
    text = re.sub(r" {2,}", lambda match: " " + "&#160;" * (len(match.group()) - 1), text)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _build_styles(font_name: str) -> dict[str, ParagraphStyle]:
    body = ParagraphStyle("ReportBody", fontName=font_name, fontSize=10, leading=15,
                          textColor=INK, wordWrap="CJK", splitLongWords=True)
    return {
        "body": body,
        "title": ParagraphStyle("ReportTitle", parent=body, fontSize=23, leading=31, spaceAfter=5),
        "subtitle": ParagraphStyle("ReportSubtitle", parent=body, fontSize=11, leading=16, textColor=MUTED, spaceAfter=12),
        "section": ParagraphStyle("ReportSection", parent=body, fontSize=13, leading=18, spaceBefore=9,
                                  spaceAfter=6, keepWithNext=True),
        "muted": ParagraphStyle("ReportMuted", parent=body, fontSize=8.5, leading=13, textColor=MUTED),
        "meta_label": ParagraphStyle("ReportMetaLabel", parent=body, fontSize=8, leading=12, textColor=MUTED),
        "meta_value": ParagraphStyle("ReportMetaValue", parent=body, fontSize=9, leading=14),
        "block_label": ParagraphStyle("ReportBlockLabel", parent=body, fontSize=9, leading=13,
                                      textColor=MUTED, spaceBefore=7, spaceAfter=4, keepWithNext=True),
    }


def _resolve_account_name(user: User) -> str:
    return user.name or user.email or "-"


def _metadata(payload: ReportPdfContent, user: User, copy: dict, styles: dict, width: float) -> Table:
    detected = _format_datetime(payload.detected_at, payload.time_zone) or copy["unrecorded"]
    exported = _format_datetime(payload.generated_at or datetime.now(timezone.utc), payload.time_zone)
    # chars_used is a quota ledger field, which is zero on some valid old records.
    characters = sum(not char.isspace() for char in payload.input_text)
    values = [(copy["detected"], detected), (copy["exported"], exported),
              (copy["account"], _resolve_account_name(user)), (copy["characters"], f"{characters:,}")]
    if payload.history_id is not None:
        values.append((copy["report_id"], f"RG-{payload.history_id}"))
    model_version = re.search(r"\bv\d+(?:\.\d+)+\b", payload.model_name or "", re.IGNORECASE)
    if model_version:
        values.append((copy["model"], model_version.group().upper()))
    cells = [Paragraph(f"<font color='#526174'>{_escape_text(label)}</font>  {_escape_text(value)}",
                       styles["meta_value"]) for label, value in values]
    rows = [cells[index:index + 2] for index in range(0, len(cells), 2)]
    if len(rows[-1]) == 1:
        rows[-1].append("")
    table = Table(rows, colWidths=[width / 2] * 2, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _bar(value: int, width: float, color) -> Drawing:
    drawing = Drawing(width, 7)
    drawing.add(Rect(0, 0, width, 5, fillColor=colors.HexColor("#E8EDF4"), strokeColor=None))
    if value:
        drawing.add(Rect(0, 0, width * value / 100, 5, fillColor=color, strokeColor=None))
    return drawing


def _summary(payload: ReportPdfContent, copy: dict, styles: dict, width: float) -> Table:
    ai = payload.analysis.summary.ai
    human = payload.analysis.summary.human
    result = Paragraph(f"<font size='30' color='#2456C7'>{ai}%</font><br/>{copy['result']}",
                       ParagraphStyle("Result", parent=styles["body"], leading=23))
    bars = []
    for label, value, color in ((copy["ai"], ai, AI_BLUE), (copy["human"], human, HUMAN_CYAN)):
        bars.extend([Paragraph(f"{label} <b>{value}%</b>", styles["meta_value"]),
                     Spacer(1, 3), _bar(value, width * 0.57 - 10, color), Spacer(1, 6)])
    table = Table([[result, bars]], colWidths=[width * 0.43, width * 0.57], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("LINEBELOW", (0, 0), (-1, -1), 0.6, LINE),
    ]))
    return table


def _display_percentage(probability: float) -> int:
    # Match the UI's Math.round; this only formats the saved display probability.
    return math.floor(probability * 100 + 0.5)


def _segment_label(segment, index: int, copy: dict) -> str:
    start, end = segment.start_paragraph, segment.end_paragraph
    if start and end and start <= end:
        if start == end:
            return f"{copy['paragraph']} {start}"
        return f"{copy['paragraph']} {start}-{end} · {copy['merged']}"
    return f"{copy['block']} {index + 1}"


def _segment_palette(segment) -> tuple[str, str]:
    if segment.type == "too_short":
        return "#F1F5F9", "#526174"
    for minimum, fill, ink in ((0.8, "#FFF1F2", "#9F1239"), (0.6, "#FFF7ED", "#9A3412"),
                              (0.4, "#FFFBEB", "#92400E"), (0.2, "#F7FEE7", "#3F6212")):
        if segment.probability >= minimum:
            return fill, ink
    return "#ECFDF5", "#065F46"


def _segment_heading(segment, index: int, copy: dict) -> str:
    label = _segment_label(segment, index, copy)
    if segment.type == "too_short":
        return f"{label} · {copy['too_short']}"
    classification = copy["ai"] if segment.type == "ai" else copy["human"]
    return f"{label} · {classification} · {copy['likelihood']}: {_display_percentage(segment.probability)}%"


def _match_segments(text: str, segments: list) -> list[tuple[int, int]] | None:
    cursor = 0
    matches = []
    for segment in segments:
        raw = (segment.raw or segment.text).replace("\r\n", "\n").replace("\r", "\n")
        lines = [line for line in raw.split("\n") if line.strip()]
        if not lines:
            return None
        # Detection removes empty paragraph separators; retain them from the source in the PDF.
        pattern = r"\n(?:[ \t]*\n)*".join(re.escape(line) for line in lines)
        match = re.search(pattern, text[cursor:])
        if not match:
            return None
        start, end = cursor + match.start(), cursor + match.end()
        matches.append((start, end))
        cursor = end
    return matches


def _append_plain_text(story: list, text: str, style: ParagraphStyle, width: float) -> None:
    # Long paragraphs must be splittable flowables, not a single table cell.
    for paragraph in re.split(r"\n[ \t]*\n", text):
        if paragraph.strip():
            body = Paragraph(_escape_text(paragraph), style)
            body.wrap(width, 0)
            # Keep headings with the first two lines, not an entire long paragraph.
            story.extend(body.split(width, style.leading * 2) or [body])
            story.append(Spacer(1, 5))


def _append_original(story: list, payload: ReportPdfContent, copy: dict, styles: dict, width: float) -> None:
    text = payload.input_text.replace("\r\n", "\n").replace("\r", "\n")
    segments = payload.analysis.sentences
    matches = _match_segments(text, segments)
    if matches is None:
        story.extend([Paragraph(copy["unmatched"], styles["muted"]), Spacer(1, 7)])
        _append_plain_text(story, text, styles["body"], width)
        story.append(Paragraph(copy["saved_results"], styles["section"]))
        for index, segment in enumerate(segments):
            story.append(Paragraph(_escape_text(_segment_heading(segment, index, copy)), styles["body"]))
            if segment.is_truncated:
                story.append(Paragraph(copy["truncated"], styles["muted"]))
        return
    if not segments:
        story.extend([Paragraph(copy["no_segments"], styles["muted"]), Spacer(1, 7)])
    cursor = 0
    for index, (segment, (start, end)) in enumerate(zip(segments, matches)):
        _append_plain_text(story, text[cursor:start], styles["body"], width)
        story.append(Paragraph(_escape_text(_segment_heading(segment, index, copy)), styles["block_label"]))
        fill, ink = _segment_palette(segment)
        style = ParagraphStyle(f"Segment{index}", parent=styles["body"], backColor=colors.HexColor(fill),
                               textColor=colors.HexColor(ink), borderPadding=(0, 6))
        _append_plain_text(story, text[start:end], style, width)
        if segment.is_truncated:
            story.append(Paragraph(copy["truncated"], styles["muted"]))
        cursor = end
    _append_plain_text(story, text[cursor:], styles["body"], width)


def build_report_filename(payload: ReportPdfContent) -> str:
    value = payload.generated_at or datetime.now(timezone.utc)
    stamp = (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    identifier = f"-{payload.history_id}" if payload.history_id is not None else ""
    return f"repreguard-report{identifier}-{stamp:%Y-%m-%d-%H-%M-%S}Z.pdf"


def build_report_pdf(payload: ReportPdfContent, user: User) -> bytes:
    copy = _get_copy(payload.locale)
    font_name = _ensure_report_font()
    styles = _build_styles(font_name)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=19 * mm, rightMargin=19 * mm,
                            topMargin=22 * mm, bottomMargin=20 * mm, title=copy["title"], author="RepreGuard")
    width = doc.width - 12
    story = [Paragraph(copy["title"], styles["title"]),
             Paragraph(_escape_text(payload.title or copy["record"]), styles["subtitle"]),
             _metadata(payload, user, copy, styles, width),
             Paragraph(copy["summary"], styles["section"]), _summary(payload, copy, styles, width),
             Paragraph(copy["original"], styles["section"])]
    _append_original(story, payload, copy, styles, width)
    if payload.include_evidence:
        story.extend(build_evidence_section(payload.evidence, locale=payload.locale, styles=styles, width=width))

    def draw_page(canvas, document):
        canvas.saveState()
        canvas.setFont(font_name, 10)
        canvas.setFillColor(BLUE)
        canvas.drawString(document.leftMargin + 6, A4[1] - 13 * mm, "RepreGuard")
        canvas.setStrokeColor(LINE)
        canvas.line(document.leftMargin + 6, A4[1] - 16 * mm, A4[0] - document.rightMargin - 6, A4[1] - 16 * mm)
        canvas.setFont(font_name, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(document.leftMargin + 6, 10 * mm, copy["note"])
        canvas.drawRightString(A4[0] - document.rightMargin - 6, 10 * mm,
                               copy["page"].format(page=canvas.getPageNumber()))
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()
