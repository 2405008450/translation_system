from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import (
    ADMIN_ROLE,
    SUPER_ADMIN_ROLE,
    USER_ROLE,
    authenticate_user,
    build_auth_response,
    count_active_super_admins,
    count_users,
    create_user,
    get_current_user,
    get_user_by_id,
    is_admin_role,
    is_super_admin_role,
    list_users,
    require_admin,
    require_project_assignment_manager,
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


PROFILE_UPDATE_FIELDS = {"username", "nickname", "password"}


def _can_update_user_profile(current_user: User, target_user: User) -> bool:
    if current_user.id == target_user.id:
        return True
    if is_super_admin_role(current_user.role):
        return True
    return current_user.role == ADMIN_ROLE and target_user.role == USER_ROLE


def _require_create_user_role_access(current_user: User, role: str) -> None:
    if role == ADMIN_ROLE and not is_super_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="只有超级管理员可以创建管理员账号。")


def _require_translator_type_update_access(current_user: User, target_user: User) -> None:
    if target_user.role != USER_ROLE:
        raise HTTPException(status_code=400, detail="只有普通用户账号可以设置译者类型。")
    if not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="只有管理员可以设置译者类型。")


def _require_status_update_access(
    *,
    db: Session,
    current_user: User,
    target_user: User,
    next_is_active: bool | None,
) -> None:
    if not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="只有管理员可以启用或停用用户。")

    if target_user.id == current_user.id and next_is_active is False:
        raise HTTPException(status_code=400, detail="不能停用当前登录账号。")

    if is_super_admin_role(current_user.role):
        if (
            target_user.role == SUPER_ADMIN_ROLE
            and target_user.is_active
            and next_is_active is False
            and count_active_super_admins(db) <= 1
        ):
            raise HTTPException(status_code=400, detail="至少需要保留一个启用中的超级管理员账号。")
        return

    if target_user.role != USER_ROLE:
        raise HTTPException(status_code=403, detail="管理员只能启用或停用普通用户。")


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
        role=SUPER_ADMIN_ROLE,
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
    _: User = Depends(require_admin),
) -> list[UserRead]:
    return [
        UserRead.model_validate(serialize_user(user))
        for user in list_users(db)
    ]


@router.get("/assignable-users", response_model=list[UserRead])
def get_assignable_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_project_assignment_manager),
) -> list[UserRead]:
    return [
        UserRead.model_validate(serialize_user(user))
        for user in list_users(db)
        if user.role == USER_ROLE and user.is_active
    ]


@router.post("/register", response_model=UserRead)
def register_user(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> UserRead:
    require_users_table()
    _require_create_user_role_access(current_user, payload.role)
    user = create_user(
        db=db,
        username=payload.username,
        nickname=payload.nickname,
        password=payload.password,
        role=payload.role,
        translator_type=payload.translator_type,
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

    if updated_fields & PROFILE_UPDATE_FIELDS and not _can_update_user_profile(current_user, target_user):
        raise HTTPException(status_code=403, detail="\u65e0\u6743\u4fee\u6539\u8be5\u8d26\u53f7\u7684\u57fa\u672c\u4fe1\u606f\u3002")

    if "is_active" in updated_fields:
        _require_status_update_access(
            db=db,
            current_user=current_user,
            target_user=target_user,
            next_is_active=payload.is_active,
        )

    if "translator_type" in updated_fields:
        _require_translator_type_update_access(current_user, target_user)

    user = update_user_profile(
        db=db,
        user=target_user,
        username=payload.username,
        nickname=payload.nickname,
        password=payload.password,
        translator_type=payload.translator_type,
        is_active=payload.is_active,
        update_username="username" in updated_fields,
        update_nickname="nickname" in updated_fields,
        update_password="password" in updated_fields,
        update_translator_type="translator_type" in updated_fields,
        update_is_active="is_active" in updated_fields,
    )
    return UserRead.model_validate(serialize_user(user))
