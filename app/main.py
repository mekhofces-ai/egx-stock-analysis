from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_alerts import router as alerts_router
from app.api.routes_market_depth import router as market_depth_router
from app.api.routes_signals import router as signals_router
from app.api.routes_sources import router as sources_router
from app.api.routes_stocks import router as stocks_router
from app.api.routes_recommendations import router as recommendations_router
from app.api.routes_reports import router as reports_router
from app.api.routes_strategy import router as strategy_router
from app.config import DISCLAIMER, get_settings
from app.database import init_db
from app.jobs.scheduler import start_scheduler
from app.services.telegram_bot import create_bot_application, start_bot_application, stop_bot_application


settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(seed=True)
    bot_app = create_bot_application(settings) if settings.telegram_bot_embedded_enabled else None
    await start_bot_application(bot_app)
    scheduler = start_scheduler(settings)
    app.state.telegram_bot_app = bot_app
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await stop_bot_application(bot_app)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(sources_router)
app.include_router(signals_router)
app.include_router(stocks_router)
app.include_router(recommendations_router)
app.include_router(alerts_router)
app.include_router(strategy_router)
app.include_router(reports_router)
app.include_router(market_depth_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "ok", "disclaimer": DISCLAIMER}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "disclaimer": DISCLAIMER}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.api_host, port=settings.api_port, reload=False)
