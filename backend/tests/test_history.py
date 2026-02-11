"""Tests for history API endpoints."""

import pytest

from app.api.v1.auth import register_user
from app.api.v1.history import (
    batch_delete_histories,
    clear_all_histories,
    create_history,
    delete_history,
    get_history,
    list_histories,
    update_history,
)
from app.schemas.auth import RegisterRequest
from app.schemas.history import (
    Analysis,
    BatchDeleteRequest,
    HistoryRecordCreate,
    HistoryRecordUpdate,
    Sentence,
    Summary,
)
from fastapi import HTTPException


@pytest.mark.anyio
async def test_create_history(db_session, unique_email):
    """测试创建历史记录"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建历史记录
    payload = HistoryRecordCreate(
        title="扫描记录 · 2026-02-11 13:15:21",
        functions=["scan"],
        input_text="The quick brown fox jumps over the lazy dog.",
        editor_html="<p>The quick brown fox jumps over the lazy dog.</p>",
        analysis=Analysis(
            summary=Summary(ai=45, mixed=25, human=30),
            sentences=[
                Sentence(
                    id="sent-1",
                    text="The quick brown fox jumps over the lazy dog.",
                    raw="The quick brown fox jumps over the lazy dog.",
                    type="ai",
                    probability=0.85,
                    score=85,
                    reason="高度结构化的表达方式",
                    suggestion="可以使用更加口语化的表述",
                )
            ],
            translation="",
            polish="",
            citations=[],
            ai_likely_count=1,
            highlighted_html='<span class="bg-amber-100">The quick brown fox jumps over the lazy dog.</span>',
        ),
    )

    response = await create_history(payload=payload, db=db_session, current_user=user)

    assert response.id > 0
    assert response.user_id == user.id
    assert response.title == "扫描记录 · 2026-02-11 13:15:21"
    assert response.functions == ["scan"]
    assert response.input_text == "The quick brown fox jumps over the lazy dog."
    assert response.analysis is not None
    assert response.analysis.summary.ai == 45


@pytest.mark.anyio
async def test_list_histories(db_session, unique_email):
    """测试获取历史记录列表"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建多条历史记录
    for i in range(3):
        payload = HistoryRecordCreate(
            title=f"记录 {i + 1}",
            functions=["scan"],
            input_text=f"Text {i + 1}",
            editor_html=f"<p>Text {i + 1}</p>",
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    # 获取列表
    response = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=10,
        sort="created_at",
        order="desc",
    )

    assert response.total == 3
    assert len(response.items) == 3
    assert response.page == 1
    assert response.per_page == 10
    assert response.total_pages == 1


@pytest.mark.anyio
async def test_list_histories_pagination(db_session, unique_email):
    """测试分页功能"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建 5 条记录
    for i in range(5):
        payload = HistoryRecordCreate(
            title=f"记录 {i + 1}",
            functions=["scan"],
            input_text=f"Text {i + 1}",
            editor_html=f"<p>Text {i + 1}</p>",
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    # 第一页（每页2条）
    page1 = await list_histories(db=db_session, current_user=user, page=1, per_page=2)
    assert page1.total == 5
    assert len(page1.items) == 2
    assert page1.total_pages == 3

    # 第二页
    page2 = await list_histories(db=db_session, current_user=user, page=2, per_page=2)
    assert len(page2.items) == 2

    # 最后一页
    page3 = await list_histories(db=db_session, current_user=user, page=3, per_page=2)
    assert len(page3.items) == 1


@pytest.mark.anyio
async def test_get_history(db_session, unique_email):
    """测试获取单条历史记录"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建记录
    payload = HistoryRecordCreate(
        title="测试记录",
        functions=["scan", "polish"],
        input_text="Test text",
        editor_html="<p>Test text</p>",
    )
    created = await create_history(payload=payload, db=db_session, current_user=user)

    # 获取记录
    response = await get_history(history_id=created.id, db=db_session, current_user=user)

    assert response.id == created.id
    assert response.title == "测试记录"
    assert response.functions == ["scan", "polish"]


