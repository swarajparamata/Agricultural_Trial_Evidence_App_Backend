from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import EMAIL_PATTERN, Role, UtcDateTime


def _lower_email(value: str) -> str:
    return value.strip().lower()


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)

    _email = field_validator("email")(_lower_email)


class SignupRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    password: str = Field(min_length=6, max_length=200)
    full_name: str = Field("", max_length=255)

    _email = field_validator("email")(_lower_email)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: UtcDateTime
    last_login_at: UtcDateTime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: UtcDateTime
    user: UserOut


class ProfileUpdate(BaseModel):
    full_name: str = Field(max_length=255)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=6, max_length=200)


class UserCreate(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    full_name: str = Field("", max_length=255)
    password: str = Field(min_length=6, max_length=200)
    role: Role = "viewer"

    _email = field_validator("email")(_lower_email)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=255)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=6, max_length=200)
