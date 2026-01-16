"""
API 总路由聚合入口。

当前只有 v1，可以这样使用：
    from app.api import router as api_router
    app.include_router(api_router, prefix="")

将来如果有 v2，也可以在这里统一调度不同版本：
    router.include_router(v2_router, prefix="/v2")
"""

from fastapi import APIRouter

from app.api.v1 import api_router as v1_router

router = APIRouter()

# 现在不做显式版本前缀，保持现有路径不变（/auth, /detect 等）
# 如果未来要显式版本号，可以改成 prefix="/v1"
router.include_router(v1_router, prefix="")

__all__ = ["router"]
