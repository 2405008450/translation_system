from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


UsernameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=50)]
NicknameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
PasswordStr = Annotated[str, StringConstraints(min_length=6, max_length=128)]


class MatchResult(BaseModel):
    source_sentence: str
    status: str = Field(pattern="^(exact|fuzzy|none)$")
    score: float
    matched_source_text: str | None = None
    matched_collection_name: str | None = None
    matched_creator_name: str | None = None
    matched_created_at: str | None = None
    matched_updated_at: str | None = None
    target_text: str | None = None
    sentence_id: str = ""


class UserRead(BaseModel):
    id: str
    username: str
    nickname: str | None
    role: Literal["admin", "user"]
    is_active: bool
    created_at: str


class LoginRequest(BaseModel):
    username: UsernameStr
    password: PasswordStr


class InitAdminRequest(BaseModel):
    username: UsernameStr
    nickname: NicknameStr | None = None
    password: PasswordStr


class RegisterRequest(BaseModel):
    username: UsernameStr
    nickname: NicknameStr | None = None
    password: PasswordStr
    role: Literal["admin", "user"] = "user"


class UpdateUserRequest(BaseModel):
    username: UsernameStr | None = None
    nickname: NicknameStr | None = None
    password: PasswordStr | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class InitStatusResponse(BaseModel):
    initialized: bool
    requires_init: bool
    table_exists: bool = True
    message: str | None = None
