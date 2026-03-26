from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.user import User
from app.schemas.report import ReportPdfContent

FONT_NAME = "NotoSansSC"
FALLBACK_FONT_NAME = "STSong-Light"
FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansSC-VF.ttf"

AI_STROKE = "#B42318"
AI_FILL = colors.HexColor("#FEF3F2")
HUMAN_STROKE = "#027A48"
HUMAN_FILL = colors.HexColor("#ECFDF3")


@dataclass(frozen=True)
class ReportCopy:
    brand: str
    title: str
    subtitle: str
    report_type_label: str
    generated_at_label: str
    display_name_label: str
    account_name_label: str
    functions_label: str
    history_id_label: str
    detection_id_label: str
    summary_title: str
    risk_title: str
    details_title: str
    original_title: str
    ai_label: str
    human_label: str
    ai_likely_label: str
    reason_label: str
    no_data: str
    no_risk: str
    page_label: str
    report_type_scan: str
    report_type_history: str
    function_scan: str
    function_polish: str
    function_translate: str
    function_citation: str


def _get_copy(locale: str) -> ReportCopy:
    if str(locale or "").startswith("zh"):
        return ReportCopy(
            brand="RepreGuard",
            title="AI \u68c0\u6d4b\u62a5\u544a",
            subtitle="\u7528\u4e8e\u4eba\u5de5\u590d\u6838\u4e0e\u7559\u6863",
            report_type_label="\u62a5\u544a\u7c7b\u578b",
            generated_at_label="\u5bfc\u51fa\u65f6\u95f4",
            display_name_label="\u7528\u6237\u59d3\u540d",
            account_name_label="\u7528\u6237\u8d26\u53f7",
            functions_label="\u529f\u80fd\u7c7b\u578b",
            history_id_label="\u5386\u53f2\u8bb0\u5f55 ID",
            detection_id_label="\u68c0\u6d4b ID",
            summary_title="\u68c0\u6d4b\u6458\u8981",
            risk_title="\u91cd\u70b9\u590d\u6838\u53e5\u6bb5",
            details_title="\u53e5\u6bb5\u660e\u7ec6",
            original_title="\u539f\u6587\u9644\u5f55",
            ai_label="AI",
            human_label="\u4eba\u5de5",
            ai_likely_label="\u6f5c\u5728\u9ad8\u98ce\u9669\u53e5\u6bb5",
            reason_label="\u5224\u5b9a\u539f\u56e0",
            no_data="\u6682\u65e0\u6570\u636e",
            no_risk="\u672c\u6b21\u7ed3\u679c\u4e2d\u6ca1\u6709\u9700\u8981\u4f18\u5148\u590d\u6838\u7684 AI \u9ad8\u98ce\u9669\u53e5\u6bb5\u3002",
            page_label="\u7b2c {current} \u9875",
            report_type_scan="\u5373\u65f6\u68c0\u6d4b\u5bfc\u51fa",
            report_type_history="\u5386\u53f2\u8bb0\u5f55\u5bfc\u51fa",
            function_scan="AI \u68c0\u6d4b",
            function_polish="\u6da6\u8272",
            function_translate="\u7ffb\u8bd1",
            function_citation="\u5f15\u7528\u6838\u67e5",
        )

    return ReportCopy(
        brand="RepreGuard",
        title="AI Detection Report",
        subtitle="Prepared for manual review and record keeping",
        report_type_label="Report Type",
        generated_at_label="Generated At",
        display_name_label="Display Name",
        account_name_label="Account Name",
        functions_label="Functions",
        history_id_label="History ID",
        detection_id_label="Detection ID",
        summary_title="Detection Summary",
        risk_title="Priority Review Passages",
        details_title="Sentence Details",
        original_title="Original Text Appendix",
        ai_label="AI",
        human_label="Human",
        ai_likely_label="Potential High-Risk Passages",
        reason_label="Reason",
        no_data="No data",
        no_risk="No AI-risk passages require priority review in this report.",
        page_label="Page {current}",
        report_type_scan="Live Scan Export",
        report_type_history="History Export",
        function_scan="AI Scan",
        function_polish="Polish",
        function_translate="Translate",
        function_citation="Citation Check",
    )


def _ensure_report_font() -> str:
    try:
        pdfmetrics.getFont(FONT_NAME)
        return FONT_NAME
    except KeyError:
        pass

    try:
        if FONT_PATH.exists():
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
            return FONT_NAME
    except Exception:
        pass

    try:
        pdfmetrics.getFont(FALLBACK_FONT_NAME)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(FALLBACK_FONT_NAME))
    return FALLBACK_FONT_NAME


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _escape_text(value: str | int | None) -> str:
    return escape(str(value or "")).replace("\n", "<br/>")


