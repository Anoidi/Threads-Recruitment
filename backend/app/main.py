from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import settings
from .database import engine
from .logging import configure_logging, get_logger

configure_logging()
log = get_logger("backend")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("application_started", env=settings.app_env, mock_mode=settings.threads_mock_mode or settings.ai_mock_mode)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Operator-Token"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))[:128]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        log.info("request", request_id=request_id, method=request.method, path=request.url.path, status_code=response.status_code)
        return response

    app.include_router(router)
    return app


app = create_app()

