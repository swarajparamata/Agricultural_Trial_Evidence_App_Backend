from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import User
from ..schemas.auth import UserCreate, UserOut, UserUpdate
from ..services.users import create_user, delete_user, update_user

router = APIRouter(prefix="/users", tags=["users (admin)"])


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.get("", response_model=list[UserOut], summary="List users")
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at, User.id)))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Add a user")
def add_user(body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> User:
    user = create_user(db, body, admin.email)
    db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut, summary="Change a user's name, role, status or password")
def edit_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> User:
    user = update_user(db, _get_user(db, user_id), body, admin)
    db.commit()
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a user")
def remove_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    delete_user(db, _get_user(db, user_id), admin)
    db.commit()
