from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import (
    authenticate_user,
    build_auth_response,
    count_active_admins,
    count_users,
    create_user,
    get_current_user,
    get_user_by_id,
    list_users,
    require_admin,
    require_users_table,
    serialize_user,
    update_user_profile,
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
    UpdateUserRequest,
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
        nickname=payload.nickname,
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


@router.get("/users", response_model=list[UserRead])
def get_users(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[UserRead]:
    return [
        UserRead.model_validate(serialize_user(user))
        for user in list_users(db)
    ]


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
        nickname=payload.nickname,
        password=payload.password,
        role=payload.role,
    )
    return UserRead.model_validate(serialize_user(user))


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user_account(
    user_id: UUID,
    payload: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserRead:
    require_users_table()
    target_user = get_user_by_id(db, user_id)
    if target_user is None:
        raise HTTPException(status_code=404, detail="\u7528\u6237\u4e0d\u5b58\u5728\u3002")

    updated_fields = payload.model_fields_set
    if not updated_fields:
        raise HTTPException(status_code=400, detail="\u8bf7\u81f3\u5c11\u63d0\u4ea4\u4e00\u4e2a\u9700\u8981\u4fee\u6539\u7684\u5b57\u6bb5\u3002")

    if "is_active" in updated_fields:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="\u53ea\u6709\u7ba1\u7406\u5458\u53ef\u4ee5\u542f\u7528\u6216\u505c\u7528\u7528\u6237\u3002")
        if target_user.id == current_user.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="\u4e0d\u80fd\u505c\u7528\u5f53\u524d\u767b\u5f55\u8d26\u53f7\u3002")
        if (
            target_user.role == "admin"
            and target_user.is_active
            and payload.is_active is False
            and count_active_admins(db) <= 1
        ):
            raise HTTPException(status_code=400, detail="\u81f3\u5c11\u9700\u8981\u4fdd\u7559\u4e00\u4e2a\u542f\u7528\u4e2d\u7684\u7ba1\u7406\u5458\u8d26\u53f7\u3002")

    user = update_user_profile(
        db=db,
        user=target_user,
        username=payload.username,
        nickname=payload.nickname,
        password=payload.password,
        is_active=payload.is_active,
        update_username="username" in updated_fields,
        update_nickname="nickname" in updated_fields,
        update_password="password" in updated_fields,
        update_is_active="is_active" in updated_fields,
    )
    return UserRead.model_validate(serialize_user(user))
