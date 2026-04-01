import asyncio
from datetime import datetime
from html import escape
from io import BytesIO
from math import exp

from docx import Document
from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pypdf import PdfReader

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
from app.services.quota_service import get_quota_limit, get_today_bounds, get_used_today
from app.services.repre_guard_client import RepreGuardError, repre_guard_client
from app.services.scan_example_service import ScanExampleService

router = APIRouter(tags=["detections"])
detect_router = APIRouter(tags=["detections"])
scan_router = APIRouter(prefix="/api/scan", tags=["scan"])

MAX_FILE_COUNT = 5
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
SUPPORTED_DETECTION_FUNCTIONS = {"scan"}
# 这是产品层的最小送检门槛，不是模型协议本身的硬要求。
# 后面如果要改，前端输入限制、README、合并规则和测试要一起改。
MIN_DETECT_VISIBLE_CHARS = 200
# 这是对用户展示的模型版本标签。
# 如果上游真实模型变了，不要只改这里，README、前端文案和测试一起对齐。
DISPLAY_MODEL_NAME = "v1.0"


def _normalize_score(raw_score: float, threshold: float) -> float:
    # 当前用阈值中心化的 sigmoid 做展示概率换算。
    # 如果以后要校准公式，前后端展示文案和文档要一起更新。
    x = raw_score - threshold
    return 1.0 / (1.0 + exp(-x))


def _split_paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    return [segment.strip() for segment in normalized.split("\n") if segment.strip()]


def _count_visible_chars(text: str) -> int:
    return len("".join(str(text).split()))


def _merge_short_paragraphs(paragraphs: list[str], min_chars: int = MIN_DETECT_VISIBLE_CHARS) -> list[dict[str, int | str]]:
    # 检测块的真实边界在这里决定。
    # 如果要改“短段自动合并”规则，前端预览映射和结果文案必须一起改。
    merged: list[dict[str, int | str]] = []
    buffer: list[str] = []
    buffer_visible_chars = 0
    start_index = 0

    for index, paragraph in enumerate(paragraphs):
        cleaned = paragraph.strip()
        if not cleaned:
            continue

        if not buffer:
            start_index = index

        buffer.append(cleaned)
        buffer_visible_chars += _count_visible_chars(cleaned)

        if buffer_visible_chars >= min_chars:
            merged.append(
                {
                    "start": start_index,
                    "end": index,
                    "text": "\n".join(buffer),
                    "visible_chars": buffer_visible_chars,
                }
            )
            buffer = []
            buffer_visible_chars = 0

    if buffer:
        if merged:
            suffix = "\n".join(buffer)
            merged[-1]["text"] = f'{merged[-1]["text"]}\n{suffix}'
            merged[-1]["end"] = len(paragraphs) - 1
            merged[-1]["visible_chars"] = int(merged[-1]["visible_chars"]) + buffer_visible_chars
        else:
            raise ValueError("TEXT_TOO_SHORT")

    return merged


def _resolve_segment_type(score: float) -> str:
    if score >= 0.67:
        return "ai"
    if score >= 0.34:
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


def _build_history_analysis(
    text: str,
    paragraph_scores: list[dict[str, float | str | int]],
    functions: set[str],
) -> Analysis:
    total_weight = sum(int(item["weight"]) for item in paragraph_scores) or 1
    weighted_probability = sum(float(item["probability"]) * int(item["weight"]) for item in paragraph_scores) / total_weight
    score_percent = max(0, min(int(round(weighted_probability * 100)), 100))
    summary = Summary(ai=score_percent, mixed=0, human=max(0, 100 - score_percent))

    reason_map = {
        "ai": "This segment is highly patterned and is more likely to be machine-generated.",
        "mixed": "This segment is borderline and should be reviewed manually.",
        "human": "This segment reads more like natural human writing.",
    }
    suggestion_map = {
        "ai": "Add concrete facts, distinctive examples, and less templated phrasing.",
        "mixed": "Add more specific detail or domain context to make this segment less formulaic.",
        "human": "This segment already looks relatively natural.",
    }

    history_sentences = []
    html_parts: list[str] = []
    for index, paragraph_result in enumerate(paragraph_scores):
        paragraph_text = str(paragraph_result["text"])
        probability = float(paragraph_result["probability"])
        paragraph_type = _resolve_segment_type(probability)
        paragraph_score = max(0, min(int(round(probability * 100)), 100))
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
                probability=probability,
                score=paragraph_score,
                reason=reason_map[paragraph_type],
                suggestion=suggestion_map[paragraph_type],
            )
        )
        html_parts.extend(
            [
                f'<p><span class="highlight-chip {_resolve_highlight_class(probability)}" data-sentence-id="para-{index}">{escape(line)}</span></p>'
                for line in paragraph_text.split("\n")
                if line.strip()
            ]
        )

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
            is_ai=segment.type != "human",
            confidence=segment.probability,
        )
        for segment in analysis.sentences
    ]
    citations = [
        Citation(source=entry.source, snippet=entry.text, url=None)
        for entry in analysis.citations
    ] or None

    return AnalysisResponse(
        summary=f"AI {analysis.summary.ai}% | Human {max(0, 100 - analysis.summary.ai)}%",
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
    actor_type = current_actor.actor_type
    actor_id = current_actor.actor_id
    day_start, day_end = get_today_bounds()
    used_today = get_used_today(db, actor_type=actor_type, actor_id=actor_id, start_time=day_start, end_time=day_end)
    limit = get_quota_limit(actor_type)

    if used_today + chars > limit:
        remaining = max(limit - used_today, 0)
        raise HTTPException(
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

    try:
        paragraphs = _split_paragraphs(payload.text)
        merged_paragraphs = _merge_short_paragraphs(paragraphs)
        rg_results = await asyncio.gather(
            *(repre_guard_client.detect(text=str(segment["text"])) for segment in merged_paragraphs)
        )
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "DETECT_BACKEND_ERROR", "message": str(exc)},
        ) from exc

    paragraph_scores: list[dict[str, float | str | int]] = []
    total_weight = 0
    weighted_probability = 0.0
    weighted_raw_score = 0.0
    weighted_threshold = 0.0
    model_names: list[str] = []
    for merged_segment, rg in zip(merged_paragraphs, rg_results, strict=True):
        segment_text = str(merged_segment["text"])
        raw_score = float(rg["score"])
        threshold = float(rg["threshold"])
        probability = _normalize_score(raw_score, threshold)
        weight = int(merged_segment["visible_chars"])
        paragraph_scores.append(
            {
                "text": segment_text,
                "start": int(merged_segment["start"]),
                "end": int(merged_segment["end"]),
                "raw_score": raw_score,
                "threshold": threshold,
                "probability": probability,
                "weight": weight,
            }
        )
        total_weight += weight
        weighted_probability += probability * weight
        weighted_raw_score += raw_score * weight
        weighted_threshold += threshold * weight
        model_names.append(str(rg["model_name"]))

    total_weight = total_weight or 1
    normalized_score = weighted_probability / total_weight
    raw_score = weighted_raw_score / total_weight
    threshold = weighted_threshold / total_weight
    label = "AI" if normalized_score >= 0.5 else "HUMAN"
    # 保留上游真实模型名用于排障和后续切换，对用户仍统一展示 DISPLAY_MODEL_NAME。
    provider_model_name = model_names[0] if model_names else None
    model_name = DISPLAY_MODEL_NAME
    remaining_after = max(limit - (used_today + chars), 0)

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
                    "visible_chars": item["weight"],
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
        currentCredits=remaining_after,
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
