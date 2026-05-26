from math import exp

import pytest

from app.api.v1.auth import register_user
from fastapi import HTTPException

from app.api.v1.detections import _merge_short_paragraphs, _split_paragraphs, detect, detect_scan, list_detections
from app.api.v1.keys import create_api_key
from app.db.deps import ActorContext, get_current_actor
from app.schemas.analysis import DetectRequest
from app.schemas.api_key import APIKeyCreateRequest
from app.schemas.auth import RegisterRequest
from app.schemas.detection import DetectionRequest
from app.services.repre_guard_client import RepreGuardError, repre_guard_client

LONG_TEXT = (
    "This is a sufficiently long detection sample that keeps repeating structured content "
    "to satisfy the minimum length requirement for the scan endpoint. "
    "It contains enough visible characters to pass validation and exercise the downstream "
    "detection flow without relying on tiny placeholder inputs. "
) * 3
LONG_TEXT = LONG_TEXT.strip()

LONG_PARAGRAPH_A = (
    "Paragraph A keeps expanding with consistent academic wording so the detector has enough "
    "content to evaluate this block independently and return a stable probability score. "
) * 3
LONG_PARAGRAPH_A = LONG_PARAGRAPH_A.strip()

LONG_PARAGRAPH_B = (
    "Paragraph B also exceeds the minimum size and is intentionally different so tests can "
    "verify weighted aggregation and merged block behavior with deterministic model outputs. "
) * 3
LONG_PARAGRAPH_B = LONG_PARAGRAPH_B.strip()

ROBERTA_MODEL_NAME = "openai-community/roberta-base-openai-detector"
ROBERTA_THRESHOLD = 0.0

@pytest.fixture(autouse=True)
def mock_repre_guard(monkeypatch):
    """
    在测试中自动 mock 掉 RepreGuard 微服务调用，
    避免真实发 HTTP 请求导致 [Errno -2] Name or service not known。
    """

    async def fake_detect(text: str) -> dict:
        # 这里返回一个“假的 RepreGuard 检测结果”
        # 字段结构要和你真实微服务保持一致
        return {
            "score": 0.4054651081081642,
            "threshold": ROBERTA_THRESHOLD,
            "label": "AI",                      # 或 "HUMAN"
            "model_name": ROBERTA_MODEL_NAME,
            "score_type": "raw_logit",
        }

    # 把单例实例上的 detect 方法替换掉
    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    yield

