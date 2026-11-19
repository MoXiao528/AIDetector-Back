import re
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.schemas.report import ReportPdfContent
from app.services.report_pdf import build_report_filename, build_report_pdf


def segment(text, probability=0.125, kind="human", **kwargs):
    return {
        "id": "block", "text": text, "raw": text, "type": kind, "probability": probability,
        "score": 12, "reason": "UNSUPPORTED_EXPLANATION", "suggestion": "UNSUPPORTED_SUGGESTION", **kwargs,
    }


def report(text, segments, locale="zh-CN", **kwargs):
    return ReportPdfContent(
        input_text=text, locale=locale, include_evidence=False,
        analysis={"summary": {"ai": 21, "human": 79}, "sentences": segments,
                  "ai_likely_count": 0, "highlighted_html": ""}, **kwargs,
    )


def render(payload):
    user = SimpleNamespace(name="report-test", email="report@example.invalid")
    data = build_report_pdf(payload, user)
    reader = PdfReader(BytesIO(data))
    return reader, "\n".join(page.extract_text() or "" for page in reader.pages)


def compact(value):
    return "".join(value.split())


def without_page_furniture(text):
    return re.sub(r"RepreGuard\n[^\n]*\n(?:第 \d+ 页|Page \d+)\n", "", text)


@pytest.mark.parametrize("locale,label", [("zh-CN", "AI 生成可能性: 13%"), ("en-US", "AI generation likelihood: 13%")])
def test_report_preserves_results_and_prints_original_once(locale, label):
    source = "Original marker <text> & source."
    payload = report(source, [segment(source, start_paragraph=1, end_paragraph=1)], locale)
    before = deepcopy(payload.model_dump())

    reader, text = render(payload)

    assert len(reader.pages) == 1
    assert compact(text).count(compact(source)) == 1
    assert label in text
    assert "21%" in text and "79%" in text
    assert "UNSUPPORTED_EXPLANATION" not in text and "UNSUPPORTED_SUGGESTION" not in text
    assert payload.model_dump() == before


def test_merged_block_matches_source_with_blank_lines_and_preserves_unmarked_text():
    source = "Unmarked heading\n\n  First source paragraph.\n\n\nSecond source paragraph.\n\nUnmarked end."
    saved = "  First source paragraph.\nSecond source paragraph."
    _, text = render(report(source, [segment(saved, start_paragraph=2, end_paragraph=3)]))
    assert "段落 2-3 · 合并检测" in text
    for part in ("Unmarked heading", "First source paragraph.", "Second source paragraph.", "Unmarked end."):
        assert text.count(part) == 1
    assert "无法完整对应" not in text


@pytest.mark.parametrize("matched", [True, False])
@pytest.mark.parametrize("has_ranges", [True, False])
def test_metadata_does_not_consume_body_numbers_in_matched_or_legacy_results(matched, has_ranges):
    paragraphs = ["A title", "Author: Reader", "First body.", "Second body.", "A section title", "好。"]
    source = "\n\n".join(paragraphs) if matched else "Unmatched original remains unchanged."
    entries = [(paragraphs[0], "metadata", 1, 1), (paragraphs[1], "metadata", 2, 2),
               ("\n".join(paragraphs[2:4]), "human", 3, 4), (paragraphs[4], "metadata", 5, 5),
               (paragraphs[5], "ai", 6, 6)]
    segments = [segment(raw, kind=kind, **({"start_paragraph": start, "end_paragraph": end} if has_ranges else {}))
                for raw, kind, start, end in entries]
    payload = report(source, segments, "en-US")
    before = deepcopy(payload.model_dump())
    _, text = render(payload)
    section = text.split("Original text and paragraph results", 1)[1]
    assert section.count("Title\n") == 2 and "Author\n" in section
    assert "context only" not in section
    if has_ranges:
        assert "Paragraph 1-2 · Merged" in section and "Paragraph 3 ·" in section
        assert "Paragraph 6" not in section
    else:
        assert "Block 1 ·" in section and "Block 2 ·" in section
        assert "Block 3" not in section
    assert section.count("AI generation likelihood") == 2
    assert payload.model_dump() == before


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_very_long_paragraph_can_split_across_pages_without_losing_text(locale):
    source = "LONG_START " + "用于验证报告长段落分页的测试文本。" * 240 + " LONG_END"
    reader, text = render(report(source, [segment(source, 0.875, "ai", is_truncated=True)], locale))
    assert 2 <= len(reader.pages) <= 4
    assert "LONG_START" in reader.pages[0].extract_text()
    assert text.count("LONG_START") == text.count("LONG_END") == 1
    assert compact(without_page_furniture(text)).count("用于验证报告长段落分页的测试文本。") == 240
    assert ("仅使用部分文本" if locale == "zh-CN" else "Only part of this block") in text


