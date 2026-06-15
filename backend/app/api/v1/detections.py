import asyncio
import re
from datetime import datetime
from html import escape
from io import BytesIO
from math import exp

from docx import Document
from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pypdf import PdfReader

from app.core.config import get_settings
from app.db.deps import ActiveMemberDep, CurrentActorDep, SessionDep
from app.schemas import (
    AnalysisResponse,
    Citation,
    DetectRequest,
    DetectionListResponse,
    DetectionRequest,
    DetectionResponse,
    ErrorResponse,
    ParseFilesResponse,
    ParsedFileResult,
    ScanExamplesResponse,
    SentenceAnalysis,
)
from app.schemas.detection import DetectionItem
from app.schemas.history import Analysis, Citation as HistoryCitation, Sentence as HistorySentence, Summary
from app.services.detection_service import DetectionService
from app.services.quota_service import QuotaExceededError, consume_quota, get_quota_limit, get_today_bounds, get_used_today
from app.services.repre_guard_client import RepreGuardError, repre_guard_client
from app.services.scan_example_service import ScanExampleService
from app.services.token_chunker import DETECTABLE_STATUS, TOO_SHORT_STATUS, build_token_aware_segments

router = APIRouter(tags=["detections"])
detect_router = APIRouter(tags=["detections"])
scan_router = APIRouter(prefix="/scan", tags=["scan"])
settings = get_settings()

MAX_FILE_COUNT = 5
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
SUPPORTED_DETECTION_FUNCTIONS = {"scan"}
MIN_DETECT_VISIBLE_CHARS = 200
MAX_DETECT_CHARS = 20000
MAX_SEGMENT_VISIBLE_CHARS = 1500
SENTENCE_BOUNDARY_PATTERN = re.compile(r".+?(?:[。！？!?]+|[.]{1,3})(?:\s+|$)|.+?$", re.S)
DISPLAY_MODEL_NAME = "v2.0-roberta"


def _quota_exceeded_http_error(*, limit: int, used_today: int, remaining: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "QUOTA_EXCEEDED",
            "message": "Daily quota exceeded",
            "detail": {
                "limit": limit,
                "used_today": used_today,
                "remaining": remaining,
            },
        },
    )


def _public_repre_guard_message(exc: RepreGuardError) -> str:
    if exc.status_code == 429:
        return "Detect service rate limit exceeded"
    if exc.status_code in {500, 502, 503, 504}:
        return "Detect service is temporarily unavailable"
    return exc.message[:160]

def _normalize_score(raw_score: float, threshold: float) -> float:
    x = raw_score - threshold
    return 1.0 / (1.0 + exp(-x))


def _score_to_probability(score: float, threshold: float, score_type: str | None = None) -> float:
    if score_type == "probability":
        return min(max(float(score), 0.0), 1.0)
    return _normalize_score(score, threshold)