@pytest.mark.anyio
async def test_detect_with_user(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    response = await detect(
        payload=DetectionRequest(
            text=LONG_TEXT,
            options={
                "language": "en",
                "api_key": "secret",
                "provider": {"api_key": "nested-secret", "token": "nested-token"},
            },
            editor_html="<p>Sample detect text</p>",
        ),
        db=db_session,
        current_actor=actor,
    )
    assert response.detection_id > 0
    assert response.input_text == LONG_TEXT
    assert response.label in {"human", "ai"}
    assert 0 <= response.score <= 1
    assert response.result is not None
    assert len(response.result.sentences) > 0

    listed = await list_detections(
        db=db_session,
        current_actor=actor,
        page=1,
        page_size=10,
        from_time=None,
        to_time=None,
    )
    assert listed.total == 1
    assert listed.items[0].meta_json["options"]["api_key"] == "***"
    assert listed.items[0].meta_json["options"]["provider"]["api_key"] == "***"
    assert listed.items[0].meta_json["options"]["provider"]["token"] == "***"


@pytest.mark.anyio
async def test_detect_with_guest(db_session):
    actor = ActorContext(actor_type="guest", actor_id="guest-test")
    detection = await detect(payload=DetectionRequest(text=LONG_TEXT), db=db_session, current_actor=actor)
    assert detection.detection_id > 0
    assert detection.input_text == LONG_TEXT
    assert detection.label in {"human", "ai"}
    assert 0 <= detection.score <= 1
    assert detection.result is not None


@pytest.mark.anyio
async def test_detect_with_api_key_actor(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    api_key_resp = await create_api_key(
        payload=APIKeyCreateRequest(name="Detect Key"),
        db=db_session,
        current_user=user,
    )

    actor = get_current_actor(db=db_session, token=None, api_key_header=api_key_resp.key)
    detection = await detect(payload=DetectionRequest(text=LONG_TEXT), db=db_session, current_actor=actor)
    assert detection.detection_id > 0
    assert detection.label in {"human", "ai"}


@pytest.mark.anyio
async def test_invalid_bearer_does_not_fallback_to_api_key(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    api_key_resp = await create_api_key(
        payload=APIKeyCreateRequest(name="Detect Key"),
        db=db_session,
        current_user=user,
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_actor(db=db_session, token="invalid-token", api_key_header=api_key_resp.key)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_detect_saves_history(db_session, unique_email):
    """
    Validation Rule:
    1. /detect saves a complete record (check meta_json["analysis"]).
    2. Response includes history_id.
    """
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    # Call /detect
    response = await detect(
        payload=DetectionRequest(
            text=f"{LONG_PARAGRAPH_A}\n{LONG_PARAGRAPH_B}",
            functions=["scan", "polish"],
            editor_html="<h2>Section</h2><p>Paragraph A</p><p>Paragraph B</p>",
        ),
        db=db_session,
        current_actor=actor,
    )

    # Check response (Standard 1 & 2)
    assert response.history_id is not None
    assert response.history_id > 0
    assert response.detection_id == response.history_id
    assert response.input_text == f"{LONG_PARAGRAPH_A}\n{LONG_PARAGRAPH_B}"
    assert response.result is not None
    assert response.result.polish == ""
    assert response.result.translation == ""
    assert response.result.citations == []

    # Check DB structure
    from app.services.detection_service import DetectionService
    service = DetectionService(db_session)
    record = list(service.list_detections(
        actor_type="user", 
        actor_id=str(user.id), 
        page=1, 
        page_size=1, 
        from_time=None, 
        to_time=None
    )[0])[0]

    # Check flat analysis structure (Standard 2)
    # Expected: record.meta_json = { "options": ..., "analysis": { "summary": ..., "sentences": ... }, ... }
    assert "analysis" in record.meta_json
    analysis = record.meta_json["analysis"]
    assert "summary" in analysis
    assert "sentences" in analysis
    assert len(analysis["sentences"]) == 2
    assert analysis["sentences"][0]["text"] == LONG_PARAGRAPH_A
    assert analysis["sentences"][1]["text"] == LONG_PARAGRAPH_B
    assert analysis["sentences"][0]["start_paragraph"] == 1
    assert analysis["sentences"][0]["end_paragraph"] == 1
    assert analysis["sentences"][1]["start_paragraph"] == 2
    assert analysis["sentences"][1]["end_paragraph"] == 2
    assert record.editor_html == "<h2>Section</h2><p>Paragraph A</p><p>Paragraph B</p>"
    
    # Check NO double nesting
    assert "analysis" not in analysis


@pytest.mark.anyio
async def test_detect_scan_compat_returns_real_scan_only_payload(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    response = await detect_scan(
        payload=DetectRequest(
            text=f"{LONG_PARAGRAPH_A}\n{LONG_PARAGRAPH_B}",
            functions=["scan", "polish", "translation", "citations"],
        ),
        db=db_session,
        current_actor=actor,
    )

    assert response.currentCredits >= 0
    assert response.summary.startswith("AI ")
    assert len(response.sentences) == 2
    assert response.sentences[0].text == LONG_PARAGRAPH_A
    assert response.sentences[0].is_ai is True
    assert response.polish is None
    assert response.translation is None
    assert response.citations is None


@pytest.mark.anyio
async def test_detect_uses_paragraph_level_weighted_average(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    async def fake_detect(text: str) -> dict:
        if text == LONG_PARAGRAPH_A:
            return {
                "score": -1.3862943611198906,
                "threshold": ROBERTA_THRESHOLD,
                "label": "HUMAN",
                "model_name": ROBERTA_MODEL_NAME,
                "score_type": "raw_logit",
            }
        return {
            "score": 0.4054651081081642,
            "threshold": ROBERTA_THRESHOLD,
            "label": "AI",
            "model_name": ROBERTA_MODEL_NAME,
            "score_type": "raw_logit",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    text = f"{LONG_PARAGRAPH_A}\n{LONG_PARAGRAPH_B}"
    response = await detect(payload=DetectionRequest(text=text), db=db_session, current_actor=actor)

    short_probability = 1.0 / (1.0 + exp(-(-1.3862943611198906 - ROBERTA_THRESHOLD)))
    long_probability = 1.0 / (1.0 + exp(-(0.4054651081081642 - ROBERTA_THRESHOLD)))
    expected_probability = (
        short_probability * len("".join(LONG_PARAGRAPH_A.split()))
        + long_probability * len("".join(LONG_PARAGRAPH_B.split()))
    ) / (len("".join(LONG_PARAGRAPH_A.split())) + len("".join(LONG_PARAGRAPH_B.split())))

    assert response.label == "human"
    assert response.score == pytest.approx(expected_probability)
    assert len(response.result.sentences) == 2
    assert response.result.sentences[0].text == LONG_PARAGRAPH_A
    assert response.result.sentences[1].text == LONG_PARAGRAPH_B
    assert response.result.sentences[0].start_paragraph == 1
    assert response.result.sentences[0].end_paragraph == 1
    assert response.result.sentences[1].start_paragraph == 2
    assert response.result.sentences[1].end_paragraph == 2
    expected_ai_summary = round(
        (len("".join(LONG_PARAGRAPH_B.split())))
        / (len("".join(LONG_PARAGRAPH_A.split())) + len("".join(LONG_PARAGRAPH_B.split())))
        * 100
    )
    assert response.result.sentences[0].type == "human"
    assert response.result.sentences[1].type == "ai"
    assert response.result.summary.ai == expected_ai_summary


@pytest.mark.anyio
async def test_detect_rejects_text_under_minimum_visible_chars(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    with pytest.raises(HTTPException) as exc_info:
        await detect(payload=DetectionRequest(text="too short"), db=db_session, current_actor=actor)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "TEXT_TOO_SHORT"
    assert exc_info.value.detail["detail"]["minimum"] == 200


@pytest.mark.anyio
async def test_detect_merges_short_paragraph_into_next_chunk(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)
    calls = []

    async def fake_detect(text: str) -> dict:
        calls.append(text)
        return {
            "score": 0.4054651081081642,
            "threshold": ROBERTA_THRESHOLD,
            "label": "AI",
            "model_name": ROBERTA_MODEL_NAME,
            "score_type": "raw_logit",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    text = f"Short intro\n{LONG_PARAGRAPH_A}"
    response = await detect(payload=DetectionRequest(text=text), db=db_session, current_actor=actor)

    assert calls == [text]
    assert len(response.result.sentences) == 1
    assert response.result.sentences[0].text == text
    assert response.result.sentences[0].start_paragraph == 1
    assert response.result.sentences[0].end_paragraph == 2


def test_segmenting_preserves_indentation_and_sentence_spacing():
    text = 'def run():\n    value = {"path": "C:\\\\tmp\\\\file.txt"}\n    return value\nEnglish sentence one. English sentence two.'

    paragraphs = _split_paragraphs(text)
    merged = _merge_short_paragraphs(paragraphs, min_chars=200, max_chars=1500)

    assert paragraphs[1].startswith("    value")
    assert merged[0]["text"] == text
    assert "one. English" in str(merged[0]["text"])


@pytest.mark.anyio
async def test_detect_retries_downstream_input_too_long(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)
    calls: list[str] = []
    successful_calls: list[str] = []

    async def fake_detect(text: str) -> dict:
        calls.append(text)
        visible_chars = len("".join(text.split()))
        if visible_chars > 120:
            raise RepreGuardError(
                "Input exceeds the model limit of 512 tokens.",
                status_code=422,
                code="INPUT_TOO_LONG",
                detail={"current_tokens": 513, "max_tokens": 512},
            )

        successful_calls.append(text)
        return {
            "score": 0.4054651081081642,
            "threshold": ROBERTA_THRESHOLD,
            "label": "AI",
            "model_name": ROBERTA_MODEL_NAME,
            "score_type": "raw_logit",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    text = (
        "This compact paragraph intentionally repeats content so the downstream service rejects "
        "the initial segment and the backend has to split it into smaller retry chunks. "
    ) * 4
    text = text.strip()

    response = await detect(payload=DetectionRequest(text=text), db=db_session, current_actor=actor)

    assert response.detection_id > 0
    assert response.result is not None
    assert len(calls) > len(successful_calls)
    assert len(successful_calls) > 1
    assert all(len("".join(call.split())) <= 120 for call in successful_calls)


@pytest.mark.anyio
async def test_detect_supports_probability_score_type(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    async def fake_detect(text: str) -> dict:
        return {
            "score": 0.003,
            "threshold": 0.0028,
            "label": "AI",
            "model_name": ROBERTA_MODEL_NAME,
            "score_type": "probability",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    response = await detect(payload=DetectionRequest(text=LONG_TEXT), db=db_session, current_actor=actor)

    assert response.label == "ai"
    assert response.score == pytest.approx(0.003)
    assert response.raw_score == pytest.approx(0.003)
    assert response.threshold == pytest.approx(0.0028)
    assert response.result is not None
    assert response.result.summary.ai == 100
    assert response.result.sentences[0].type == "ai"
    assert response.result.sentences[0].probability >= 0.67

