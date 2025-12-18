from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.db import router as db_router
from app.api.v1.health import router as health_router
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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    logger.warning("HTTPException raised", extra={"path": request.url.path, "status_code": exc.status_code})
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(code=exc.status_code, message=str(exc.detail), detail=exc.detail).model_dump(),
    )


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
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(code=422, message="Validation Error", detail=sanitized_errors).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled server error", exc_info=exc, extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(code=500, message="Internal Server Error", detail="Internal Server Error").model_dump(),
    )


@app.get(
    "/",
    response_model=WelcomeResponse,
    summary="根路由",
    responses={404: {"model": ErrorResponse}},
)
async def root() -> WelcomeResponse:
    return WelcomeResponse(message="Welcome to AIDetector API")


app.include_router(health_router, prefix="")
app.include_router(db_router, prefix="")
app.include_router(auth_router, prefix="")
