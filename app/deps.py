"""FastAPI dependencies: database session, current user, role checks, settings and the LLM client."""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import Settings
from .database import get_db
from .models import User
from .schemas.settings import AppSettings
from .security import decode_access_token
from .services.llm_client import LlmClient
from .services.settings_store import load_settings

# auto_error=False so a missing token gets our own message rather than FastAPI's generic one.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


def get_config(request: Request) -> Settings:
    return request.app.state.config


def get_llm_client(request: Request) -> LlmClient:
    return request.app.state.llm_client


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=message, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    if not token:
        raise _unauthorized("Not signed in.")
    payload = decode_access_token(token, request.app.state.secret_key)
    if payload is None:
        raise _unauthorized("Your session has expired. Please sign in again.")
    user = db.get(User, int(payload.get("sub", 0)))
    if user is None or not user.is_active:
        raise _unauthorized("This account no longer has access.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only admins can do this.")
    return user


def get_app_settings(db: Session = Depends(get_db)) -> AppSettings:
    return load_settings(db)
