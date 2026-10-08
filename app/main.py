"""FastAPI application factory. Run with: uvicorn app.main:app --reload"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, get_settings
from .database import Base, create_db_engine, create_session_factory
from .routers import admin, analysis, assistant, auth, data, meta, settings, trials, users
from .security import resolve_secret_key
from .services.llm_client import LlmClient
from .services.seed import seed_database

logger = logging.getLogger("agritrial")

DESCRIPTION = """
Backend of **AgriEvidence**, the agricultural trial evidence platform.

* Imports trial CSVs and free-text reports, maps spelling variants and units, and reconciles every
  trial across its sources, keeping conflicts and provenance visible.
* Users and roles: **admins** manage users, trials, imports and settings; **viewers** browse,
  compare and ask the assistant.
* The Evidence Assistant runs in the separate LLM service (LangGraph + Ollama/Qwen 3.6).

Sign in with the **Authorize** button (in demo mode: `admin@agrievidence.demo` / `admin123`).
"""


def create_app(config: Settings | None = None) -> FastAPI:
    config = config or get_settings()
    engine = create_db_engine(config.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if config.database_url.startswith("sqlite:///"):
            Path(config.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        app.state.secret_key = resolve_secret_key(config)
        Base.metadata.create_all(engine)
        with session_factory() as db:
            seed_database(db, config)
        logger.info("AgriEvidence API ready (demo mode: %s, LLM service: %s)", config.demo_mode, config.llm_service_url)
        yield
        engine.dispose()

    app = FastAPI(title=config.app_name, version=__version__, description=DESCRIPTION, lifespan=lifespan)
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.llm_client = LlmClient(config.llm_service_url, config.llm_service_api_key, config.llm_service_timeout_s)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (meta, auth, users, trials, analysis, settings, data, assistant, admin):
        app.include_router(module.router, prefix="/api")
    app.include_router(data.sources_router, prefix="/api")
    return app


app = create_app()
