from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import (
    authenticate_user,
    build_auth_response,
    count_users,
    create_user,
    get_current_user,
    require_admin,
    require_users_table,
    serialize_user,
    users_table_exists,
)
from app.database import get_db
from app.models import User
from app.schemas import (
    AuthResponse,
    InitAdminRequest,
    InitStatusResponse,
    LoginRequest,
    RegisterRequest,
    UserRead,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/init", response_model=InitStatusResponse)
def get_init_status(
    db: Session = Depends(get_db),
) -> InitStatusResponse:
    if not users_table_exists():
        return InitStatusResponse(
            initialized=False,
            requires_init=True,
            table_exists=False,
            message="users 表不存在，请先执行 scripts/create_users_table.sql。",
        )

    initialized = count_users(db) > 0
    return InitStatusResponse(
        initialized=initialized,
        requires_init=not initialized,
        table_exists=True,
        message=None,
    )


@router.post("/init", response_model=AuthResponse)
def init_admin_account(
    payload: InitAdminRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    require_users_table()
    if count_users(db) > 0:
        raise HTTPException(status_code=409, detail="系统已初始化，不能重复创建管理员。")

    user = create_user(
        db=db,
        username=payload.username,
        password=payload.password,
        role="admin",
    )
    return AuthResponse.model_validate(build_auth_response(user))


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> AuthResponse:
    require_users_table()
    user = authenticate_user(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误。",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="当前用户已被禁用。")
    return AuthResponse.model_validate(build_auth_response(user))


@router.get("/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(serialize_user(current_user))


@router.post("/register", response_model=UserRead)
def register_user(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> UserRead:
    require_users_table()
    user = create_user(
        db=db,
        username=payload.username,
        password=payload.password,
        role=payload.role,
    )
    return UserRead.model_validate(serialize_user(user))
