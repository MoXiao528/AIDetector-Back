from datetime import datetime
from html import escape
from io import BytesIO
from math import exp
import re

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
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\u3002\uFF01\uFF1F.!?])\s+|\n+")


def _normalize_score(raw_score: float, threshold: float) -> float:
    k = 2.0
    x = raw_score - threshold
    return 1.0 / (1.0 + exp(-k * x))


def _split_sentences(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return []
    parts = [segment.strip() for segment in _SENTENCE_SPLIT_RE.split(normalized) if segment.strip()]
    return parts or [normalized]


def _resolve_sentence_type(score: float, label: str) -> str:
    label_lower = label.lower()
    if label_lower in {"ai", "human", "mixed"}:
        if label_lower == "human" and 0.4 <= score <= 0.6:
            return "mixed"
        return label_lower
    if score >= 0.7:
        return "ai"
    if score >= 0.4:
        return "mixed"
    return "human"


def _build_history_analysis(text: str, score: float, label: str, functions: set[str]) -> Analysis:
    sentences_raw = _split_sentences(text)
    score_percent = max(0, min(int(round(score * 100)), 100))
    sentence_type = _resolve_sentence_type(score, label)

    if sentence_type == "mixed":
        summary = Summary(ai=max(score_percent - 15, 0), mixed=min(30, 100), human=max(100 - score_percent - 15, 0))
        total = summary.ai + summary.mixed + summary.human
        if total != 100:
            summary.human = max(0, summary.human + (100 - total))
    else:
        summary = Summary(
            ai=score_percent,
            mixed=0,
            human=max(0, 100 - score_percent),
        )

    reason_map = {
        "ai": "Sentence structure is highly uniform and resembles machine-generated text.",
        "mixed": "This passage shows some templated patterns and should be reviewed manually.",
        "human": "The phrasing varies naturally and is closer to human writing.",
    }
    suggestion_map = {
        "ai": "Add specific details and more personal phrasing to reduce template-like patterns.",
        "mixed": "Add concrete examples or lived details to make the passage less formulaic.",
        "human": "The current passage already reads naturally.",
    }
    class_map = {
        "ai": "bg-amber-100 text-amber-900",
        "mixed": "bg-violet-100 text-violet-900",
        "human": "bg-emerald-100 text-emerald-900",
    }

    history_sentences = []
    html_parts = []
    for index, sentence_text in enumerate(sentences_raw):
        history_sentences.append(
            HistorySentence(
                id=f"sent-{index}",
                text=sentence_text,
                raw=sentence_text,
                type=sentence_type,
                probability=score,
                score=score_percent,
                reason=reason_map[sentence_type],
                suggestion=suggestion_map[sentence_type],
            )
        )
        html_parts.append(
            f'<span class="highlight-chip {class_map[sentence_type]}" data-sentence-id="sent-{index}">{escape(sentence_text)}</span>'
        )

    translation = ""
    if {"translate", "translation"} & functions:
        translation = f"{text.strip()} (translated)"

    polish = ""
    if "polish" in functions:
        polish = f"{text.strip()} (polished)"

    citations: list[HistoryCitation] = []
    if {"citation", "citations"} & functions:
        citations.append(
            HistoryCitation(
                id="cite-0",
                text=sentences_raw[0] if sentences_raw else text.strip(),
                source="Generated placeholder citation",
            )
        )

    ai_likely_count = sum(1 for item in history_sentences if item.type in {"ai", "mixed"})
    highlighted_html = " ".join(html_parts) if html_parts else f"<p>{escape(text)}</p>"

    return Analysis(
        summary=summary,
        sentences=history_sentences,
        translation=translation,
        polish=polish,
        citations=citations,
        ai_likely_count=ai_likely_count,
        highlighted_html=highlighted_html,
    )


def _build_scan_analysis_response(text: str, functions: set[str], current_credits: int) -> AnalysisResponse:
    sentences = [
        SentenceAnalysis(text=segment, is_ai=False, confidence=0.1)
        for segment in (_split_sentences(text) or [text.strip()])
    ]

    polish = f"{text.strip()} (polished)" if "polish" in functions else None
    translation = f"{text.strip()} (translated)" if {"translate", "translation"} & functions else None
    citations = None
    if {"citation", "citations"} & functions:
        citations = [Citation(source="stub", snippet="Generated placeholder citation.", url=None)]

    return AnalysisResponse(
        summary=f"Analyzed {len(sentences)} sentence(s).",
        sentences=sentences,
        polish=polish,
        translation=translation,
        citations=citations,
        currentCredits=current_credits,
    )


async def _detect_impl(
    payload: DetectionRequest,
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> DetectionResponse:
    if not payload.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Text cannot be empty",
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
        rg = await repre_guard_client.detect(text=payload.text)
    except RepreGuardError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "DETECT_BACKEND_ERROR", "message": str(exc)},
        ) from exc

    raw_score = float(rg["score"])
    threshold = float(rg["threshold"])
    label = str(rg["label"])
    model_name = str(rg["model_name"])
    normalized_score = _normalize_score(raw_score, threshold)
    remaining_after = max(limit - (used_today + chars), 0)

    functions = set(payload.functions or [])
    if not functions:
        functions.add("scan")

    analysis = _build_history_analysis(
        text=payload.text,
        score=normalized_score,
        label=label,
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
    functions = set(payload.functions or [])
    functions.add("scan")

    detection_response = await _detect_impl(
        payload=DetectionRequest(text=payload.text, options=None, functions=list(functions)),
        db=db,
        current_actor=current_actor,
    )

    return _build_scan_analysis_response(
        text=payload.text,
        functions=functions,
        current_credits=detection_response.currentCredits,
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
