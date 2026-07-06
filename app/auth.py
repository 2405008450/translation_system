from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import engine, get_db
from app.models import User
from app.services.analytics_service import record_user_activity_safely


USERS_TABLE_MISSING_MESSAGE = "users 表不存在，请先执行 scripts/create_users_table.sql。"
SUPER_ADMIN_ROLE = "super_admin"
ADMIN_ROLE = "admin"
USER_ROLE = "user"
ADMIN_ROLES = {SUPER_ADMIN_ROLE, ADMIN_ROLE}
INTERNAL_TRANSLATOR_TYPE = "internal"
EXTERNAL_TRANSLATOR_TYPE = "external"
TRANSLATOR_TYPES = {INTERNAL_TRANSLATOR_TYPE, EXTERNAL_TRANSLATOR_TYPE}

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
_users_table_exists_cache = False


def is_super_admin_role(role: str | None) -> bool:
    return role == SUPER_ADMIN_ROLE


def is_admin_role(role: str | None) -> bool:
    return role in ADMIN_ROLES


def get_user_translator_type(user: User | None) -> str:
    if user is None:
        return INTERNAL_TRANSLATOR_TYPE
    translator_type = (getattr(user, "translator_type", None) or INTERNAL_TRANSLATOR_TYPE).strip().lower()
    return translator_type if translator_type in TRANSLATOR_TYPES else INTERNAL_TRANSLATOR_TYPE


def is_external_translator(user: User | None) -> bool:
    return (
        getattr(user, "role", None) == USER_ROLE
        and get_user_translator_type(user) == EXTERNAL_TRANSLATOR_TYPE
    )


def can_access_all_projects(user: User | None) -> bool:
    role = getattr(user, "role", None)
    return is_admin_role(role) or (
        role == USER_ROLE and get_user_translator_type(user) == INTERNAL_TRANSLATOR_TYPE
    )


def can_create_projects(user: User | None) -> bool:
    return can_access_all_projects(user)


def can_assign_projects(user: User | None) -> bool:
    return can_access_all_projects(user)


def can_create_resources(user: User | None) -> bool:
    return can_access_all_projects(user)


def normalize_translator_type(translator_type: str | None, role: str | None) -> str:
    if role != USER_ROLE:
        return INTERNAL_TRANSLATOR_TYPE
    cleaned = (translator_type or EXTERNAL_TRANSLATOR_TYPE).strip().lower()
    if cleaned not in TRANSLATOR_TYPES:
        raise HTTPException(status_code=400, detail="译者类型只能是 internal 或 external。")
    return cleaned


def users_table_exists() -> bool:
    global _users_table_exists_cache
    if _users_table_exists_cache:
        return True
    try:
        exists = inspect(engine).has_table(User.__tablename__)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail=f"无法检查 users 表状态：{exc}") from exc
    if exists:
        _users_table_exists_cache = True
    return exists


def require_users_table() -> None:
    if not users_table_exists():
        raise HTTPException(status_code=503, detail=USERS_TABLE_MISSING_MESSAGE)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: UUID) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def count_users(db: Session) -> int:
    require_users_table()
    return db.query(User).count()


def count_active_admins(db: Session) -> int:
    require_users_table()
    return (
        db.query(User)
        .filter(User.role.in_(ADMIN_ROLES), User.is_active.is_(True))
        .count()
    )


def count_active_super_admins(db: Session) -> int:
    require_users_table()
    return (
        db.query(User)
        .filter(User.role == SUPER_ADMIN_ROLE, User.is_active.is_(True))
        .count()
    )


def list_users(db: Session) -> list[User]:
    require_users_table()
    return (
        db.query(User)
        .order_by(User.created_at.desc(), User.username.asc())
        .all()
    )


def normalize_user_nickname(nickname: str | None, username: str) -> str:
    return (nickname or "").strip() or username


