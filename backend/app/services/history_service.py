"""History service for managing history records based on Detection model."""

from __future__ import annotations

from math import ceil
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.detection import Detection


class HistoryService:
    """历史记录服务，基于 Detection 模型"""
    
    MAX_HISTORY_RECORDS = 100  # 每个用户最多保存 100 条历史记录

    def __init__(self, db: Session):
        self.db = db

    def create_history(
        self,
        user_id: int,
        title: str,
        functions: list[str],
        input_text: str,
        editor_html: str,
        analysis: dict[str, Any] | None = None,
    ) -> Detection:
        """创建历史记录，自动处理 100 条限制"""
        # 先执行 100 条限制检查
        self._enforce_limit(user_id)

        # 创建历史记录（存储到 detections 表）
        # 将 analysis 数据存储到 meta_json 中
        meta_json = {}
        if analysis:
            meta_json["analysis"] = analysis

        detection = Detection(
            user_id=user_id,
            actor_type="user",
            actor_id=str(user_id),
            chars_used=len(input_text),
            title=title,
            input_text=input_text,
            editor_html=editor_html,
            functions_used=functions,
            result_label="human",  # 历史记录默认标签，可根据 analysis 调整
            score=0.0,  # 历史记录默认分数，可根据 analysis 调整
            meta_json=meta_json,
        )

        self.db.add(detection)
        self.db.commit()
        self.db.refresh(detection)
        return detection

    def get_history(self, user_id: int, history_id: int) -> Detection | None:
        """获取单条历史记录（验证权限）"""
        stmt = select(Detection).where(
            Detection.id == history_id,
            Detection.user_id == user_id,
        )
        return self.db.scalar(stmt)

    def list_histories(
        self,
        user_id: int,
        page: int = 1,
        per_page: int = 20,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Detection], int, int]:
        """分页列表查询历史记录
        
        Returns:
            tuple: (records, total, total_pages)
        """
        # 限制每页数量
        per_page = min(per_page, 100)
        
        # 构建查询
        query = select(Detection).where(Detection.user_id == user_id)

        # 排序
        if sort == "created_at":
            order_by = Detection.created_at.desc() if order == "desc" else Detection.created_at.asc()
        else:
            order_by = Detection.created_at.desc()
        
        query = query.order_by(order_by)

        # 计算总数
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        total_pages = ceil(total / per_page) if per_page > 0 else 0

        # 分页
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)

        records = list(self.db.scalars(query).all())
        return records, total, total_pages

    def update_history(
        self,
        user_id: int,
        history_id: int,
        title: str,
    ) -> Detection | None:
        """更新历史记录（仅允许更新 title）"""
        detection = self.get_history(user_id, history_id)
        if not detection:
            return None

        detection.title = title
        self.db.commit()
        self.db.refresh(detection)
        return detection

    def delete_history(self, user_id: int, history_id: int) -> bool:
        """删除单条历史记录"""
        detection = self.get_history(user_id, history_id)
        if not detection:
            return False

        self.db.delete(detection)
        self.db.commit()
        return True

    def batch_delete_histories(
        self,
        user_id: int,
        ids: list[int],
    ) -> tuple[int, list[int]]:
        """批量删除历史记录
        
        Returns:
            tuple: (deleted_count, failed_ids)
        """
        deleted_count = 0
        failed_ids = []

        for history_id in ids:
            success = self.delete_history(user_id, history_id)
            if success:
                deleted_count += 1
            else:
                failed_ids.append(history_id)

        return deleted_count, failed_ids

    def clear_all_histories(self, user_id: int) -> int:
        """清空用户所有历史记录"""
        stmt = delete(Detection).where(Detection.user_id == user_id)
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount or 0

    def _enforce_limit(self, user_id: int) -> None:
        """强制执行 100 条限制（删除最旧的记录）"""
        # 查询当前用户的历史记录总数
        count_stmt = select(func.count()).select_from(Detection).where(Detection.user_id == user_id)
        total = self.db.scalar(count_stmt) or 0

        if total >= self.MAX_HISTORY_RECORDS:
            # 需要删除的数量
            to_delete = total - self.MAX_HISTORY_RECORDS + 1

            # 查询最旧的记录 IDs
            oldest_stmt = (
                select(Detection.id)
                .where(Detection.user_id == user_id)
                .order_by(Detection.created_at.asc())
                .limit(to_delete)
            )
            oldest_ids = list(self.db.scalars(oldest_stmt).all())

            if oldest_ids:
                # 批量删除
                delete_stmt = delete(Detection).where(Detection.id.in_(oldest_ids))
                self.db.execute(delete_stmt)
                self.db.commit()
