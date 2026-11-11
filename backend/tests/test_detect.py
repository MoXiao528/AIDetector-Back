from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.auth import register_user
from app.api.v1.detections import (
    _combine_repre_guard_results,
    _split_paragraphs,
    detect,
    detect_scan,
    list_detections,
)
from app.api.v1.keys import create_api_key
from app.db.deps import ActorContext, get_current_actor
from app.models.detection import Detection
from app.models.guest_session import GuestSession
from app.schemas.analysis import DetectRequest
from app.schemas.api_key import APIKeyCreateRequest
from app.schemas.auth import RegisterRequest
from app.schemas.detection import DetectionRequest
from app.services.repre_guard_client import RepreGuardError, repre_guard_client
from app.services.token_chunker import build_token_aware_segments, split_sentence_like_chunks

LONG_TEXT = (
    "This is a sufficiently long detection sample that keeps repeating structured content "
    "to satisfy the minimum length requirement for the scan endpoint. "
    "It contains enough visible characters to pass validation and exercise the downstream "
    "detection flow without relying on tiny placeholder inputs. "
) * 7
LONG_TEXT = LONG_TEXT.strip()

LONG_PARAGRAPH_A = (
    "Paragraph A keeps expanding with consistent academic wording so the detector has enough "
    "content to evaluate this block independently and return a stable probability score. "
) * 6
LONG_PARAGRAPH_A = LONG_PARAGRAPH_A.strip()

LONG_PARAGRAPH_B = (
    "Paragraph B also exceeds the minimum size and is intentionally different so tests can "
    "verify weighted aggregation and merged block behavior with deterministic model outputs. "
) * 6
LONG_PARAGRAPH_B = LONG_PARAGRAPH_B.strip()

WRAPPED_CHINESE_LINES = [
    "我与父亲不相见已二年余了，我最不能忘记的是他的背影。那年冬天，祖",
    "母死了，父亲的差使也交卸了，正是祸不单行的日子，我从北京到徐州，",
    "打算跟着父亲奔丧回家。到徐州见着父亲，看见满院狼藉的东西，又想起",
    "祖母，不禁簌簌地流下眼泪。父亲说，事已如此，不必难过，好在天无",
    "绝人之路！",
    "回家变卖典质，父亲还了亏空；又借钱办了丧事。这些日子，家中光",
    "景很是惨淡，一半为了丧事，一半为了父亲赋闲。丧事完毕，父亲要到南",
    "京谋事，我也要回北京念书，我们便同行。",
] * 2
WRAPPED_CHINESE_TEXT = "\n".join(WRAPPED_CHINESE_LINES)

ROBERTA_MODEL_NAME = "openai-community/roberta-base-openai-detector"
ROBERTA_THRESHOLD = 0.0


class FakeTokenizer:
    @staticmethod
    def num_special_tokens_to_add(pair: bool = False) -> int:
        return 2

    @staticmethod
    def _pieces(text: str) -> list[str]:
        return [piece for piece in str(text or "").replace("\n", " ").split(" ") if piece]

    def encode(
        self,
        text: str,
        *,
        add_special_tokens: bool = True,
        truncation: bool = False,
        max_length: int | None = None,
    ) -> list[int]:
        body = list(range(len(self._pieces(text))))
        if truncation and max_length is not None:
            special_count = 2 if add_special_tokens else 0
            body = body[: max(max_length - special_count, 0)]
        if not add_special_tokens:
            return body
        return [-1, *body, -2]


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
    monkeypatch.setattr("app.services.token_chunker.get_tokenizer", lambda model_name=ROBERTA_MODEL_NAME: FakeTokenizer())
    yield


async def _detect_once(*, payload, db, current_actor):
    return await detect(
        payload=payload,
        db=db,
        current_actor=current_actor,
        idempotency_key=uuid4(),
    )


async def _detect_scan_once(*, payload, db, current_actor):
    return await detect_scan(
        payload=payload,
        db=db,
        current_actor=current_actor,
        idempotency_key=uuid4(),
    )