def _resolve_display_name(user: User) -> str:
    full_name = " ".join(part for part in [user.first_name, user.surname] if part).strip()
    if full_name:
        return full_name
    if user.name:
        return user.name
    if user.email and "@" in user.email:
        return user.email.split("@", 1)[0]
    return "User"


def _resolve_account_name(user: User) -> str:
    if user.name:
        return user.name
    if user.email and "@" in user.email:
        return user.email.split("@", 1)[0]
    return "user"


def _map_function_label(key: str, copy: ReportCopy) -> str:
    mapping = {
        "scan": copy.function_scan,
        "polish": copy.function_polish,
        "translate": copy.function_translate,
        "citation": copy.function_citation,
    }
    return mapping.get(key, key)


def _map_sentence_type(value: str, copy: ReportCopy) -> str:
    mapping = {
        "ai": copy.ai_label,
        "mixed": copy.ai_label,
        "human": copy.human_label,
    }
    return mapping.get(value, value)


def _get_sentence_palette(value: str) -> tuple[str, colors.Color]:
    if value in {"ai", "mixed"}:
        return AI_STROKE, AI_FILL
    return HUMAN_STROKE, HUMAN_FILL


def _collapse_summary_for_display(summary) -> tuple[int, int]:
    ai = max(0, min(100, round(float(getattr(summary, "ai", 0) or 0))))
    human = max(0, 100 - ai)
    return ai, human


def _build_styles(font_name: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=font_name,
            fontSize=22,
            leading=28,
            textColor=colors.HexColor("#101828"),
            spaceAfter=6,
            wordWrap="CJK",
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=10.5,
            leading=15,
            textColor=colors.HexColor("#667085"),
            spaceAfter=12,
            wordWrap="CJK",
        ),
        "section": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontName=font_name,
            fontSize=14,
            leading=20,
            textColor=colors.HexColor("#101828"),
            spaceBefore=8,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "BodyCopy",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=10.5,
            leading=16,
            textColor=colors.HexColor("#101828"),
            wordWrap="CJK",
        ),
        "muted": ParagraphStyle(
            "MutedCopy",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#667085"),
            wordWrap="CJK",
        ),
        "meta_label": ParagraphStyle(
            "MetaLabel",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#667085"),
            wordWrap="CJK",
        ),
        "meta_value": ParagraphStyle(
            "MetaValue",
            parent=base["BodyText"],
            fontName=font_name,
            fontSize=10.5,
            leading=15,
            textColor=colors.HexColor("#101828"),
            wordWrap="CJK",
        ),
    }


