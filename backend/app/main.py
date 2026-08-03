import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.routes import health, seo
from app.config import get_settings
from app.core.playwright_executor import shutdown_playwright_executor
from app.middleware.abuse_middleware import AbuseProtectionMiddleware
from app.platform_adapters.registry import ensure_adapters_registered

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_adapters_registered()
    yield
    shutdown_playwright_executor()


settings = get_settings()

app = FastAPI(
    title="Saturday Runs",
    description=(
        "Personal cabinet and global data core for Saturday park runs. "
        "Unified stats across platforms to lower the barrier for runners starting in a new system."
    ),
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AbuseProtectionMiddleware)

app.include_router(health.router)
# sitemap.xml, robots.txt и пререндер живут на корневых адресах, не под /api —
# роботы ходят по обычным путям сайта (см. nginx/conf.d/default.conf).
app.include_router(seo.router)
app.include_router(api_router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path)
    detail = str(exc) if settings.app_debug else f"{type(exc).__name__}: {exc}"
    return JSONResponse(status_code=500, content={"detail": detail})