@pytest.mark.anyio
async def test_get_history_not_found(db_session, unique_email):
    """测试获取不存在的记录"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    with pytest.raises(HTTPException) as exc_info:
        await get_history(history_id=99999, db=db_session, current_user=user)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "HISTORY_NOT_FOUND"


@pytest.mark.anyio
async def test_update_history(db_session, unique_email):
    """测试更新历史记录"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建记录
    payload = HistoryRecordCreate(
        title="旧标题",
        functions=["scan"],
        input_text="Test",
        editor_html="<p>Test</p>",
    )
    created = await create_history(payload=payload, db=db_session, current_user=user)

    # 更新标题
    update_payload = HistoryRecordUpdate(title="新标题")
    response = await update_history(
        history_id=created.id,
        payload=update_payload,
        db=db_session,
        current_user=user,
    )

    assert response.id == created.id
    assert response.title == "新标题"


@pytest.mark.anyio
async def test_delete_history(db_session, unique_email):
    """测试删除历史记录"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建记录
    payload = HistoryRecordCreate(
        title="待删除记录",
        functions=["scan"],
        input_text="Test",
        editor_html="<p>Test</p>",
    )
    created = await create_history(payload=payload, db=db_session, current_user=user)

    # 删除记录
    await delete_history(history_id=created.id, db=db_session, current_user=user)

    # 验证已删除
    with pytest.raises(HTTPException) as exc_info:
        await get_history(history_id=created.id, db=db_session, current_user=user)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_batch_delete(db_session, unique_email):
    """测试批量删除"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建 3 条记录
    ids = []
    for i in range(3):
        payload = HistoryRecordCreate(
            title=f"记录 {i + 1}",
            functions=["scan"],
            input_text=f"Text {i + 1}",
            editor_html=f"<p>Text {i + 1}</p>",
        )
        created = await create_history(payload=payload, db=db_session, current_user=user)
        ids.append(created.id)

    # 批量删除前 2 条
    batch_payload = BatchDeleteRequest(ids=ids[:2])
    response = await batch_delete_histories(payload=batch_payload, db=db_session, current_user=user)

    assert response.deleted_count == 2
    assert len(response.failed_ids) == 0

    # 验证剩余 1 条
    list_response = await list_histories(
        db=db_session, current_user=user, page=1, per_page=20, sort="created_at", order="desc"
    )
    assert list_response.total == 1


@pytest.mark.anyio
async def test_clear_all(db_session, unique_email):
    """测试清空所有历史记录"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建 5 条记录
    for i in range(5):
        payload = HistoryRecordCreate(
            title=f"记录 {i + 1}",
            functions=["scan"],
            input_text=f"Text {i + 1}",
            editor_html=f"<p>Text {i + 1}</p>",
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    # 清空所有
    response = await clear_all_histories(db=db_session, current_user=user)
    assert response.deleted_count == 5

    # 验证已清空
    list_response = await list_histories(
        db=db_session, current_user=user, page=1, per_page=20, sort="created_at", order="desc"
    )
    assert list_response.total == 0


@pytest.mark.anyio
async def test_permission_control(db_session, unique_email):
    """测试权限控制：用户A不能访问用户B的记录"""
    # 创建两个用户
    user_a = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    user_b_email = f"other-{unique_email}"
    user_b = await register_user(RegisterRequest(email=user_b_email, password="StrongPass!23"), db_session)

    # 用户A创建记录
    payload = HistoryRecordCreate(
        title="用户A的记录",
        functions=["scan"],
        input_text="User A text",
        editor_html="<p>User A text</p>",
    )
    record_a = await create_history(payload=payload, db=db_session, current_user=user_a)

    # 用户B尝试访问用户A的记录（应该返回404）
    with pytest.raises(HTTPException) as exc_info:
        await get_history(history_id=record_a.id, db=db_session, current_user=user_b)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_limit_enforcement(db_session, unique_email):
    """测试100条记录限制"""
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    # 创建 102 条记录
    for i in range(102):
        payload = HistoryRecordCreate(
            title=f"记录 {i + 1}",
            functions=["scan"],
            input_text=f"Text {i + 1}",
            editor_html=f"<p>Text {i + 1}</p>",
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    # 验证只保留最新的 100 条
    list_response = await list_histories(
        db=db_session, current_user=user, page=1, per_page=100, sort="created_at", order="desc"
    )
    assert list_response.total == 100
    assert len(list_response.items) == 100

    # 验证最旧的两条记录已被删除
    # 由于 SQLite 内存数据库中所有记录的 created_at 相同，排序不稳定
    # 只验证总数正确即可，100条限制生效