def _build_metadata_table(
    payload: ReportPdfContent,
    user: User,
    copy: ReportCopy,
    styles: dict[str, ParagraphStyle],
) -> Table:
    function_labels = ", ".join(_map_function_label(item, copy) for item in payload.functions) or copy.no_data
    rows = [
        [
            Paragraph(copy.report_type_label, styles["meta_label"]),
            Paragraph(copy.report_type_history if payload.report_type == "history" else copy.report_type_scan, styles["meta_value"]),
        ],
        [
            Paragraph(copy.generated_at_label, styles["meta_label"]),
            Paragraph(_escape_text(_format_datetime(payload.generated_at)), styles["meta_value"]),
        ],
        [
            Paragraph(copy.display_name_label, styles["meta_label"]),
            Paragraph(_escape_text(_resolve_display_name(user)), styles["meta_value"]),
        ],
        [
            Paragraph(copy.account_name_label, styles["meta_label"]),
            Paragraph(_escape_text(_resolve_account_name(user)), styles["meta_value"]),
        ],
        [
            Paragraph(copy.functions_label, styles["meta_label"]),
            Paragraph(_escape_text(function_labels), styles["meta_value"]),
        ],
        [
            Paragraph(copy.history_id_label, styles["meta_label"]),
            Paragraph(_escape_text(payload.history_id if payload.history_id is not None else copy.no_data), styles["meta_value"]),
        ],
        [
            Paragraph(copy.detection_id_label, styles["meta_label"]),
            Paragraph(_escape_text(payload.detection_id if payload.detection_id is not None else copy.no_data), styles["meta_value"]),
        ],
    ]

    table = Table(rows, colWidths=[35 * mm, 135 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#EAECF0")),
                ("INNERGRID", (0, 0), (-1, -1), 1, colors.HexColor("#EAECF0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _build_summary_table(payload: ReportPdfContent, copy: ReportCopy, styles: dict[str, ParagraphStyle]) -> Table:
    ai, human = _collapse_summary_for_display(payload.analysis.summary)
    rows = [[
        Paragraph(f"<font color='{AI_STROKE}'><b>{copy.ai_label}</b></font><br/><font size='18'>{ai}%</font>", styles["body"]),
        Paragraph(f"<font color='{HUMAN_STROKE}'><b>{copy.human_label}</b></font><br/><font size='18'>{human}%</font>", styles["body"]),
    ]]
    table = Table(rows, colWidths=[85 * mm, 85 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), AI_FILL),
                ("BACKGROUND", (1, 0), (1, 0), HUMAN_FILL),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#EAECF0")),
                ("INNERGRID", (0, 0), (-1, -1), 1, colors.HexColor("#EAECF0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return table


def _build_sentence_block(sentence, index: int, copy: ReportCopy, styles: dict[str, ParagraphStyle]) -> Table:
    stroke, fill = _get_sentence_palette(sentence.type)
    probability = round(float(sentence.probability or 0) * 100)
    title = Paragraph(
        f"<font color='{stroke}'><b>{index}. {_map_sentence_type(sentence.type, copy)} {probability}%</b></font>",
        styles["body"],
    )
    body = Paragraph(_escape_text(sentence.text), styles["body"])
    content = [title, Spacer(1, 4), body]
    if sentence.reason:
        content.extend(
            [
                Spacer(1, 4),
                Paragraph(f"<b>{copy.reason_label}:</b> {_escape_text(sentence.reason)}", styles["muted"]),
            ]
        )

    table = Table([[content]], colWidths=[170 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fill),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor(stroke)),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _append_original_text(story: list, text: str, styles: dict[str, ParagraphStyle], copy: ReportCopy) -> None:
    paragraphs = str(text or "").splitlines()
    if not paragraphs:
        story.append(Paragraph(copy.no_data, styles["muted"]))
        return

    for paragraph in paragraphs:
        if paragraph.strip():
            story.append(Paragraph(_escape_text(paragraph), styles["body"]))
        else:
            story.append(Spacer(1, 6))
        story.append(Spacer(1, 3))


def build_report_filename(payload: ReportPdfContent) -> str:
    timestamp = (payload.generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d-%H-%M-%S")
    if payload.report_type == "history" and payload.history_id is not None:
        return f"aidetector-report-history-{payload.history_id}-{timestamp}.pdf"
    if payload.history_id is not None:
        return f"aidetector-report-scan-{payload.history_id}-{timestamp}.pdf"
    return f"aidetector-report-{timestamp}.pdf"


def build_report_pdf(payload: ReportPdfContent, user: User) -> bytes:
    copy = _get_copy(payload.locale)
    font_name = _ensure_report_font()
    styles = _build_styles(font_name)
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=24 * mm,
        bottomMargin=16 * mm,
    )
    story: list = []

    story.append(Paragraph(copy.title, styles["title"]))
    story.append(Paragraph(copy.subtitle, styles["subtitle"]))
    story.append(_build_metadata_table(payload, user, copy, styles))
    story.append(Spacer(1, 10))

    story.append(Paragraph(copy.summary_title, styles["section"]))
    story.append(_build_summary_table(payload, copy, styles))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>{copy.ai_likely_label}:</b> {payload.analysis.ai_likely_count}", styles["body"]))
    story.append(Spacer(1, 12))

    risk_sentences = [item for item in payload.analysis.sentences if item.type in {"ai", "mixed"}]
    story.append(Paragraph(copy.risk_title, styles["section"]))
    if risk_sentences:
        for index, sentence in enumerate(risk_sentences, start=1):
            story.append(_build_sentence_block(sentence, index, copy, styles))
            story.append(Spacer(1, 8))
    else:
        story.append(Paragraph(copy.no_risk, styles["muted"]))
        story.append(Spacer(1, 8))

    story.append(Paragraph(copy.details_title, styles["section"]))
    if payload.analysis.sentences:
        for index, sentence in enumerate(payload.analysis.sentences, start=1):
            story.append(_build_sentence_block(sentence, index, copy, styles))
            story.append(Spacer(1, 8))
    else:
        story.append(Paragraph(copy.no_data, styles["muted"]))
        story.append(Spacer(1, 8))

    story.append(Paragraph(copy.original_title, styles["section"]))
    _append_original_text(story, payload.input_text, styles, copy)

    generated_at_text = _format_datetime(payload.generated_at)

    def draw_page(canvas, document):
        canvas.saveState()
        canvas.setFont(font_name, 9)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(document.leftMargin, A4[1] - 12 * mm, copy.brand)
        canvas.drawRightString(A4[0] - document.rightMargin, A4[1] - 12 * mm, generated_at_text)
        canvas.setStrokeColor(colors.HexColor("#EAECF0"))
        canvas.line(document.leftMargin, A4[1] - 14 * mm, A4[0] - document.rightMargin, A4[1] - 14 * mm)
        canvas.drawRightString(
            A4[0] - document.rightMargin,
            8 * mm,
            copy.page_label.format(current=canvas.getPageNumber()),
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()
