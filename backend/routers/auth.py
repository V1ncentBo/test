"""JWT 认证路由 + 用户管理"""
import os
import json
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
import jwt
import re

from models.database import SessionLocal, User, get_db

router = APIRouter(prefix="/api/auth", tags=["认证与用户管理"])

# ═══════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════

JWT_SECRET = os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET or SECRET_KEY environment variable is required")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

# .env 中的默认管理员（兜底用）
ENV_ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ENV_ADMIN_PASS = os.getenv("ADMIN_PASS", "")

# ═══════════════════════════════════════════════════════════
#  权限目录（可配置的模块级访问权限）
# ═══════════════════════════════════════════════════════════
PERMISSION_CATALOG = [
    {"key": "dashboard", "label": "监控总览"},
    {"key": "machines", "label": "设备管理"},
    {"key": "monitor", "label": "监控详情"},
    {"key": "alerts", "label": "告警日志"},
    {"key": "ai", "label": "AI 分析"},
    {"key": "reports", "label": "数据报表"},
    {"key": "settings", "label": "通知设置"},
    {"key": "users", "label": "账号管理"},
    {"key": "deploy", "label": "部署指南"},
    {"key": "overview", "label": "资源总览"},
    {"key": "resources", "label": "资源台账"},
    {"key": "physical", "label": "硬件台账"},
    {"key": "proj", "label": "项目管理"},
    {"key": "owner", "label": "负责人"},
    {"key": "cabinets", "label": "机房机柜"},
    {"key": "dbs", "label": "数据库监控"},
]
ALL_PERMISSIONS = [p["key"] for p in PERMISSION_CATALOG]


def _sanitize_perms(perms):
    """只保留目录中已知的权限 key，去重保序"""
    if not isinstance(perms, list):
        return []
    seen = set()
    out = []
    for k in perms:
        if k in ALL_PERMISSIONS and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def parse_perms(raw):
    """从 DB 字符串解析权限列表"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
#  登录限流（防爆破）：同一 IP 60 秒内最多 5 次
# ─────────────────────────────────────────────────────────────
import time
from collections import defaultdict

_LOGIN_WINDOW = 60
_LOGIN_MAX = 5
_login_hits = defaultdict(list)

def _login_allowed(ip: str) -> bool:
    now = time.time()
    hits = _login_hits[ip]
    hits[:] = [t for t in hits if now - t < _LOGIN_WINDOW]
    if len(hits) >= _LOGIN_MAX:
        return False
    hits.append(now)
    return True


# ═══════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """bcrypt 哈希密码"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def create_token(username: str) -> str:
    """生成 JWT token"""
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


# ═══════════════════════════════════════════════════════════
#  请求/响应模型
# ═══════════════════════════════════════════════════════════

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str = "admin"
    display_name: str = ""
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=128)
    role: str = Field(default="user", pattern=r'^(admin|user)$')
    email: str = Field(default="", max_length=128)
    phone: str = Field(default="", max_length=32)
    permissions: list = Field(default=[])


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_enabled: Optional[bool] = None
    permissions: Optional[list] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


# ═══════════════════════════════════════════════════════════
#  认证相关
# ═══════════════════════════════════════════════════════════

@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """用户登录 — 优先查 users 表，失败时回退到 .env 管理员"""
    # 登录限流：同一 IP 60 秒内最多 5 次，防止暴力破解
    client_ip = request.client.host if request.client else "unknown"
    if not _login_allowed(client_ip):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")

    # 1. 尝试从 users 表验证
    user = db.query(User).filter(User.username == req.username).first()
    if user:
        if not user.is_enabled:
            raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")
        if verify_password(req.password, user.password_hash):
            # 更新最后登录时间
            user.last_login_at = datetime.now()
            db.commit()
            return LoginResponse(
                token=create_token(req.username),
                username=user.username,
                role=user.role,
                display_name=user.display_name or user.username,
                expires_in=JWT_EXPIRE_HOURS * 3600,
            )

    # 2. 回退到 .env 中的管理员（首次部署过渡用）
    if ENV_ADMIN_PASS and req.username == ENV_ADMIN_USER and req.password == ENV_ADMIN_PASS:
        # 自动将 .env 管理员写入 users 表
        try:
            existing = db.query(User).filter(User.username == ENV_ADMIN_USER).first()
            if not existing:
                new_admin = User(
                    username=ENV_ADMIN_USER,
                    password_hash=hash_password(ENV_ADMIN_PASS),
                    display_name="系统管理员",
                    role="admin",
                    is_enabled=True,
                )
                db.add(new_admin)
                db.commit()
                print("[Auth] 已将 .env 管理员迁移到 users 表")
        except Exception as e:
            db.rollback()
            print(f"[Auth] 管理员迁移失败: {e}")

        return LoginResponse(
            token=create_token(ENV_ADMIN_USER),
            username=ENV_ADMIN_USER,
            role="admin",
            display_name="系统管理员",
            expires_in=JWT_EXPIRE_HOURS * 3600,
        )

    raise HTTPException(status_code=401, detail="用户名或密码错误")


@router.get("/verify")
def verify_token(authorization: str = Header(None)):
    """验证 token 是否有效"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供 token")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {"valid": True, "username": payload.get("sub")}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="token 无效")



def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> dict:
    """JWT auth dependency for protected routes"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证 token")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        username = payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="token 无效")
    user = db.query(User).filter(User.username == username).first()
    if user:
        if not user.is_enabled:
            raise HTTPException(status_code=403, detail="账号已被禁用")
        return {"username": username, "role": user.role, "user_id": user.id}
    if username == ENV_ADMIN_USER:
        return {"username": username, "role": "admin", "user_id": 0}
    raise HTTPException(status_code=401, detail="用户不存在")

