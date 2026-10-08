from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import Settings
from ..database import get_db
from ..deps import get_app_settings, get_config
from ..schemas.misc import MetaOut
from ..schemas.settings import AppSettings
from ..services.seed import DEMO_ACCOUNTS

router = APIRouter(tags=["meta"])


@router.get("/health", summary="Service health (database and LLM service)")
async def health(request: Request, db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    client = request.app.state.llm_client
    llm = await client.health()
    return {
        "status": "ok",
        "version": __version__,
        "database": "ok",
        "llm_service": {"url": client.base_url, "reachable": llm is not None, "ollama": llm["ollama"] if llm else None},
    }


@router.get("/meta", response_model=MetaOut, summary="Public app info for the login page")
def meta(config: Settings = Depends(get_config), settings: AppSettings = Depends(get_app_settings)) -> MetaOut:
    return MetaOut(
        app_name=settings.display.app_name,
        version=__version__,
        demo_mode=config.demo_mode,
        allow_signup=settings.access.allow_self_signup,
        demo_accounts=DEMO_ACCOUNTS if config.demo_mode else [],
    )