def _clamp_probability(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


def _score_to_display_probability(score: float, threshold: float, score_type: str) -> float:
    if score_type != "probability":
        return _score_to_probability(score, threshold, score_type)

    score = _clamp_probability(score)
    threshold = _clamp_probability(threshold)
    if threshold <= 0:
        return score

    if score >= threshold:
        return 0.67 + min(((score - threshold) / max(1.0 - threshold, 1e-9)) * 0.33, 0.33)

    ratio_to_threshold = score / threshold
    if ratio_to_threshold >= 0.5:
        return 0.34 + ((ratio_to_threshold - 0.5) / 0.5) * 0.32
    return (ratio_to_threshold / 0.5) * 0.33


def _split_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    return [segment for segment in normalized.split("\n") if segment.strip()]


def _count_visible_chars(text: str) -> int:
    return len("".join(str(text).split()))


def _combine_segment_text(left: str, right: str, *, same_paragraph: bool) -> str:
    separator = "" if same_paragraph else "\n"
    return f"{left}{separator}{right}"


def _hard_split_text(text: str, max_chars: int = MAX_SEGMENT_VISIBLE_CHARS) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    visible_chars = 0

    for char in text:
        buffer.append(char)
        if not char.isspace():
            visible_chars += 1

        if visible_chars >= max_chars:
            chunk = "".join(buffer)
            if chunk.strip():
                chunks.append(chunk)
            buffer = []
            visible_chars = 0

    if buffer:
        chunk = "".join(buffer)
        if chunk.strip():
            chunks.append(chunk)

    return chunks


def _split_text_for_detect_retry(text: str) -> list[str]:
    normalized = str(text)
    if not normalized.strip():
        return []

    visible_total = _count_visible_chars(normalized)
    if visible_total <= 1:
        return [normalized]

    target_visible = max(1, visible_total // 2)
    visible_seen = 0
    split_at = 0

    for index, char in enumerate(normalized):
        if not char.isspace():
            visible_seen += 1
        if visible_seen >= target_visible:
            split_at = index + 1
            break

    left = normalized[:split_at]
    right = normalized[split_at:]
    return [part for part in (left, right) if part.strip()]


def _combine_repre_guard_results(parts: list[str], results: list[dict]) -> dict:
    total_weight = 0
    weighted_score = 0.0
    weighted_probability = 0.0
    weighted_threshold = 0.0
    model_names: list[str] = []
    score_type = str(results[0]["score_type"]) if results else "probability"

    for part, result in zip(parts, results, strict=True):
        result_score_type = str(result["score_type"])
        if result_score_type != score_type:
            raise RepreGuardError(
                "detect service returned inconsistent score_type values while splitting input",
                code="INVALID_DETECT_RESPONSE",
                detail={"score_types": [str(item.get("score_type")) for item in results]},
            )
        weight = max(_count_visible_chars(part), 1)
        score = float(result["score"])
        threshold = float(result["threshold"])
        probability = _score_to_probability(score, threshold, result_score_type)
        total_weight += weight
        weighted_score += score * weight
        weighted_probability += probability * weight
        weighted_threshold += threshold * weight
        model_names.append(str(result["model_name"]))

    score = weighted_score / max(total_weight, 1)
    threshold = weighted_threshold / max(total_weight, 1)
    if score_type == "probability":
        score = weighted_probability / max(total_weight, 1)

    return {
        "score": score,
        "threshold": threshold,
        "label": "AI" if score >= threshold else "HUMAN",
        "model_name": model_names[0] if model_names else DISPLAY_MODEL_NAME,
        "score_type": score_type,
    }


async def _detect_text_with_retry(text: str) -> dict:
    try:
        return await repre_guard_client.detect(text=text)
    except RepreGuardError as exc:
        if exc.code not in {"INPUT_TOO_LONG", "TEXT_TOO_LONG"}:
            raise

        parts = _split_text_for_detect_retry(text)
        if len(parts) <= 1:
            raise

        results = [await _detect_text_with_retry(part) for part in parts]
        return _combine_repre_guard_results(parts, results)


def _split_sentence_like_chunks(text: str) -> list[str]:
    normalized = str(text)
    if not normalized.strip():
        return []

    chunks = [
        match.group(0)
        for match in SENTENCE_BOUNDARY_PATTERN.finditer(normalized)
        if match.group(0).strip()
    ]
    return chunks or [normalized]


def _split_oversized_paragraph(
    paragraph: str,
    paragraph_index: int,
    max_chars: int = MAX_SEGMENT_VISIBLE_CHARS,
) -> list[dict[str, int | str]]:
    cleaned = paragraph
    if not cleaned.strip():
        return []

    visible_chars = _count_visible_chars(cleaned)
    if visible_chars <= max_chars:
        return [
            {
                "start": paragraph_index,
                "end": paragraph_index,
                "text": cleaned,
                "visible_chars": visible_chars,
            }
        ]

    split_chunks: list[dict[str, int | str]] = []
    buffer: list[str] = []
    buffer_visible_chars = 0

    def flush_buffer() -> None:
        nonlocal buffer, buffer_visible_chars
        if not buffer:
            return

        text = "".join(buffer)
        if text.strip():
            split_chunks.append(
                {
                    "start": paragraph_index,
                    "end": paragraph_index,
                    "text": text,
                    "visible_chars": _count_visible_chars(text),
                }
            )
        buffer = []
        buffer_visible_chars = 0

    for sentence in _split_sentence_like_chunks(cleaned):
        sentence_visible_chars = _count_visible_chars(sentence)
        if sentence_visible_chars > max_chars:
            flush_buffer()
            for hard_chunk in _hard_split_text(sentence, max_chars):
                hard_chunk_visible_chars = _count_visible_chars(hard_chunk)
                if hard_chunk_visible_chars:
                    split_chunks.append(
                        {
                            "start": paragraph_index,
                            "end": paragraph_index,
                            "text": hard_chunk,
                            "visible_chars": hard_chunk_visible_chars,
                        }
                    )
            continue

        if buffer and buffer_visible_chars + sentence_visible_chars > max_chars:
            flush_buffer()

        buffer.append(sentence)
        buffer_visible_chars += sentence_visible_chars

    flush_buffer()
    return split_chunks


def _merge_short_paragraphs(
    paragraphs: list[str],
    min_chars: int = MIN_DETECT_VISIBLE_CHARS,
    max_chars: int = MAX_SEGMENT_VISIBLE_CHARS,
) -> list[dict[str, int | str]]:
    seeds: list[dict[str, int | str]] = []
    for index, paragraph in enumerate(paragraphs):
        seeds.extend(_split_oversized_paragraph(paragraph, index, max_chars=max_chars))

    if not seeds:
        return []

    merged: list[dict[str, int | str]] = []
    buffer: dict[str, int | str] | None = None

    for seed in seeds:
        if buffer is None:
            buffer = seed.copy()
            continue

        buffer_visible_chars = int(buffer["visible_chars"])
        seed_visible_chars = int(seed["visible_chars"])
        can_merge = buffer_visible_chars < min_chars and (buffer_visible_chars + seed_visible_chars) <= max_chars

        if can_merge:
            same_paragraph = int(buffer["end"]) == int(seed["start"]) == int(seed["end"])
            buffer["text"] = _combine_segment_text(str(buffer["text"]), str(seed["text"]), same_paragraph=same_paragraph)
            buffer["end"] = int(seed["end"])
            buffer["visible_chars"] = buffer_visible_chars + seed_visible_chars
            continue

        merged.append(buffer)
        buffer = seed.copy()

    if buffer is not None:
        buffer_visible_chars = int(buffer["visible_chars"])
        if merged and buffer_visible_chars < min_chars:
            previous_visible_chars = int(merged[-1]["visible_chars"])
            if previous_visible_chars + buffer_visible_chars <= max_chars:
                same_paragraph = int(merged[-1]["end"]) == int(buffer["start"]) == int(buffer["end"])
                merged[-1]["text"] = _combine_segment_text(
                    str(merged[-1]["text"]),
                    str(buffer["text"]),
                    same_paragraph=same_paragraph,
                )
                merged[-1]["end"] = int(buffer["end"])
                merged[-1]["visible_chars"] = previous_visible_chars + buffer_visible_chars
            else:
                merged.append(buffer)
        else:
            merged.append(buffer)

    return merged


def _resolve_segment_type(score: float, threshold: float, score_type: str) -> str:
    if score >= threshold:
        return "ai"
    display_probability = _score_to_display_probability(score, threshold, score_type)
    if display_probability >= 0.34:
        return "mixed"
    return "human"


def _resolve_highlight_class(score: float) -> str:
    if score >= 0.8:
        return "bg-rose-100 text-rose-900"
    if score >= 0.6:
        return "bg-orange-100 text-orange-900"
    if score >= 0.4:
        return "bg-amber-100 text-amber-900"
    if score >= 0.2:
        return "bg-lime-100 text-lime-900"
    return "bg-emerald-100 text-emerald-900"


def _normalize_detection_functions(functions: list[str] | None) -> set[str]:
    normalized = {
        item.strip().lower()
        for item in (functions or [])
        if isinstance(item, str) and item.strip()
    }
    supported = normalized & SUPPORTED_DETECTION_FUNCTIONS
    supported.add("scan")
    return supported


async def _detect_segments_with_limit(segments: list[dict[str, int | str | bool]]) -> list[dict]:
    concurrency = max(1, int(settings.detect_segment_concurrency))
    semaphore = asyncio.Semaphore(concurrency)

    async def detect_segment(segment: dict[str, int | str | bool]) -> dict:
        async with semaphore:
            return await _detect_text_with_retry(str(segment.get("detect_text") or segment["text"]))

    tasks = [detect_segment(segment) for segment in segments]
    return await asyncio.wait_for(
        asyncio.gather(*tasks),
        timeout=max(float(settings.detect_request_timeout), float(settings.detect_service_timeout)),
    )


def _build_history_analysis(
    text: str,
    paragraph_scores: list[dict[str, float | str | int | bool]],
    functions: set[str],
) -> Analysis:
    total_weight = sum(int(item["weight"]) for item in paragraph_scores if item.get("status") != TOO_SHORT_STATUS)
    bucket_weights = {"ai": 0, "mixed": 0, "human": 0}

    reason_map = {
        "ai": "This segment is highly patterned and is more likely to be machine-generated.",
        "mixed": "This segment is borderline and should be reviewed manually.",
        "human": "This segment reads more like natural human writing.",
        TOO_SHORT_STATUS: "This segment is too short for a reliable model judgment.",
    }
    suggestion_map = {
        "ai": "Add concrete facts, distinctive examples, and less templated phrasing.",
        "mixed": "Add more specific detail or domain context to make this segment less formulaic.",
        "human": "This segment already looks relatively natural.",
        TOO_SHORT_STATUS: "No action is needed unless this short section should be merged into surrounding context.",
    }

    history_sentences = []
    html_parts: list[str] = []
    for index, paragraph_result in enumerate(paragraph_scores):
        paragraph_text = str(paragraph_result["text"])
        if paragraph_result.get("status") == TOO_SHORT_STATUS:
            display_probability = 0.0
            paragraph_type = TOO_SHORT_STATUS
            paragraph_score = 0
        else:
            raw_score = float(paragraph_result["raw_score"])
            threshold = float(paragraph_result["threshold"])
            score_type = str(paragraph_result["score_type"])
            display_probability = _score_to_display_probability(raw_score, threshold, score_type)
            paragraph_type = _resolve_segment_type(raw_score, threshold, score_type)
            paragraph_score = max(0, min(int(round(display_probability * 100)), 100))
            bucket_weights[paragraph_type] += int(paragraph_result["weight"])
        start_paragraph = int(paragraph_result["start"]) + 1
        end_paragraph = int(paragraph_result["end"]) + 1
        history_sentences.append(
            HistorySentence(
                id=f"para-{index}",
                text=paragraph_text,
                raw=paragraph_text,
                start_paragraph=start_paragraph,
                end_paragraph=end_paragraph,
                type=paragraph_type,
                probability=display_probability,
                score=paragraph_score,
                reason=reason_map[paragraph_type],
                suggestion=suggestion_map[paragraph_type],
                token_count=int(paragraph_result.get("token_count") or 0) or None,
                visible_chars=int(paragraph_result.get("visible_chars") or paragraph_result.get("weight") or 0) or None,
                is_truncated=bool(paragraph_result.get("truncated")),
            )
        )
        highlight_class = (
            "bg-slate-100 text-slate-600 ring-1 ring-slate-200"
            if paragraph_type == TOO_SHORT_STATUS
            else _resolve_highlight_class(display_probability)
        )
        html_parts.extend(
            [
                f'<p><span class="highlight-chip {highlight_class}" data-sentence-id="para-{index}">{escape(line)}</span></p>'
                for line in paragraph_text.split("\n")
                if line.strip()
            ]
        )

    if total_weight > 0:
        summary_values = {
            key: max(0, min(int(round((weight / total_weight) * 100)), 100))
            for key, weight in bucket_weights.items()
        }
        diff = sum(summary_values.values()) - 100
        if diff != 0:
            largest_bucket = max(summary_values, key=lambda key: summary_values[key])
            summary_values[largest_bucket] = max(0, min(summary_values[largest_bucket] - diff, 100))
    else:
        summary_values = {"ai": 0, "mixed": 0, "human": 0}
    summary = Summary(ai=summary_values["ai"], mixed=summary_values["mixed"], human=summary_values["human"])

    _ = functions
    translation = ""
    polish = ""
    citations: list[HistoryCitation] = []

    ai_likely_count = sum(1 for item in history_sentences if item.type in {"ai", "mixed"})
    highlighted_html = "".join(html_parts) if html_parts else f"<p>{escape(text)}</p>"

    return Analysis(
        summary=summary,
        sentences=history_sentences,
        translation=translation,
        polish=polish,
        citations=citations,
        ai_likely_count=ai_likely_count,
        highlighted_html=highlighted_html,
    )


def _build_scan_analysis_response(detection_response: DetectionResponse) -> AnalysisResponse:
    analysis = detection_response.result
    if analysis is None:
        return AnalysisResponse(
            summary="No detailed analysis available.",
            sentences=[],
            polish=None,
            translation=None,
            citations=None,
            currentCredits=detection_response.currentCredits,
        )

    sentences = [
        SentenceAnalysis(
            text=segment.text,
            is_ai=segment.type in {"ai", "mixed"},
            confidence=segment.probability,
        )
        for segment in analysis.sentences
    ]
    citations = [
        Citation(source=entry.source, snippet=entry.text, url=None)
        for entry in analysis.citations
    ] or None

    return AnalysisResponse(
        summary=(
            f"AI {analysis.summary.ai}% | "
            f"Mixed {analysis.summary.mixed}% | "
            f"Human {analysis.summary.human}%"
        ),
        sentences=sentences,
        polish=analysis.polish or None,
        translation=analysis.translation or None,
        citations=citations,
        currentCredits=detection_response.currentCredits,
    )


async def _detect_impl(
    payload: DetectionRequest,
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> DetectionResponse:
    if not payload.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Text cannot be empty",
        )

    visible_chars = _count_visible_chars(payload.text)
    if visible_chars < MIN_DETECT_VISIBLE_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "TEXT_TOO_SHORT",
                "message": f"At least {MIN_DETECT_VISIBLE_CHARS} non-whitespace characters are required",
                "detail": {
                    "minimum": MIN_DETECT_VISIBLE_CHARS,
                    "current": visible_chars,
                    "remaining": MIN_DETECT_VISIBLE_CHARS - visible_chars,
                },
            },
        )

    chars = len(payload.text)
    if chars > MAX_DETECT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "TEXT_TOO_LONG",
                "message": f"Text exceeds the {MAX_DETECT_CHARS} character limit",
                "detail": {
                    "maximum": MAX_DETECT_CHARS,
                    "current": chars,
                },
            },
        )
    actor_type = current_actor.actor_type
    actor_id = current_actor.actor_id
    day_start, day_end = get_today_bounds()
    used_today = get_used_today(db, actor_type=actor_type, actor_id=actor_id, start_time=day_start, end_time=day_end)
    limit = get_quota_limit(actor_type)

    if used_today + chars > limit:
        raise _quota_exceeded_http_error(limit=limit, used_today=used_today, remaining=max(limit - used_today, 0))

    try:
        paragraphs = _split_paragraphs(payload.text)
        token_segments = build_token_aware_segments(
            paragraphs,
            short_visible_chars=settings.detect_short_segment_visible_chars,
            max_tokens=settings.detect_max_input_tokens,
            tokenizer_model=settings.detect_tokenizer_model,
        )
        detectable_segments = [segment for segment in token_segments if segment.get("status") == DETECTABLE_STATUS]
        rg_results = await _detect_segments_with_limit(detectable_segments) if detectable_segments else []
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "DETECT_BACKEND_TIMEOUT",
                "message": "Detect service timed out",
                "detail": {"timeout_seconds": settings.detect_request_timeout},
            },
        ) from exc
    except ValueError as exc:
        if str(exc) != "TEXT_TOO_SHORT":
            raise
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "TEXT_TOO_SHORT",
                "message": f"At least {MIN_DETECT_VISIBLE_CHARS} non-whitespace characters are required",
                "detail": {
                    "minimum": MIN_DETECT_VISIBLE_CHARS,
                    "current": visible_chars,
                    "remaining": max(MIN_DETECT_VISIBLE_CHARS - visible_chars, 0),
                },
            },
        ) from exc
    except RepreGuardError as exc:
        if exc.code in {"INPUT_TOO_LONG", "TEXT_TOO_LONG"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "TEXT_TOO_LONG",
                    "message": exc.message,
                    "detail": exc.detail,
                },
            ) from exc

        upstream_status = exc.status_code if exc.status_code in {429, 500, 502, 503, 504} else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(
            status_code=upstream_status,
            detail={
                "code": exc.code or "DETECT_BACKEND_ERROR",
                "message": _public_repre_guard_message(exc),
                "detail": {"upstream_status": exc.status_code},
            },
        ) from exc

    paragraph_scores: list[dict[str, float | str | int | bool]] = []
    total_weight = 0
    weighted_probability = 0.0
    weighted_raw_score = 0.0
    weighted_threshold = 0.0
    model_names: list[str] = []
    score_types: list[str] = []
    rg_iter = iter(rg_results)
    for token_segment in token_segments:
        segment_text = str(token_segment["text"])
        if token_segment.get("status") == TOO_SHORT_STATUS:
            paragraph_scores.append(
                {
                    "text": segment_text,
                    "start": int(token_segment["start"]),
                    "end": int(token_segment["end"]),
                    "raw_score": 0.0,
                    "threshold": 1.0,
                    "probability": 0.0,
                    "weight": 0,
                    "visible_chars": int(token_segment["visible_chars"]),
                    "token_count": int(token_segment.get("token_count") or 0),
                    "score_type": "probability",
                    "status": TOO_SHORT_STATUS,
                    "truncated": bool(token_segment.get("truncated")),
                }
            )
            continue

        rg = next(rg_iter)
        raw_score = float(rg["score"])
        threshold = float(rg["threshold"])
        score_type = str(rg["score_type"])
        probability = _score_to_probability(raw_score, threshold, score_type)
        weight = int(token_segment.get("weight") or token_segment["visible_chars"])
        paragraph_scores.append(
            {
                "text": segment_text,
                "start": int(token_segment["start"]),
                "end": int(token_segment["end"]),
                "raw_score": raw_score,
                "threshold": threshold,
                "probability": probability,
                "weight": weight,
                "visible_chars": int(token_segment["visible_chars"]),
                "token_count": int(token_segment.get("token_count") or 0),
                "score_type": score_type,
                "status": DETECTABLE_STATUS,
                "truncated": bool(token_segment.get("truncated")),
            }
        )
        total_weight += weight
        weighted_probability += probability * weight
        weighted_raw_score += raw_score * weight
        weighted_threshold += threshold * weight
        model_names.append(str(rg["model_name"]))
        score_types.append(score_type)

    total_weight = total_weight or 1
    normalized_score = weighted_probability / total_weight
    raw_score = weighted_raw_score / total_weight
    threshold = weighted_threshold / total_weight if score_types else 1.0
    score_type = score_types[0] if score_types else "probability"
    if any(item != score_type for item in score_types):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "INVALID_DETECT_RESPONSE",
                "message": "Detect service returned inconsistent score_type values",
                "detail": {"score_types": score_types},
            },
        )
    label = "AI" if raw_score >= threshold else "HUMAN"
    provider_model_name = model_names[0] if model_names else None
    model_name = DISPLAY_MODEL_NAME
    functions = _normalize_detection_functions(payload.functions)

    analysis = _build_history_analysis(
        text=payload.text,
        paragraph_scores=paragraph_scores,
        functions=functions,
    )

    options = payload.options.copy() if payload.options else {}
    options.setdefault("repre_guard", {})
    options["repre_guard"].update(
        {
            "raw_score": raw_score,
            "threshold": threshold,
            "label": label,
            "model_name": model_name,
            "provider_model_name": provider_model_name,
            "score_type": score_type,
            "segments": [
                {
                    "index": index,
                    "start": item["start"],
                    "end": item["end"],
                    "start_paragraph": int(item["start"]) + 1,
                    "end_paragraph": int(item["end"]) + 1,
                    "raw_score": item["raw_score"],
                    "threshold": item["threshold"],
                    "probability": item["probability"],
                    "visible_chars": item.get("visible_chars", item["weight"]),
                    "token_count": item.get("token_count"),
                    "weight": item["weight"],
                    "score_type": item["score_type"],
                    "status": item.get("status", DETECTABLE_STATUS),
                    "truncated": item.get("truncated", False),
                }
                for index, item in enumerate(paragraph_scores)
            ],
        }
    )

    user_id = current_actor.user.id if current_actor.user else None
    if actor_type == "user" and user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )

    try:
        quota_result = consume_quota(
            db,
            actor_type=actor_type,
            actor_id=actor_id,
            chars=chars,
            start_time=day_start,
            limit=limit,
            baseline_used=used_today,
        )
    except QuotaExceededError as exc:
        raise _quota_exceeded_http_error(
            limit=exc.limit,
            used_today=exc.used_today,
            remaining=exc.remaining,
        ) from exc

    detection = DetectionService(db).create_detection(
        user_id=user_id,
        text=payload.text,
        editor_html=payload.editor_html,
        options=options,
        functions_used=list(functions),
        label=label.lower(),
        score=normalized_score,
        commit=True,
        actor_type=actor_type,
        actor_id=actor_id,
        chars_used=chars,
        analysis=analysis.model_dump(),
    )

    return DetectionResponse(
        detection_id=detection.id,
        label=label.lower(),
        score=normalized_score,
        model_name=model_name,
        raw_score=raw_score,
        threshold=threshold,
        currentCredits=quota_result.remaining,
        history_id=detection.id,
        input_text=payload.text,
        result=analysis,
    )