@router.get("/me")
def get_me(authorization: str = Header(None), db: Session = Depends(get_db)):
    """获取当前登录用户信息"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供 token")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        username = payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="token 无效")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        return {
            "username": username,
            "role": "admin" if username == ENV_ADMIN_USER else "user",
            "display_name": username,
            "permissions": ALL_PERMISSIONS if username == ENV_ADMIN_USER else [],
        }
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name or user.username,
        "email": user.email,
        "phone": user.phone,
        "is_enabled": user.is_enabled,
        "permissions": ALL_PERMISSIONS if user.role == "admin" else parse_perms(user.permissions),
    }


@router.put("/change-password")
def change_password(
    req: ChangePasswordRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """当前用户修改自己的密码"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供 token")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        username = payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="token 无效")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        # 如果用户不在 users 表中（仅存在于 .env），不允许修改
        raise HTTPException(status_code=400, detail="当前账号不支持修改密码")

    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")

    user.password_hash = hash_password(req.new_password)
    user.updated_at = datetime.now()
    db.commit()

    return {"ok": True, "message": "密码修改成功"}


# ═══════════════════════════════════════════════════════════
#  用户管理（admin 权限）
# ═══════════════════════════════════════════════════════════

def require_admin(authorization: str = Header(None), db: Session = Depends(get_db)) -> dict:
    """验证当前用户是否为管理员"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供 token")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        username = payload.get("sub")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise HTTPException(status_code=401, detail="token 无效")

    user = db.query(User).filter(User.username == username).first()
    is_admin = False

    if user and user.role == "admin":
        is_admin = True
    elif not user and username == ENV_ADMIN_USER:
        is_admin = True

    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    return {"username": username, "role": "admin"}


def require_any_perm(*keys):
    """读接口最小权限（2026-09-09）：admin 恒放行；普通用户需持有任一 keys。

    用法：@router.get("/x", dependencies=[Depends(require_any_perm("a", "b"))])
    """
    def dep(authorization: str = Header(None), db: Session = Depends(get_db)) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="未提供 token")
        token = authorization[7:]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            raise HTTPException(status_code=401, detail="token 无效")
        username = payload.get("sub")
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        if user.role == "admin":
            return {"username": username, "role": "admin", "user_id": user.id}
        perms = parse_perms(user.permissions)
        for k in keys:
            if k in perms:
                return {"username": username, "role": user.role, "user_id": user.id}
        raise HTTPException(status_code=403, detail="无权限访问该资源")
    return dep


@router.get("/permissions-catalog", summary="权限目录", dependencies=[])
def permissions_catalog(
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """返回可配置的模块权限目录（需要管理员权限）"""
    return {"code": 0, "data": PERMISSION_CATALOG}


@router.get("/users", summary="获取用户列表", dependencies=[])
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """获取所有用户列表（需要管理员权限）"""
    total = db.query(User).count()
    users = db.query(User).order_by(User.id).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    items = []
    for u in users:
        items.append({
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name or "",
            "role": u.role,
            "email": u.email or "",
            "phone": u.phone or "",
            "is_enabled": u.is_enabled,
            "permissions": ALL_PERMISSIONS if u.role == "admin" else parse_perms(u.permissions),
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })

    return {
        "code": 0,
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        },
    }


@router.post("/users", summary="创建用户", dependencies=[])
def create_user(
    req: UserCreate,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """创建新用户（需要管理员权限）"""
    # 检查用户名是否已存在
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"用户名 '{req.username}' 已存在")

    # 检查是否与 .env 管理员同名
    if req.username == ENV_ADMIN_USER:
        raise HTTPException(status_code=400, detail=f"用户名 '{req.username}' 为系统保留")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        display_name=req.display_name or req.username,
        role=req.role,
        email=req.email,
        phone=req.phone,
        permissions=json.dumps(_sanitize_perms(req.permissions)),
        is_enabled=True,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "code": 0,
        "message": "用户创建成功",
        "data": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "email": user.email,
            "is_enabled": user.is_enabled,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
    }


@router.put("/users/{user_id}", summary="更新用户", dependencies=[])
def update_user(
    user_id: int,
    req: UserUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """更新用户信息（需要管理员权限）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 不允许修改 .env 管理员（通过 users 表管理的同名用户除外）
    if req.role is not None and req.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="无效的角色值")

    if req.display_name is not None:
        user.display_name = req.display_name
    if req.role is not None:
        user.role = req.role
    if req.email is not None:
        user.email = req.email
    if req.phone is not None:
        user.phone = req.phone
    if req.is_enabled is not None:
        user.is_enabled = req.is_enabled
    if req.permissions is not None:
        user.permissions = json.dumps(_sanitize_perms(req.permissions))
    if req.password:
        user.password_hash = hash_password(req.password)

    user.updated_at = datetime.now()
    db.commit()

    return {
        "code": 0,
        "message": "用户更新成功",
        "data": {"id": user.id, "username": user.username},
    }


@router.delete("/users/{user_id}", summary="删除用户", dependencies=[])
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """删除用户（需要管理员权限）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 至少保留一个管理员
    admin_count = db.query(User).filter(
        User.role == "admin", User.is_enabled == True
    ).count()
    if user.role == "admin" and admin_count <= 1:
        raise HTTPException(status_code=400, detail="不能删除最后一个管理员账号")

    username = user.username
    db.delete(user)
    db.commit()

    return {"code": 0, "message": f"用户 '{username}' 已删除"}
