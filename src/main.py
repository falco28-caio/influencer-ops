from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.core.config import settings
from src.core.database import close_db, init_db
from src.core.logging import get_logger, setup_logging
from src.core.redis import close_redis, get_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    logger = get_logger("lifespan")

    # Startup
    logger.info("Starting InfluencerOps Agent", env=settings.app_env)

    # Initialize logging
    setup_logging()

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Initialize Redis
    await get_redis()
    logger.info("Redis connected")

    yield

    # Shutdown
    logger.info("Shutting down InfluencerOps Agent")
    await close_db()
    await close_redis()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="InfluencerOps Agent API - Communication efficiency and New IF acquisition",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(router, prefix="/api/v1")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
