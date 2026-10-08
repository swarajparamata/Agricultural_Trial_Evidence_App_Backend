from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_app_settings, get_current_user
from ..models import User
from ..schemas.auth import (
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    SignupRequest,
    TokenResponse,
    UserCreate,
    UserOut,
)
from ..schemas.settings import AppSettings
from ..security import create_access_token, hash_password, verify_password
from ..services import audit
from ..services.users import authenticate, create_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(request: Request, user: User) -> TokenResponse:
    token, expires_at = create_access_token(
        user_id=user.id,
        role=user.role,
        secret=request.app.state.secret_key,
        expires_minutes=request.app.state.config.access_token_expire_minutes,
    )
    return TokenResponse(access_token=token, expires_at=expires_at, user=UserOut.model_validate(user))


def _login(request: Request, db: Session, email: str, password: str) -> TokenResponse:
    user = authenticate(db, email, password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")
    db.commit()
    return _token_response(request, user)


@router.post("/login", response_model=TokenResponse, summary="Sign in with email and password")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    return _login(request, db, body.email, body.password)


@router.post("/token", response_model=TokenResponse, summary="OAuth2 form login (used by the API docs' Authorize button)")
def token(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenResponse:
    return _login(request, db, form.username, form.password)


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, summary="Create a viewer account")
def signup(
    body: SignupRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_app_settings),
) -> TokenResponse:
    if not settings.access.allow_self_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Sign-up is switched off. Ask an admin to create your account.")
    user = create_user(db, UserCreate(email=body.email, full_name=body.full_name, password=body.password), actor=body.email)
    db.commit()
    return _token_response(request, user)


@router.get("/me", response_model=UserOut, summary="The signed-in user")
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut, summary="Update your profile")
def update_me(body: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    user.full_name = body.full_name.strip()
    db.commit()
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, summary="Change your password")
def change_password(body: PasswordChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The current password is incorrect.")
    user.hashed_password = hash_password(body.new_password)
    audit.record(db, user.email, "user.change_password", user.email)
    db.commit()