@pytest.mark.anyio
async def test_detect_with_user(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    response = await _detect_once(
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
    assert listed.items[0].meta_json["options"]["repre_guard"]["provider_model_name"] == ROBERTA_MODEL_NAME
    assert listed.items[0].meta_json["options"]["repre_guard"]["raw_score"] == pytest.approx(0.4054651081081642)


@pytest.mark.anyio
async def test_detect_with_guest(db_session):
    db_session.add(
        GuestSession(
            id="guest-test",
            refresh_token_hash="a" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.flush()
    actor = ActorContext(actor_type="guest", actor_id="guest-test")
    detection = await _detect_once(payload=DetectionRequest(text=LONG_TEXT), db=db_session, current_actor=actor)
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
    detection = await _detect_once(payload=DetectionRequest(text=LONG_TEXT), db=db_session, current_actor=actor)
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
    response = await _detect_once(
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

    response = await _detect_scan_once(
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
async def test_detect_flushes_after_reaching_target_and_projects_block_scores(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    opening = "Opening " + "alpha " * 118 + "ends."
    first = "First " + "beta " * 131 + "ends."
    crossing = "Crossing " + "gamma " * 18 + "ends."
    last = "Last " + "delta " * 236 + "ends."
    tail = "Tiny tail."
    body = f"{first} {crossing} {last}"
    first_block = f"{opening}\n{first} {crossing}"
    final_block = f"{last}\n{tail}"
    calls = []

    async def fake_detect(text: str) -> dict:
        calls.append(text)
        score = {first_block: 0.4, final_block: 0.95}[text]
        return {
            "score": score,
            "threshold": 0.5,
            "label": "AI" if score >= 0.5 else "HUMAN",
            "model_name": ROBERTA_MODEL_NAME,
            "score_type": "probability",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    text = f"{opening}\n{body}\n{tail}"
    response = await _detect_once(payload=DetectionRequest(text=text), db=db_session, current_actor=actor)

    # The first two sentences total 255 input tokens. The next sentence takes the block to 275.
    assert calls == [first_block, final_block]
    assert response.label == "ai"
    assert response.score == pytest.approx((273 * 0.4 + 240 * 0.95) / 513)
    assert response.result.summary.ai == 47
    assert response.result.summary.human == 53
    assert [(item.start_paragraph, item.end_paragraph, item.raw, item.type) for item in response.result.sentences] == [
        (1, 1, opening, "human"),
        (2, 2, body, "ai"),
        (3, 3, tail, "ai"),
    ]
    body_score = (153 * 0.4 + 238 * 0.95) / 391
    assert response.result.sentences[1].probability == pytest.approx(0.67 + (body_score - 0.5) / 0.5 * 0.33)


@pytest.mark.anyio
async def test_detect_merges_short_tail_before_single_inference(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)
    first = " ".join(f"word{index}" for index in range(254)) + "."
    tail = "Tail remaining sentence."
    combined_text = f"{first}\n{tail}"
    calls = []

    async def fake_detect(text: str) -> dict:
        calls.append(text)
        assert text == combined_text
        return {
            "score": 0.23,
            "threshold": 0.5,
            "label": "HUMAN",
            "model_name": ROBERTA_MODEL_NAME,
            "score_type": "probability",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    response = await _detect_once(payload=DetectionRequest(text=combined_text), db=db_session, current_actor=actor)

    assert calls == [combined_text]
    assert response.score == pytest.approx(0.23)
    assert response.raw_score == pytest.approx(0.23)
    assert response.result.summary.ai == 0
    assert [(item.start_paragraph, item.end_paragraph, item.raw) for item in response.result.sentences] == [
        (1, 1, first),
        (2, 2, tail),
    ]
    stored = db_session.get(Detection, response.detection_id).meta_json["options"]["repre_guard"]["segments"]
    assert len(stored) == 1
    assert stored[0]["token_count"] == 259
    assert stored[0]["raw_score"] == pytest.approx(0.23)


@pytest.mark.anyio
async def test_detect_rejects_text_under_minimum_tokens(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    with pytest.raises(HTTPException) as exc_info:
        await _detect_once(payload=DetectionRequest(text="too short"), db=db_session, current_actor=actor)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "TEXT_TOO_SHORT"
    assert exc_info.value.detail["detail"]["minimumTokens"] == 256


@pytest.mark.anyio
async def test_detect_merges_line_wrapped_chinese_text_for_detection(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)
    calls = []

    class CharacterTokenizer(FakeTokenizer):
        @staticmethod
        def _pieces(text: str) -> list[str]:
            return [character for character in text if not character.isspace()]

    monkeypatch.setattr("app.services.token_chunker.get_tokenizer", lambda model_name=None: CharacterTokenizer())

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

    response = await _detect_once(payload=DetectionRequest(text=WRAPPED_CHINESE_TEXT), db=db_session, current_actor=actor)

    assert calls == [WRAPPED_CHINESE_TEXT]
    assert [(item.start_paragraph, item.end_paragraph, item.raw, item.type) for item in response.result.sentences] == [
        (index, index, line, "ai") for index, line in enumerate(WRAPPED_CHINESE_LINES, start=1)
    ]
    assert response.result.highlighted_html.count("<p>") == len(WRAPPED_CHINESE_LINES)


def test_segmenting_preserves_indentation_and_sentence_spacing():
    text = 'def run():\n    value = {"path": "C:\\\\tmp\\\\file.txt"}\n    return value\nEnglish sentence one. English sentence two.'

    paragraphs = _split_paragraphs(text)
    segments = build_token_aware_segments(paragraphs, tokenizer_model=ROBERTA_MODEL_NAME)

    assert paragraphs[1].startswith("    value")
    assert len(segments) == 1
    assert segments[0]["text"] == text


def test_split_chinese_sentences_without_spaces():
    assert split_sentence_like_chunks("甲。乙！丙？末尾") == ["甲。", "乙！", "丙？", "末尾"]


@pytest.mark.parametrize("body_tokens", [254, 400])
def test_token_aware_segments_merge_short_tail_with_an_entire_sentence(body_tokens):
    first = " ".join(f"word{i}" for i in range(body_tokens)) + "."
    tail = "Short tail."
    segments = build_token_aware_segments([f"{first} {tail}"], tokenizer_model=ROBERTA_MODEL_NAME)

    assert [segment["detect_text"] for segment in segments] == [f"{first} {tail}"]
    assert [segment["token_count"] for segment in segments] == [body_tokens + 4]


def test_token_aware_segments_split_single_oversized_sentence_without_text_loss():
    oversized_sentence = " ".join(f"word{i}" for i in range(600)) + "."
    segments = build_token_aware_segments([oversized_sentence], tokenizer_model=ROBERTA_MODEL_NAME)

    assert len(segments) > 1
    assert all(segment["status"] == "detectable" and not segment["truncated"] for segment in segments)
    assert all(0 < int(segment["token_count"]) <= 512 for segment in segments)
    assert "".join(str(segment["text"]) for segment in segments) == oversized_sentence
    assert " ".join(str(segment["detect_text"]) for segment in segments).split() == oversized_sentence.split()


def test_combine_repre_guard_raw_logit_preserves_score_scale():
    result = _combine_repre_guard_results(
        ["short text", "longer text segment"],
        [
            {
                "score": -1.0,
                "threshold": 0.25,
                "label": "HUMAN",
                "model_name": ROBERTA_MODEL_NAME,
                "score_type": "raw_logit",
            },
            {
                "score": 1.0,
                "threshold": 0.25,
                "label": "AI",
                "model_name": ROBERTA_MODEL_NAME,
                "score_type": "raw_logit",
            },
        ],
    )

    assert result["score_type"] == "raw_logit"
    assert result["threshold"] == pytest.approx(0.25)
    assert result["score"] == pytest.approx((-1.0 * 2 + 1.0 * 3) / 5)
    assert result["label"] == "HUMAN"


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
    ) * 11
    text = text.strip()

    response = await _detect_once(payload=DetectionRequest(text=text), db=db_session, current_actor=actor)

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

    response = await _detect_once(payload=DetectionRequest(text=LONG_TEXT), db=db_session, current_actor=actor)

    assert response.label == "ai"
    assert response.score == pytest.approx(0.003)
    assert response.raw_score == pytest.approx(0.003)
    assert response.threshold == pytest.approx(0.0028)
    assert response.result is not None
    assert response.result.summary.ai == 100
    assert response.result.sentences[0].type == "ai"
    assert response.result.sentences[0].probability >= 0.67

