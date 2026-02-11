# backend/app/api/v1/detections.py

from datetime import datetime
from math import exp

from io import BytesIO

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from docx import Document  # Requires python-docx package.
from pypdf import PdfReader  # Requires pypdf package.

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
    SentenceAnalysis,
)
from app.schemas.detection import DetectionItem
from app.schemas.history import Analysis, Summary, Sentence as HistorySentence
from app.services.detection_service import DetectionService
from app.services.quota_service import get_quota_limit, get_today_bounds, get_used_today
from app.services.repre_guard_client import RepreGuardError, repre_guard_client

router = APIRouter(tags=["detections"])
detect_router = APIRouter(tags=["detections"])
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

    # 3) 构建 AnalysisResponse 结构的数据（服务器端生成 summary / sentences 等）
    #    注意：原本 _build_analysis_response 是在 /scan/detect 里调用的，现在提出来通用复用
    #    这里我们需要手动构建一个 dict 存入 meta_json["analysis"]
    
    # 临时构建一个函数集合，如果没有传 functions 则默认 scan
    funcs_set = set(payload.functions or [])
    if not funcs_set:
        funcs_set.add("scan") # 默认加上 scan 以生成句子分析

    # 为了生成 analysis 数据，我们需要先计算 credits (因为 _build_analysis_response 需要 current_credits)
    # 但实际上存库的时候不需要 current_credits，只需要 analysis 的核心数据。
    # 我们这里手动构建核心数据，或者复用 _build_analysis_response 后转 dict
    
    # 复用 _build_analysis_response 逻辑:
    # 注意：_build_analysis_response 目前返回的是 AnalysisResponse 对象，我们需要它的 .model_dump()
    # 但 _build_analysis_response 需要 current_credits，我们先算一下
    remaining_after = max(limit - (used_today + chars), 0)
    
    analysis_resp = _build_analysis_response(
        text=payload.text,
        functions=funcs_set,
        current_credits=remaining_after
    )
    # 提取 analysis 核心字段 (去除非持久化字段 if any, currently AnalysisResponse mix responses fields)
    # AnalysisResponse 包含 summary, sentences, polish, translation, citations, currentCredits
    # 我们存库时不需要 currentCredits
    analysis_dict = analysis_resp.model_dump(exclude={"currentCredits"})

    # 4) 通过 DetectionService 落一条记录
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
    user_id = current_actor.user.id if current_actor.user else None
    if actor_type == "user" and user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )

    detection = service.create_detection(
        user_id=user_id,
        text=payload.text,
        options=options,
        functions_used=payload.functions or list(funcs_set), # 确保存入使用的 functions
        label=label.lower(),
        score=normalized_score,
        commit=True,
        actor_type=actor_type,
        actor_id=actor_id,
        chars_used=chars,
        analysis=analysis_dict, # <--- 新增：传入 analysis
    )

    # 5) 对客户端返回统一结构
    return DetectionResponse(
        detection_id=detection.id,
        label=label.lower(),
        score=normalized_score,
        model_name=model_name,
        raw_score=raw_score,
        threshold=threshold,
        currentCredits=remaining_after,
        history_id=detection.id, # <--- 新增：返回 history_id
    )


def _build_analysis_response_for_history(text: str, score: float, label: str) -> dict:
    """
    构建符合 History Analysis Schema 的字典数据。
    注意：这里仅做简单的后端生成示例。如果需要复杂的句子级 AI 判断逻辑，需要在此扩展。
    目前逻辑：
    - 基于 Global Score 估算 Summary
    - 简单拆分句子
    - 生成简单的 highlighted_html
    """
    sentences_raw = [segment.strip() for segment in text.split(".") if segment.strip()]
    if not sentences_raw:
        sentences_raw = [text.strip()]

    # 1. 估算 Summary (Mock Logic: 基于全局分数)
    # score 是 AI 书写的概率 (0-1, normalized)
    ai_percent = int(score * 100)
    human_percent = 100 - ai_percent
    mixed_percent = 0  # 暂时简化

    summary_obj = Summary(
        ai=ai_percent,
        human=human_percent,
        mixed=mixed_percent
    )

    # 2. 构建 Sentences & HTML
    # 简单策略：如果 score > 0.8，认为大部分是 AI；否则根据分段假定
    # 这里为了演示，全部统一标记，或者随机。
    # 更好的做法是 RepreGuard 如果提供句子级分数则用之。
    # 现阶段：全部标记为与全局 label 一致。
    
    history_sentences = []
    html_parts = []
    
    is_global_ai = label.lower() == "ai"
    
    for idx, sent_text in enumerate(sentences_raw):
        # 模拟句子级分数：简单取全局分数
        sent_score = int(score * 100)
        sent_prob = score
        sent_type = "ai" if is_global_ai else "human"
        
        # 构建 Sentence 对象
        s_obj = HistorySentence(
            id=f"sent-{idx}",
            text=sent_text,
            raw=sent_text,
            type=sent_type,
            probability=sent_prob,
            score=sent_score,
            reason="Based on global detection" if is_global_ai else "Looks natural",
            suggestion=""
        )
        history_sentences.append(s_obj)

        # 构建 HTML (前端 CSS class 需对应)
        # 假设前端样式类： .ai-sentence, .human-sentence
        css_class = "ai-sentence" if is_global_ai else "human-sentence"
        html_parts.append(f'<span class="{css_class}">{sent_text}.</span>')

    highlighted_html = "".join(html_parts)
    if not highlighted_html:
        highlighted_html = f"<p>{text}</p>"

    # 3. 组装 Analysis 对象
    analysis_obj = Analysis(
        summary=summary_obj,
        sentences=history_sentences,
        translation="",
        polish="",
        citations=[],
        ai_likely_count=len(sentences_raw) if is_global_ai else 0,
        highlighted_html=highlighted_html
    )

    return analysis_obj.model_dump()


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


