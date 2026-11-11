import asyncio
import hashlib
import json
from datetime import datetime
from html import escape
from math import ceil, exp
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.deps import (
    CurrentActorDep,
    DetectActorDep,
    PreflightAuthorizationDep,
    SessionDep,
    _get_active_guest_session_id,
)
from app.models.detection import Detection
from app.schemas import (
    AnalysisResponse,
    Citation,
    DetectRequest,
    DetectionListResponse,
    DetectionRequest,
    DetectionResponse,
    ErrorResponse,
    ScanExamplesResponse,
    SentenceAnalysis,
)
from app.schemas.detection import DetectionItem, DetectionPreflightRequest, DetectionPreflightResponse
from app.schemas.history import Analysis, Citation as HistoryCitation, Sentence as HistorySentence, Summary, project_document_analysis
from app.services import token_chunker
from app.services.document_metadata import paragraph_highlight_html
from app.services.detection_service import DetectionService
from app.services.quota_service import (
    DetectionActorBusyError,
    DetectionRequestInProgressError,
    DetectionReservationLostError,
    IdempotencyKeyConflictError,
    QuotaExceededError,
    complete_detection_request,
    consume_reserved_quota,
    fail_detection_request,
    get_effective_used_today,
    get_quota_limit,
    get_today_bounds,
    lock_detection_settlement,
    reserve_detection_request,
)
from app.services.repre_guard_client import RepreGuardError, repre_guard_client
from app.services.scan_example_service import ScanExampleService
from app.services.token_chunker import (
    DETECTABLE_STATUS,
    TARGET_INPUT_TOKENS,
    TOO_SHORT_STATUS,
    TokenSegment,
    build_token_aware_segments,
    count_tokens,
)

router = APIRouter(tags=["detections"])
detect_router = APIRouter(tags=["detections"])
scan_router = APIRouter(prefix="/scan", tags=["scan"])
settings = get_settings()

SUPPORTED_DETECTION_FUNCTIONS = {"scan"}
MAX_DETECT_CHARS = 20000
DISPLAY_MODEL_NAME = "v2.0-roberta"
DETECTION_LEASE_BUFFER_SECONDS = 30


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


def _measure_detection_text(text: str) -> DetectionPreflightResponse:
    if len(text) > MAX_DETECT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "TEXT_TOO_LONG",
                "message": f"Text exceeds the {MAX_DETECT_CHARS} character limit",
                "detail": {"maximum": MAX_DETECT_CHARS, "current": len(text)},
            },
        )

    character_count = _count_visible_chars(text)
    token_count = 0
    estimated_additional_characters = None
    if character_count:
        normalized = "\n".join(_split_paragraphs(text)).strip()
        token_count = count_tokens(normalized, tokenizer_model=settings.detect_tokenizer_model)
        if token_count >= TARGET_INPUT_TOKENS:
            estimated_additional_characters = 0
        else:
            tokenizer = token_chunker.get_tokenizer(settings.detect_tokenizer_model)
            content_tokens = token_count - tokenizer.num_special_tokens_to_add(pair=False)
            if content_tokens > 0:
                estimated_additional_characters = ceil(
                    character_count * (TARGET_INPUT_TOKENS - token_count) / content_tokens
                )

    return DetectionPreflightResponse(
        character_count=character_count,
        token_count=token_count,
        minimum_tokens=TARGET_INPUT_TOKENS,
        ready=token_count >= TARGET_INPUT_TOKENS,
        estimated_additional_characters=estimated_additional_characters,
    )


def _text_too_short_error(measurement: DetectionPreflightResponse) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "TEXT_TOO_SHORT",
            "message": f"At least {TARGET_INPUT_TOKENS} model tokens are required",
            "detail": {**measurement.model_dump(by_alias=True), "unit": "tokens"},
        },
    )


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
        weight = max(count_tokens(part.strip(), add_special_tokens=False, tokenizer_model=settings.detect_tokenizer_model), 1)
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


def _resolve_segment_type(score: float, threshold: float, score_type: str) -> str:
    return "ai" if score >= threshold else "human"


def _normalize_detection_functions(functions: list[str] | None) -> set[str]:
    normalized = {
        item.strip().lower()
        for item in (functions or [])
        if isinstance(item, str) and item.strip()
    }
    supported = normalized & SUPPORTED_DETECTION_FUNCTIONS
    supported.add("scan")
    return supported


async def _detect_segments_with_limit(segments: list[TokenSegment]) -> list[dict]:
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


