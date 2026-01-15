from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.db import router as db_router
from app.api.v1.health import router as health_router
from app.api.v1.keys import router as api_keys_router
from app.api.v1.detections import router as detection_router, scan_router as scan_router
from app.api.v1.admin import router as admin_router
from app.api.v1.teams import router as teams_router
# 如果你未来想用聚合 router，可以改成：
# from app.api import router as api_router

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.schemas import ErrorResponse, WelcomeResponse

settings = get_settings()
logger = configure_logging()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin) for origin in settings.backend_cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_error_response(status_code: int, detail: object, message: str | None = None) -> ErrorResponse:
    phrase = HTTPStatus(status_code).phrase if status_code in HTTPStatus._value2member_map_ else "Error"
    return ErrorResponse(code=status_code, message=message or phrase, detail=detail)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    logger.warning("HTTPException raised", extra={"path": request.url.path, "status_code": exc.status_code})
    error = _build_error_response(status_code=exc.status_code, detail=exc.detail)
    return JSONResponse(status_code=exc.status_code, content=error.model_dump(by_alias=True))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    sanitized_errors = [
        {"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    logger.warning(
        "Validation error",
        extra={"path": request.url.path, "error_count": len(sanitized_errors)},
    )
    error = _build_error_response(status_code=422, detail=sanitized_errors, message="Validation Error")
    return JSONResponse(status_code=422, content=error.model_dump(by_alias=True))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled server error", exc_info=exc, extra={"path": request.url.path})
    error = _build_error_response(status_code=500, detail="Internal Server Error")
    return JSONResponse(status_code=500, content=error.model_dump(by_alias=True))


@app.get(
    "/",
    response_model=WelcomeResponse,
    summary="根路由",
    responses={404: {"model": ErrorResponse}},
)
async def root() -> WelcomeResponse:
    return WelcomeResponse(message="Welcome to AIDetector API")


# 方式一：像现在这样，一个个挂载 v1 子路由（你已经在用的方式）
app.include_router(health_router, prefix="")
app.include_router(db_router, prefix="")
app.include_router(auth_router, prefix="")
app.include_router(auth_router, prefix="/api")
app.include_router(api_keys_router, prefix="")
app.include_router(detection_router, prefix="")
app.include_router(scan_router, prefix="")
app.include_router(admin_router, prefix="")
app.include_router(teams_router, prefix="")

# 方式二（可选）：改用聚合 router（使用上面 api/__init__.py 和 api/v1/__init__.py）：
# from app.api import router as api_router
# app.include_router(api_router, prefix="")
