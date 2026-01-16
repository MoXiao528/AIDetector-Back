# backend/app/api/v1/detections.py

from datetime import datetime
from math import exp

from io import BytesIO

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from docx import Document
from pypdf import PdfReader

from app.db.deps import ActiveMemberDep, SessionDep
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
    SentenceAnalysis,
)
from app.schemas.detection import DetectionItem
from app.services.detection_service import DetectionService
from app.services.repre_guard_client import RepreGuardError, repre_guard_client

router = APIRouter(tags=["detections"])
scan_router = APIRouter(prefix="/api/scan", tags=["scan"])

MAX_FILE_COUNT = 5
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def _normalize_score(raw_score: float, threshold: float) -> float:
    """
    简单把 RepreGuard 的原始分数/阈值映射到 0-1 置信度，给前端用。
    这里只用一个 sigmoid(score - threshold) 作为示例：
        score_norm = 1 / (1 + exp(-k * (raw_score - threshold)))
    当 raw_score == threshold 时约 0.5，越大越偏向 AI，越小越偏向 HUMAN。
    """
    k = 2.0  # 斜率超参数，可以根据实际效果微调
    x = raw_score - threshold
    return 1.0 / (1.0 + exp(-k * x))


async def _detect_impl(
    payload: DetectionRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> DetectionResponse:
    if not payload.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Text cannot be empty",
        )

    # 1) 调用 RepreGuard 微服务拿原始结果
    try:
        rg = await repre_guard_client.detect(text=payload.text)
    except RepreGuardError as exc:
        # 下游微服务挂了 / 请求失败等，统一映射为 502
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "DETECT_BACKEND_ERROR", "message": str(exc)},
        ) from exc

    raw_score: float = float(rg["score"])
    threshold: float = float(rg["threshold"])
    label: str = str(rg["label"])
    model_name: str = str(rg["model_name"])

    # 2) 归一化为 0-1 置信度
    normalized_score = _normalize_score(raw_score, threshold)

    # 3) 通过 DetectionService 落一条记录，同时把 RepreGuard 结果塞进 meta_json 里
    service = DetectionService(db)

    # 如果你的 Detection 模型/Service 已经有 meta_json 字段，通常会把 options 存进去；
    # 这里我们在 options 里加一层 "repre_guard" 的信息。
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

    detection = service.create_detection(
        user_id=current_user.id,
        text=payload.text,
        options=options,
        functions_used=payload.functions or None,
        label=label.lower(),
        score=normalized_score,
    )

    # 4) 对客户端返回统一结构
    return DetectionResponse(
        detection_id=detection.id,
        label=label.lower(),
        score=normalized_score,
        model_name=model_name,
        raw_score=raw_score,
        threshold=threshold,
    )


def _build_analysis_response(text: str, functions: set[str]) -> AnalysisResponse:
    sentences_raw = [segment.strip() for segment in text.split(".") if segment.strip()]
    if not sentences_raw:
        sentences_raw = [text.strip()]
    sentences = [
        SentenceAnalysis(text=segment, is_ai=False, confidence=0.1) for segment in sentences_raw
    ]

    polish = None
    if "polish" in functions:
        polish = f"{text.strip()} (polished)"

    translation = None
    if "translation" in functions:
        translation = f"{text.strip()} (translated)"

    citations = None
    if "citations" in functions:
        citations = [
            Citation(
                source="stub",
                snippet="Generated placeholder citation.",
                url=None,
            )
        ]

    return AnalysisResponse(
        summary=f"Analyzed {len(sentences)} sentence(s).",
        sentences=sentences,
        polish=polish,
        translation=translation,
        citations=citations,
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


@router.post(
    "/",
    response_model=DetectionResponse,
    summary="对文本进行检测（调用 RepreGuard 微服务）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect(
    payload: DetectionRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> DetectionResponse:
    """
    调用 RepreGuard 检测微服务，对文本进行 AI/HUMAN 分类，并保存检测记录。
    """
    return await _detect_impl(payload=payload, db=db, current_user=current_user)


@scan_router.post(
    "/detect",
    response_model=AnalysisResponse,
    summary="对文本进行检测（返回富结构分析结果）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect_scan(
    payload: DetectRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> AnalysisResponse:
    functions = set(payload.functions or [])
    if not functions:
        functions.add("scan")
    else:
        functions.add("scan")

    if not payload.text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Text cannot be empty",
        )

    if "scan" in functions:
        await _detect_impl(
            payload=DetectionRequest(text=payload.text, options=None, functions=list(functions)),
            db=db,
            current_user=current_user,
        )

    return _build_analysis_response(text=payload.text, functions=functions)


@scan_router.post(
    "/parse-files",
    response_model=ParseFilesResponse,
    summary="解析上传文件内容（pdf/docx/txt）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def parse_files(
    current_user: ActiveMemberDep,
    files: list[UploadFile] = File(..., description="上传的文件列表"),
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
            results.append(
                ParsedFileResult(
                    file_name=filename,
                    content=content,
                    error=None,
                )
            )
        except Exception as exc:  # noqa: BLE001 - surface per-file errors
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
    current_user: ActiveMemberDep,
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

    service = DetectionService(db)
    records, total = service.list_detections(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        from_time=from_time,
        to_time=to_time,
    )

    items = [
        DetectionItem(
            id=r.id,
            label=r.result_label,
            score=r.score,
            input_text=r.input_text,
            created_at=r.created_at,
            meta_json=r.meta_json,
        )
        for r in records
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
    summary="分页查询检测记录",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_detections(
    db: SessionDep,
    current_user: ActiveMemberDep,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    from_time: datetime | None = Query(
        None,
        alias="from",
        description="开始时间，ISO8601",
    ),
    to_time: datetime | None = Query(
        None,
        alias="to",
        description="结束时间，ISO8601",
    ),
) -> DetectionListResponse:
    """
    按用户分页查询检测记录。
    """
    return await _list_detections_impl(
        db=db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        from_time=from_time,
        to_time=to_time,
    )


@scan_router.get(
    "/history",
    response_model=DetectionListResponse,
    summary="分页查询检测记录（兼容 /api/scan/history）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_detections_history(
    db: SessionDep,
    current_user: ActiveMemberDep,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    from_time: datetime | None = Query(
        None,
        alias="from",
        description="开始时间，ISO8601",
    ),
    to_time: datetime | None = Query(
        None,
        alias="to",
        description="结束时间，ISO8601",
    ),
) -> DetectionListResponse:
    return await _list_detections_impl(
        db=db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        from_time=from_time,
        to_time=to_time,
    )