def _build_detection_request_hash(payload: DetectionRequest, *, operation: str) -> str:
    canonical_payload = {
        "operation": operation,
        "text": payload.text,
        "functions": sorted(_normalize_detection_functions(payload.functions)),
        "options": payload.options,
        "editor_html": payload.editor_html,
    }
    canonical_json = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _idempotency_http_error(
    *,
    status_code: int,
    code: str,
    message: str,
    retry_after: int | None = None,
) -> HTTPException:
    headers = {"Retry-After": str(max(1, retry_after))} if retry_after is not None else None
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "detail": None},
        headers=headers,
    )


def _replay_detection_response(
    db: SessionDep,
    *,
    detection_id: int | None,
    actor_type: str,
    actor_id: str,
) -> DetectionResponse:
    detection = db.get(Detection, detection_id) if detection_id is not None else None
    if detection is None:
        raise _idempotency_http_error(
            status_code=status.HTTP_410_GONE,
            code="IDEMPOTENCY_RESULT_GONE",
            message="The stored result for this idempotency key is no longer available",
        )

    meta = detection.meta_json if isinstance(detection.meta_json, dict) else {}
    options = meta.get("options") if isinstance(meta.get("options"), dict) else {}
    repre_guard = options.get("repre_guard") if isinstance(options.get("repre_guard"), dict) else {}
    analysis_payload = meta.get("analysis")
    try:
        analysis = project_document_analysis(analysis_payload, detection.input_text, detection.editor_html) if isinstance(analysis_payload, dict) else None
    except ValueError as exc:
        raise _idempotency_http_error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="IDEMPOTENCY_RESULT_INVALID",
            message="The stored detection result cannot be reconstructed",
        ) from exc

    day_start, day_end = get_today_bounds()
    used_today = get_effective_used_today(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        start_time=day_start,
        end_time=day_end,
    )
    limit = get_quota_limit(actor_type)
    return DetectionResponse(
        detection_id=detection.id,
        label=detection.result_label,
        score=detection.score,
        model_name=repre_guard.get("model_name"),
        raw_score=repre_guard.get("raw_score"),
        threshold=repre_guard.get("threshold"),
        currentCredits=max(limit - used_today, 0),
        history_id=detection.id,
        input_text=detection.input_text,
        result=analysis,
        evidence=meta.get("evidence"),
    )


def _aggregate_paragraph_scores(
    paragraphs: list[str],
    token_segments: list[TokenSegment],
    segment_scores: list[dict[str, float | str | int | bool]],
) -> list[dict[str, float | str | int | bool]]:
    paragraph_scores: list[dict[str, float | str | int | bool]] = [
        {
            "text": paragraph,
            "start": index,
            "end": index,
            "raw_score": 0.0,
            "threshold": 0.0,
            "probability": 0.0,
            "weight": 0.0,
            "visible_chars": _count_visible_chars(paragraph),
            "token_count": count_tokens(paragraph.strip(), tokenizer_model=settings.detect_tokenizer_model),
            "score_type": "probability",
            "status": TOO_SHORT_STATUS,
            "truncated": False,
        }
        for index, paragraph in enumerate(paragraphs)
    ]
    for segment, score in zip(token_segments, segment_scores, strict=True):
        if score["status"] == TOO_SHORT_STATUS:
            continue
        for part in segment["paragraph_parts"]:
            paragraph = paragraph_scores[part["index"]]
            weight = part["weight"]
            for field in ("raw_score", "threshold", "probability"):
                paragraph[field] = float(paragraph[field]) + float(score[field]) * weight
            paragraph["weight"] = float(paragraph["weight"]) + weight
            paragraph["score_type"] = score["score_type"]
            paragraph["status"] = DETECTABLE_STATUS
    for paragraph in paragraph_scores:
        weight = float(paragraph["weight"])
        if weight:
            for field in ("raw_score", "threshold", "probability"):
                paragraph[field] = float(paragraph[field]) / weight
    return paragraph_scores