def _build_analysis_response(text: str, functions: set[str], current_credits: int) -> AnalysisResponse:
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

    # 3) 构建符合 History Schema 的 Analysis 数据
    analysis_dict = _build_analysis_response_for_history(
        text=payload.text, 
        score=normalized_score, 
        label=label
    )

    # 4) 通过 DetectionService 落一条记录
    service = DetectionService(db)

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
    
    # 确保存入 functions
    funcs_list = payload.functions or ["scan"]

    detection = service.create_detection(
        user_id=user_id,
        text=payload.text,
        options=options,
        functions_used=funcs_list,
        label=label.lower(),
        score=normalized_score,
        commit=True,
        actor_type=actor_type,
        actor_id=actor_id,
        chars_used=chars,
        analysis=analysis_dict, # 传入符合 schema 的字典
    )
    remaining_after = max(limit - (used_today + chars), 0)

    # 5) 对客户端返回统一结构
    return DetectionResponse(
        detection_id=detection.id,
        label=label.lower(),
        score=normalized_score,
        model_name=model_name,
        raw_score=raw_score,
        threshold=threshold,
        currentCredits=remaining_after,
        history_id=detection.id,
    )


@detect_router.post(
    "/detect",
    response_model=DetectionResponse,
    summary="对文本进行检测（调用 RepreGuard 微服务）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect(
    payload: DetectionRequest,
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> DetectionResponse:
    """
    调用 RepreGuard 检测微服务，对文本进行 AI/HUMAN 分类，并保存检测记录。
    """
    return await _detect_impl(payload=payload, db=db, current_actor=current_actor)


@scan_router.post(
    "/detect",
    response_model=AnalysisResponse,
    summary="对文本进行检测（返回富结构分析结果）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def detect_scan(
    payload: DetectRequest,
    db: SessionDep,
    current_actor: CurrentActorDep,
) -> AnalysisResponse:
    # 保持旧接口兼容性，但建议前端迁移到 /v1/detect
    # 此接口目前主要返回 AnalysisResponse 给前端做即时渲染，
    # 如果前端改用 history 渲染，则此接口可能废弃。
    
    # 复用 impl 逻辑
    functions = set(payload.functions or [])
    functions.add("scan")
    
    detection_response = await _detect_impl(
        payload=DetectionRequest(text=payload.text, options=None, functions=list(functions)),
        db=db,
        current_actor=current_actor,
    )
    
    # 构造旧版 AnalysisResponse 返回
    # 注意：_detect_impl 内部已经保存了 history。
    # 这里我们重新构造 AnalysisResponse 以兼容旧前端（如果旧前端还没改）
    # 但由于 User 要求“Fix duplication”，前端应该改用 /detect 的返回 (history_id) 去 fetch history。
    
    return _build_analysis_response(
        text=payload.text,
        functions=functions,
        current_credits=detection_response.currentCredits,
    )


@router.post(
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

    service = DetectionService(db)
    records, total = service.list_detections(
        actor_type=current_actor.actor_type,
        actor_id=current_actor.actor_id,
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
    current_actor: CurrentActorDep,
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
        current_actor=current_actor,
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
    current_actor: CurrentActorDep,
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
        current_actor=current_actor,
        page=page,
        page_size=page_size,
        from_time=from_time,
        to_time=to_time,
    )
