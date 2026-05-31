from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


UsernameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=50)]
NicknameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
PasswordStr = Annotated[str, StringConstraints(min_length=6, max_length=128)]


class TMMatchCandidate(BaseModel):
    """单个TM匹配候选"""
    source_text: str
    target_text: str
    score: float
    diff_html: str | None = None
    collection_name: str | None = None
    creator_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TermMatchCandidate(BaseModel):
    """单个术语匹配候选"""
    source_text: str
    target_text: str
    term_base_name: str | None = None
    creator_name: str | None = None
    updated_at: str | None = None


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
    # 多个TM匹配候选（Top 5）
    tm_candidates: list[TMMatchCandidate] = []


UserRole = Literal["super_admin", "admin", "user"]
CreatableUserRole = Literal["admin", "user"]


class UserRead(BaseModel):
    id: str
    username: str
    nickname: str | None
    role: UserRole
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
    role: CreatableUserRole = "user"


class UpdateUserRequest(BaseModel):
    username: UsernameStr | None = None
    nickname: NicknameStr | None = None
    password: PasswordStr | None = None
    is_active: bool | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class InitStatusResponse(BaseModel):
    initialized: bool
    requires_init: bool
    table_exists: bool = True
    message: str | None = None