def _parse_txt(content: bytes) -> str:
    return content.decode("utf-8")


def _parse_pdf(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def _parse_docx(content: bytes) -> str:
    document = Document(BytesIO(content))
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    return "\n".join(paragraphs).strip()


def _extension_from_filename(filename: str) -> str:
    if not filename or "." not in filename:
        return ""
    return "." + filename.split(".")[-1].lower()


@detect_router.post(
    "/detect",
    response_model=DetectionResponse,
    summary="Detect text and persist a record",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect(
    payload: DetectionRequest,
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> DetectionResponse:
    return await _detect_impl(payload=payload, db=db, current_actor=current_actor)


@scan_router.post(
    "/detect",
    response_model=AnalysisResponse,
    summary="Compatibility analysis endpoint",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect_scan(
    payload: DetectRequest,
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> AnalysisResponse:
    functions = _normalize_detection_functions(payload.functions)

    detection_response = await _detect_impl(
        payload=DetectionRequest(text=payload.text, options=None, functions=list(functions)),
        db=db,
        current_actor=current_actor,
    )

    return _build_scan_analysis_response(detection_response)


@scan_router.post(
    "",
    response_model=AnalysisResponse,
    summary="Compatibility scan endpoint",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect_scan_root(
    payload: DetectRequest,
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> AnalysisResponse:
    return await detect_scan(payload=payload, db=db, current_actor=current_actor)


@detect_router.get(
    "/scan/examples",
    response_model=ScanExamplesResponse,
    summary="Get scan examples",
)
async def get_scan_examples(
    db: SessionDep,
    locale: str = Query("zh-CN", description="Example locale, supports zh-CN / en-US"),
) -> ScanExamplesResponse:
    return ScanExampleService(db).list_examples(locale=locale)


@router.post(
    "/parse-files",
    response_model=ParseFilesResponse,
    summary="Parse uploaded files",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def parse_files(
    current_user: ActiveMemberDep,
    files: list[UploadFile] = File(..., description="Uploaded files"),
) -> ParseFilesResponse:
    _ = current_user
    if len(files) > MAX_FILE_COUNT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Too many files. Max {MAX_FILE_COUNT}.",
        )

    results: list[ParsedFileResult] = []
    for upload in files:
        filename = upload.filename or "unknown"
        extension = _extension_from_filename(filename)
        if extension not in ALLOWED_EXTENSIONS:
            results.append(
                ParsedFileResult(
                    file_name=filename,
                    content=None,
                    error=f"Unsupported file type: {extension or 'unknown'}",
                )
            )
            continue

        data = await upload.read()
        if len(data) > MAX_FILE_SIZE_BYTES:
            results.append(
                ParsedFileResult(
                    file_name=filename,
                    content=None,
                    error=f"File too large. Max {MAX_FILE_SIZE_BYTES} bytes.",
                )
            )
            continue

        try:
            if extension == ".pdf":
                content = _parse_pdf(data)
            elif extension == ".docx":
                content = _parse_docx(data)
            else:
                content = _parse_txt(data)
            results.append(ParsedFileResult(file_name=filename, content=content, error=None))
        except Exception as exc:  # noqa: BLE001
            results.append(
                ParsedFileResult(
                    file_name=filename,
                    content=None,
                    error=f"Failed to parse file: {exc}",
                )
            )

    return ParseFilesResponse(results=results)


async def _list_detections_impl(
    db: SessionDep,
    current_actor: CurrentActorDep,
    page: int,
    page_size: int,
    from_time: datetime | None,
    to_time: datetime | None,
) -> DetectionListResponse:
    if from_time and to_time and from_time > to_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="from must be <= to",
        )

    records, total = DetectionService(db).list_detections(
        actor_type=current_actor.actor_type,
        actor_id=current_actor.actor_id,
        page=page,
        page_size=page_size,
        from_time=from_time,
        to_time=to_time,
    )

    items = [
        DetectionItem(
            id=record.id,
            label=record.result_label,
            score=record.score,
            input_text=record.input_text,
            created_at=record.created_at,
            meta_json=record.meta_json,
        )
        for record in records
    ]

    return DetectionListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


@router.get(
    "/",
    response_model=DetectionListResponse,
    summary="List detections with pagination",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_detections(
    db: SessionDep,
    current_actor: CurrentActorDep,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Page size"),
    from_time: datetime | None = Query(None, alias="from", description="From datetime in ISO8601"),
    to_time: datetime | None = Query(None, alias="to", description="To datetime in ISO8601"),
) -> DetectionListResponse:
    return await _list_detections_impl(
        db=db,
        current_actor=current_actor,
        page=page,
        page_size=page_size,
        from_time=from_time,
        to_time=to_time,
    )


@scan_router.get(
    "/history",
    response_model=DetectionListResponse,
    summary="Compatibility detection history endpoint",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_detections_history(
    db: SessionDep,
    current_actor: CurrentActorDep,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Page size"),
    from_time: datetime | None = Query(None, alias="from", description="From datetime in ISO8601"),
    to_time: datetime | None = Query(None, alias="to", description="To datetime in ISO8601"),
) -> DetectionListResponse:
    return await _list_detections_impl(
        db=db,
        current_actor=current_actor,
        page=page,
        page_size=page_size,
        from_time=from_time,
        to_time=to_time,
    )
