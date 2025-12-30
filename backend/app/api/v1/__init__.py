"""
API v1 路由聚合入口。

如果你希望在 main.py 里只挂一个 v1 总路由，
可以在这里把各个子 router 汇总到一个 APIRouter 上：
    from app.api.v1 import router as api_v1_router
    app.include_router(api_v1_router, prefix="")
"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.db import router as db_router
from app.api.v1.health import router as health_router
from app.api.v1.keys import router as api_keys_router
from app.api.v1.detections import router as detections_router
from app.api.v1.admin import router as admin_router
from app.api.v1.teams import router as teams_router

router = APIRouter()

# 目前各模块的路径本身已经是完整的（例如 /auth/login, /detect, /detections 等），
# 因此这里 prefix 统一设为 "" 即可。
router.include_router(health_router, prefix="")
router.include_router(db_router, prefix="")
router.include_router(auth_router, prefix="")
router.include_router(api_keys_router, prefix="")
router.include_router(detections_router, prefix="")
router.include_router(admin_router, prefix="")
router.include_router(teams_router, prefix="")

__all__ = ["router"]