def test_twenty_thousand_character_source_has_no_repeated_appendix():
    source = "BEGIN " + "abcdefghi " * 1998 + " END"
    reader, text = render(report(source, [segment(source)], "en-US"))
    assert len(reader.pages) > 1
    assert text.count("BEGIN") == text.count("END") == 1
    assert text.count("abcdefghi") == 1998


def test_too_short_uses_status_instead_of_ai_percentage():
    source = "短文"
    _, text = render(report(source, [segment(source, 0, "too_short")]))
    paragraph_section = text.split("原文与段落结果", 1)[1]
    assert "文本过短，未检测" in paragraph_section
    assert "AI 生成可能性" not in paragraph_section
    assert "too_short" not in text


def test_all_unanalyzed_text_keeps_zero_summary_without_inventing_human_result():
    payload = report("短文", [segment("短文", 0, "too_short")])
    payload.analysis.summary.ai = 0
    payload.analysis.summary.human = 0
    _, text = render(payload)
    assert "100%" not in text
    assert "人工写作 0%" in text


def test_report_does_not_rebalance_saved_summary_values():
    payload = report("Saved original.", [segment("Saved original.")])
    payload.analysis.summary.human = 78
    _, text = render(payload)
    assert "人工写作 78%" in text and "79%" not in text


def test_unmatched_legacy_segments_preserve_original_and_result_without_guessing_highlight():
    source = "A legacy original that differs from its saved segment."
    _, text = render(report(source, [segment("Different legacy segment.")], "en-US"))
    assert "cannot be fully matched" in text
    assert compact(text).count(compact(source)) == 1
    assert "Block 1" in text and "AI generation likelihood: 13%" in text
    assert "Different legacy segment." not in text


def test_report_dates_have_explicit_zone_and_use_stored_text_character_count():
    payload = report(" A B 中文 ", [], time_zone="Asia/Shanghai", chars_used=0, history_id=42,
                     detected_at=datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc),
                     generated_at=datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc))
    _, text = render(payload)
    assert "2026-11-09 20:00 UTC+08:00" in text
    assert "2026-11-09 21:00 UTC+08:00" in text
    assert "RG-42" in text and re.search(r"字符数（不含空白）\s+4\b", text)
    assert build_report_filename(payload) == "repreguard-report-42-2026-11-09-13-00-00Z.pdf"


def test_unknown_timezone_falls_back_to_labelled_utc():
    _, text = render(report("Text", [], time_zone="Invalid/Zone",
                            generated_at=datetime(2026, 11, 9, 12, tzinfo=timezone.utc)))
    assert "2026-11-09 12:00 UTC+00:00" in text
    assert re.search(r"检测时间\s+未记录", text)


@pytest.mark.parametrize("locale,label", [("zh-CN", "检测模型"), ("en-US", "Detection model")])
@pytest.mark.parametrize("model_name,version", [
    ("v2.0-roberta", "V2.0"),
    ("v3.1-next-model", "V3.1"),
    ("legacy-model", None),
])
def test_report_displays_only_saved_model_version(locale, label, model_name, version):
    payload = report("Text", [], locale, model_name=model_name)
    _, text = render(payload)
    assert model_name not in text
    assert payload.model_name == model_name
    if version:
        assert f"{label} {version}" in text
    else:
        assert label not in text