def _build_history_analysis(
    text: str,
    paragraph_scores: list[dict[str, float | str | int | bool]],
    segment_scores: list[dict[str, float | str | int | bool]],
    functions: set[str],
) -> Analysis:
    total_weight = sum(int(item["weight"]) for item in segment_scores if item.get("status") != TOO_SHORT_STATUS)
    bucket_weights = {"ai": 0, "human": 0}
    for segment in segment_scores:
        if segment.get("status") != TOO_SHORT_STATUS:
            segment_type = _resolve_segment_type(
                float(segment["raw_score"]), float(segment["threshold"]), str(segment["score_type"])
            )
            bucket_weights[segment_type] += int(segment["weight"])

    reason_map = {
        "ai": "This segment is highly patterned and is more likely to be machine-generated.",
        "human": "This segment reads more like natural human writing.",
        TOO_SHORT_STATUS: "This segment is too short for a reliable model judgment.",
    }
    suggestion_map = {
        "ai": "Add concrete facts, distinctive examples, and less templated phrasing.",
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
        html_parts.append(paragraph_highlight_html(paragraph_text, f"para-{index}", paragraph_type, display_probability))

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
        summary_values = {"ai": 0, "human": 0}
    summary = Summary(ai=summary_values["ai"], human=summary_values["human"])

    _ = functions
    translation = ""
    polish = ""
    citations: list[HistoryCitation] = []

    ai_likely_count = sum(1 for item in history_sentences if item.type == "ai")
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
            type=segment.type,
            is_ai=segment.type == "ai",
            confidence=segment.probability,
        )
        for segment in analysis.sentences
    ]
    citations = [
        Citation(source=entry.source, snippet=entry.text, url=None)
        for entry in analysis.citations
    ] or None

    return AnalysisResponse(
        summary=f"AI {analysis.summary.ai}% | Human {analysis.summary.human}%",
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
    idempotency_key: UUID,
    *,
    operation: str,
    request: Request | None = None,
) -> DetectionResponse:
    measurement = await run_in_threadpool(_measure_detection_text, payload.text)
    if not measurement.ready:
        raise _text_too_short_error(measurement)
    chars = len(payload.text)
    paragraphs = _split_paragraphs(payload.text)
    token_segments = await run_in_threadpool(
        build_token_aware_segments,
        paragraphs,
        short_visible_chars=1,
        max_tokens=settings.detect_max_input_tokens,
        tokenizer_model=settings.detect_tokenizer_model,
    )

    actor_type = current_actor.actor_type
    actor_id = current_actor.actor_id
    user_id = current_actor.user.id if current_actor.user else None
    if actor_type == "user" and user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )

    functions = _normalize_detection_functions(payload.functions)
    request_hash = _build_detection_request_hash(payload, operation=operation)
    limit = get_quota_limit(actor_type)
    lease_seconds = max(int(settings.detect_request_timeout), int(settings.detect_service_timeout))
    lease_seconds += DETECTION_LEASE_BUFFER_SECONDS
    evidence_engine = getattr(request.app.state, "evidence_engine", None) if request else None
    if evidence_engine is not None and evidence_engine.status == "ready":
        lease_seconds += ceil(evidence_engine.timeout_seconds)

    try:
        admission = reserve_detection_request(
            db,
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=str(idempotency_key),
            request_hash=request_hash,
            chars=chars,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        if actor_type == "guest":
            _get_active_guest_session_id(db, actor_id, lock=True)
        db.commit()
    except IdempotencyKeyConflictError as exc:
        db.commit()
        raise _idempotency_http_error(
            status_code=status.HTTP_409_CONFLICT,
            code="IDEMPOTENCY_KEY_CONFLICT",
            message="This idempotency key was already used with a different request",
        ) from exc
    except DetectionRequestInProgressError as exc:
        db.commit()
        raise _idempotency_http_error(
            status_code=status.HTTP_409_CONFLICT,
            code="DETECTION_IN_PROGRESS",
            message="This detection request is still in progress",
            retry_after=exc.retry_after,
        ) from exc
    except DetectionActorBusyError as exc:
        db.commit()
        raise _idempotency_http_error(
            status_code=status.HTTP_409_CONFLICT,
            code="DETECTION_ACTOR_BUSY",
            message="Another detection request is already in progress",
            retry_after=exc.retry_after,
        ) from exc
    except QuotaExceededError as exc:
        db.commit()
        raise _quota_exceeded_http_error(
            limit=exc.limit,
            used_today=exc.used_today,
            remaining=exc.remaining,
        ) from exc
    except Exception:
        db.rollback()
        raise

    if admission.state == "completed":
        return _replay_detection_response(
            db,
            detection_id=admission.detection_id,
            actor_type=actor_type,
            actor_id=actor_id,
        )

    detectable_segments = [segment for segment in token_segments if segment.get("status") == DETECTABLE_STATUS]
    try:
        rg_results = await _detect_segments_with_limit(detectable_segments) if detectable_segments else []
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "DETECT_BACKEND_TIMEOUT",
                "message": "Detect service timed out",
                "detail": {"timeout_seconds": settings.detect_request_timeout},
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

    segment_scores: list[dict[str, float | str | int | bool]] = []
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
            segment_scores.append(
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
        segment_scores.append(
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

    analysis = _build_history_analysis(
        text=payload.text,
        paragraph_scores=_aggregate_paragraph_scores(paragraphs, token_segments, segment_scores),
        segment_scores=segment_scores,
        functions=functions,
    )
    analysis = project_document_analysis(analysis, payload.text, payload.editor_html)

    options = payload.options.copy() if payload.options else {}
    options["repre_guard"] = {
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
            for index, item in enumerate(segment_scores)
        ],
    }

    evidence = None
    if evidence_engine is not None and evidence_engine.status != "off":
        evidence = await evidence_engine.run(payload.text, main_label=label, client=repre_guard_client)

    request_record = None
    try:
        request_record, quota_row = lock_detection_settlement(
            db,
            admission=admission,
            limit=limit,
        )
        if actor_type == "guest":
            try:
                _get_active_guest_session_id(db, actor_id, lock=True)
            except HTTPException:
                fail_detection_request(request_record)
                db.commit()
                raise

        quota_result = consume_reserved_quota(
            quota_row,
            chars=admission.reserved_chars,
            limit=limit,
        )
        detection = DetectionService(db).create_detection(
            user_id=user_id,
            text=payload.text,
            editor_html=payload.editor_html,
            options=options,
            functions_used=list(functions),
            label=label.lower(),
            score=normalized_score,
            commit=False,
            actor_type=actor_type,
            actor_id=actor_id,
            chars_used=chars,
            analysis=analysis.model_dump(),
            evidence=evidence,
        )
        detection_id = detection.id
        evidence = detection.meta_json.get("evidence")
        complete_detection_request(request_record, detection_id=detection_id)
        db.commit()
    except QuotaExceededError as exc:
        if request_record is not None:
            fail_detection_request(request_record)
            try:
                db.commit()
            except Exception:
                db.rollback()
        raise _quota_exceeded_http_error(
            limit=exc.limit,
            used_today=exc.used_today,
            remaining=exc.remaining,
        ) from exc
    except DetectionReservationLostError as exc:
        db.rollback()
        raise _idempotency_http_error(
            status_code=status.HTTP_409_CONFLICT,
            code="DETECTION_RESERVATION_LOST",
            message="The detection reservation expired or was superseded",
            retry_after=1,
        ) from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return DetectionResponse(
        detection_id=detection_id,
        label=label.lower(),
        score=normalized_score,
        model_name=model_name,
        raw_score=raw_score,
        threshold=threshold,
        currentCredits=quota_result.remaining,
        history_id=detection_id,
        input_text=payload.text,
        result=analysis,
        evidence=evidence,
    )


@detect_router.post(
    "/detect/preflight",
    response_model=DetectionPreflightResponse,
    summary="Check input length without running detection",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def preflight_detection(
    payload: DetectionPreflightRequest,
    _: PreflightAuthorizationDep,
) -> DetectionPreflightResponse:
    return _measure_detection_text(payload.text)


@detect_router.post(
    "/detect",
    response_model=DetectionResponse,
    summary="Detect text and persist a record",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def detect(
    payload: DetectionRequest,
    db: SessionDep,
    current_actor: DetectActorDep,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    request: Request = None,
) -> DetectionResponse:
    return await _detect_impl(
        payload=payload,
        db=db,
        current_actor=current_actor,
        idempotency_key=idempotency_key,
        operation="detect",
        request=request,
    )


@scan_router.post(
    "/detect",
    response_model=AnalysisResponse,
    summary="Compatibility analysis endpoint",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def detect_scan(
    payload: DetectRequest,
    db: SessionDep,
    current_actor: DetectActorDep,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    request: Request = None,
) -> AnalysisResponse:
    functions = _normalize_detection_functions(payload.functions)

    detection_response = await _detect_impl(
        payload=DetectionRequest(text=payload.text, options=None, functions=list(functions)),
        db=db,
        current_actor=current_actor,
        idempotency_key=idempotency_key,
        operation="scan",
        request=request,
    )

    return _build_scan_analysis_response(detection_response)


@scan_router.post(
    "",
    response_model=AnalysisResponse,
    summary="Compatibility scan endpoint",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        410: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def detect_scan_root(
    payload: DetectRequest,
    db: SessionDep,
    current_actor: DetectActorDep,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    request: Request = None,
) -> AnalysisResponse:
    return await detect_scan(
        payload=payload,
        db=db,
        current_actor=current_actor,
        idempotency_key=idempotency_key,
        request=request,
    )


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
