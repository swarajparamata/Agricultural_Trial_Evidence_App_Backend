"""User accounts. Only admins manage them; guards keep at least one active admin."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import utcnow
from ..models import User
from ..schemas.auth import UserCreate, UserUpdate
from ..security import hash_password, verify_password
from . import audit


def find_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = find_by_email(db, email)
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        return None
    user.last_login_at = utcnow()
    return user


def create_user(db: Session, payload: UserCreate, actor: str) -> User:
    if find_by_email(db, payload.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"A user with email {payload.email} already exists.")
    user = User(
        email=payload.email,
        full_name=payload.full_name.strip(),
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()
    audit.record(db, actor, "user.create", user.email, f"role {user.role}")
    return user


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))) or 0


def update_user(db: Session, user: User, payload: UserUpdate, actor: User) -> User:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    demoting = changes.get("role") == "viewer" and user.role == "admin"
    deactivating = changes.get("is_active") is False and user.is_active
    if user.id == actor.id and (demoting or deactivating):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You can't remove your own admin access or deactivate yourself.")
    if (demoting or deactivating) and user.role == "admin" and user.is_active and _active_admins(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="At least one active admin is required.")

    if "full_name" in changes:
        user.full_name = changes["full_name"].strip()
    if "role" in changes:
        user.role = changes["role"]
    if "is_active" in changes:
        user.is_active = changes["is_active"]
    if "password" in changes:
        user.hashed_password = hash_password(changes["password"])
    db.flush()
    described = ", ".join(f"{key}={'***' if key == 'password' else value}" for key, value in changes.items())
    audit.record(db, actor.email, "user.update", user.email, described)
    return user


def delete_user(db: Session, user: User, actor: User) -> None:
    if user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You can't delete your own account.")
    if user.role == "admin" and user.is_active and _active_admins(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="At least one active admin is required.")
    db.delete(user)
    db.flush()
    audit.record(db, actor.email, "user.delete", user.email)