def create_user(
    db: Session,
    username: str,
    nickname: str | None,
    password: str,
    role: str = "user",
    translator_type: str | None = None,
    is_active: bool = True,
) -> User:
    require_users_table()
    existing_user = get_user_by_username(db, username)
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="用户名已存在。")

    cleaned_nickname = normalize_user_nickname(nickname, username)
    user = User(
        username=username,
        nickname=cleaned_nickname,
        hashed_password=hash_password(password),
        role=role,
        translator_type=normalize_translator_type(translator_type, role),
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user_profile(
    db: Session,
    user: User,
    *,
    username: str | None = None,
    nickname: str | None = None,
    password: str | None = None,
    translator_type: str | None = None,
    is_active: bool | None = None,
    update_username: bool = False,
    update_nickname: bool = False,
    update_password: bool = False,
    update_translator_type: bool = False,
    update_is_active: bool = False,
) -> User:
    require_users_table()

    original_username = user.username
    nickname_was_default = (user.nickname or "").strip() in {"", original_username}

    if update_username:
        if username is None:
            raise HTTPException(status_code=400, detail="\u7528\u6237\u540d\u4e0d\u80fd\u4e3a\u7a7a\u3002")
        existing_user = get_user_by_username(db, username)
        if existing_user is not None and existing_user.id != user.id:
            raise HTTPException(status_code=409, detail="\u7528\u6237\u540d\u5df2\u5b58\u5728\u3002")
        user.username = username

    if update_nickname:
        user.nickname = normalize_user_nickname(nickname, user.username)
    elif update_username and nickname_was_default:
        user.nickname = user.username

    if update_password:
        if password is None:
            raise HTTPException(status_code=400, detail="\u5bc6\u7801\u4e0d\u80fd\u4e3a\u7a7a\u3002")
        user.hashed_password = hash_password(password)

    if update_translator_type:
        user.translator_type = normalize_translator_type(translator_type, user.role)

    if update_is_active:
        if is_active is None:
            raise HTTPException(status_code=400, detail="\u542f\u7528\u72b6\u6001\u4e0d\u80fd\u4e3a\u7a7a\u3002")
        user.is_active = is_active

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    require_users_table()
    user = get_user_by_username(db, username)
    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(
    *,
    subject: str,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    settings = get_settings()
    expire_at = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire_at,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def build_auth_response(user: User) -> dict[str, Any]:
    access_token = create_access_token(
        subject=str(user.id),
        extra_claims={
            "username": user.username,
            "nickname": getattr(user, "nickname", None),
            "role": user.role,
            "translator_type": get_user_translator_type(user),
        },
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": serialize_user(user),
    }


def serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "username": user.username,
        "nickname": getattr(user, "nickname", None),
        "role": user.role,
        "translator_type": get_user_translator_type(user),
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat(),
    }


def get_user_display_name(user: User | None) -> str | None:
    if user is None:
        return None
    return normalize_user_nickname(user.nickname, user.username)


def _decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    require_users_table()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或已过期的认证凭据。",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = _decode_access_token(token)
        subject = payload.get("sub")
        if not subject:
            raise credentials_exception
        user_id = UUID(subject)
    except (JWTError, ValueError) as exc:
        raise credentials_exception from exc

    user = get_user_by_id(db, user_id)
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=403, detail="当前用户已被禁用。")
    record_user_activity_safely(user.id)
    return user


def require_project_creator(current_user: User = Depends(get_current_user)) -> User:
    if not can_create_projects(current_user):
        raise HTTPException(status_code=403, detail="\u5f53\u524d\u8d26\u53f7\u6ca1\u6709\u521b\u5efa\u9879\u76ee\u7684\u6743\u9650\u3002")
    return current_user


def require_project_assignment_manager(current_user: User = Depends(get_current_user)) -> User:
    if not can_assign_projects(current_user):
        raise HTTPException(status_code=403, detail="\u5f53\u524d\u8d26\u53f7\u6ca1\u6709\u9879\u76ee\u5206\u914d\u7684\u6743\u9650\u3002")
    return current_user


def require_resource_creator(current_user: User = Depends(get_current_user)) -> User:
    if not can_create_resources(current_user):
        raise HTTPException(status_code=403, detail="\u5f53\u524d\u8d26\u53f7\u6ca1\u6709\u65b0\u5efa\u8d44\u6e90\u5e93\u7684\u6743\u9650\u3002")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="当前操作需要管理员权限。")
    return current_user
