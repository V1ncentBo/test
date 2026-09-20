"""运维文件库 —— 系统镜像 / 运维服务包 的上传、检索、下载、审计

模块定位：资源管理 → 运维文件库（第 7 项），深链 /u/filelib

关键设计（对齐平台既有铁律）：
  1. 文件本体落宿主机 /opt/monitor-platform/filelib/（bind 进容器 /app/filelib），
     不落容器可写层、不进 DB blob → docker commit 不会把几十 G 烤进镜像层。
  2. 上传必须流式分块：绝不 await file.read() 全量进内存。
     backend mem_limit=768m，5G ISO 一次性读入必然 OOM。
     同步磁盘写盘走 asyncio.to_thread 卸载，避免卡住单 worker 事件循环。
  3. 下载走后端流式吐而非 nginx 静态直发 —— 保下载审计（nginx 侧拿不到 JWT）。
     为兼顾"浏览器原生下载体验"（进度条/可断点），采用【签名 ticket】方案：
       前端先 POST /download-ticket 拿一次性 5 分钟票据 → 再 GET ...?t=<ticket>
     票据走内存字典 + 过期清理，不落库、不泄露长期 JWT。
  4. 权限：读 require_any_perm("filelib")；写 require_admin（仅 admin 可传/删/改）。
     兼容策略：v1 同时接受 "resources" 权限，避免只勾了资源台账的用户看不了入口。
"""
import os
import io
import time
import uuid
import hashlib
import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, desc, asc, or_
from sqlalchemy.orm import Session

from models.database import get_db, SessionLocal
from models.filelib import FileAsset, FileDownloadLog
from routers.auth import require_any_perm, require_admin

router = APIRouter(prefix="/api/files", tags=["运维文件库"])

# ═══════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════

LIB_DIR = os.getenv("FILELIB_DIR", "/app/filelib")
TMP_DIR = os.path.join(LIB_DIR, "_tmp")

# 单文件上限：独立 200G 盘到位后放开到 16G
MAX_BYTES = int(os.getenv("FILELIB_MAX_BYTES", str(16 * 1024 * 1024 * 1024)))
# 总量软上限：200G 盘留 50G 缓冲
SOFT_QUOTA_BYTES = int(os.getenv("FILELIB_SOFT_QUOTA", str(150 * 1024 * 1024 * 1024)))
# 低于此余量直接拒绝上传，防写满拖垮整机
MIN_FREE_BYTES = int(os.getenv("FILELIB_MIN_FREE", str(1024 * 1024 * 1024)))

CHUNK = 1024 * 1024  # 1MB 分块

CATEGORIES = [
    {"key": "os-image", "label": "系统镜像"},
    {"key": "app-package", "label": "应用服务包"},
    {"key": "driver-firmware", "label": "驱动固件"},
    {"key": "script-tool", "label": "脚本工具"},
    {"key": "doc-other", "label": "文档其他"},
]
VALID_CATS = {c["key"] for c in CATEGORIES}

# 扩展名白名单：不是防病毒（内网可信），是防手滑把可执行 web 脚本传进站点目录。
# 本方案文件存 filelib/，不在 nginx root 下，天然不可执行，这里是第二道闸。
ALLOWED_EXT = {
    # 系统镜像 / 虚机
    ".iso", ".img", ".ova", ".ovf", ".vmdk", ".vhd", ".vhdx", ".qcow2", ".vdi", ".raw",
    # 包管理
    ".deb", ".rpm", ".apk", ".msi", ".pkg", ".dmg",
    # 压缩包
    ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz", ".zip", ".7z", ".rar",
    # 二进制 / 安装器
    ".bin", ".run", ".exe", ".msu",
    # 脚本
    ".sh", ".ps1", ".bat", ".cmd", ".py", ".yml", ".yaml", ".conf", ".sql",
    # 文档
    ".pdf", ".txt", ".md", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".json",
    # 其它运维产物
    ".log", ".patch", ".diff", ".cer", ".crt", ".pem", ".key", ".jks", ".pfx",
    # 数据库转储
    ".bak", ".dump",
}

# ── 一次性下载票据（内存态，5 分钟有效）─────────────────────
_TICKETS = {}          # ticket -> {file_id, username, ip, exp}
TICKET_TTL = 300       # 秒


def _gc_tickets():
    """清理过期票据（调用频率低，线性扫足够）"""
    now = time.time()
    dead = [k for k, v in _TICKETS.items() if v["exp"] < now]
    for k in dead:
        _TICKETS.pop(k, None)


# ═══════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════

def _safe_ext(name: str) -> str:
    """取扩展名（小写）。.tar.gz 视为一个整体扩展名。"""
    low = (name or "").lower()
    for two in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if low.endswith(two):
            return two
    return os.path.splitext(low)[1]


def _human(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n or 0)
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return ("%.0f %s" if i == 0 else "%.2f %s") % (f, units[i])


def _disk_free() -> int:
    """宿主 filelib 所在分区剩余字节。用 os.statvfs，容错返回 -1。"""
    try:
        st = os.statvfs(LIB_DIR)
        return st.f_bavail * st.f_frsize
    except Exception:
        return -1


def _total_used_bytes(db: Session) -> int:
    v = db.query(func.coalesce(func.sum(FileAsset.size_bytes), 0)).filter(
        FileAsset.deleted_at.is_(None)
    ).scalar()
    return int(v or 0)


def _locate(stored_name: str, category: str = "") -> str:
    """定位磁盘文件。

    ⚠ 不能只按 DB 里记的 category 拼路径 —— PATCH 允许改分类，
    改了之后文件仍在旧目录，按新分类找会 miss（E2E 实测踩到）。
    策略：先试记录的 category，再全分类兜底扫。
    """
    if category:
        p = os.path.join(LIB_DIR, category, stored_name)
        if os.path.isfile(p):
            return p
    for c in VALID_CATS:
        p = os.path.join(LIB_DIR, c, stored_name)
        if os.path.isfile(p):
            return p
    return ""


def _record_to_dict(r: FileAsset) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "category": r.category,
        "distro": r.distro or "",
        "version": r.version or "",
        "arch": r.arch or "any",
        "size_bytes": int(r.size_bytes or 0),
        "size_human": _human(r.size_bytes or 0),
        "sha256": r.sha256 or "",
        "note": r.note or "",
        "tags": [t for t in (r.tags or "").split(",") if t],
        "uploader": r.uploader or "",
        "download_count": int(r.download_count or 0),
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "ext": _safe_ext(r.name),
    }


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def _ensure_dirs():
    """确保分类目录与临时目录存在（挂载盘首次使用/被换盘后自愈）"""
    os.makedirs(TMP_DIR, exist_ok=True)
    for c in VALID_CATS:
        os.makedirs(os.path.join(LIB_DIR, c), exist_ok=True)


def _write_chunk(fh, chunk: bytes):
    """同步写块（供 asyncio.to_thread 调用，避免同步 I/O 卡事件循环）"""
    fh.write(chunk)


# ═══════════════════════════════════════════════════════════
#  读接口
# ═══════════════════════════════════════════════════════════

@router.get("/meta", summary="分类字典 + 计数 + 磁盘余量")
def files_meta(
    request: Request,
    db: Session = Depends(get_db),
    _u=Depends(require_any_perm("filelib", "resources")),
):
    """前端顶部总结卡片与分类侧栏的数据源"""
    rows = db.query(FileAsset.category, func.count(FileAsset.id)).filter(
        FileAsset.deleted_at.is_(None)
    ).group_by(FileAsset.category).all()
    cnt = {k: int(v) for k, v in rows}
    total = sum(cnt.values())
    used = _total_used_bytes(db)

    week_ago = datetime.now() - timedelta(days=7)
    dl7 = db.query(func.count(FileDownloadLog.id)).filter(
        FileDownloadLog.created_at >= week_ago
    ).scalar() or 0

    free = _disk_free()
    return {
        "code": 0,
        "data": {
            "categories": [dict(c, count=cnt.get(c["key"], 0)) for c in CATEGORIES],
            "total_count": total,
            "used_bytes": used,
            "used_human": _human(used),
            "quota_bytes": SOFT_QUOTA_BYTES,
            "quota_human": _human(SOFT_QUOTA_BYTES),
            "quota_pct": round(used * 100.0 / SOFT_QUOTA_BYTES, 1) if SOFT_QUOTA_BYTES else 0,
            "disk_free_bytes": free,
            "disk_free_human": _human(free) if free >= 0 else "未知",
            "max_bytes": MAX_BYTES,
            "max_human": _human(MAX_BYTES),
            "downloads_7d": int(dl7),
        },
    }


@router.get("", summary="文件列表（分类/搜索/排序/分页）")
@router.get("/", include_in_schema=False)
def list_files(
    category: Optional[str] = Query(None, description="分类 key；all/空 = 全部"),
    q: Optional[str] = Query(None, description="关键词：文件名/发行版/版本/备注/标签"),
    sort: str = Query("created_at", description="created_at | size | download_count | name"),
    order: str = Query("desc", description="asc | desc"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _u=Depends(require_any_perm("filelib", "resources")),
):
    query = db.query(FileAsset).filter(FileAsset.deleted_at.is_(None))

    if category and category not in ("all", ""):
        query = query.filter(FileAsset.category == category)

    if q:
        like = "%" + q.strip() + "%"
        query = query.filter(or_(
            FileAsset.name.like(like),
            FileAsset.distro.like(like),
            FileAsset.version.like(like),
            FileAsset.note.like(like),
            FileAsset.tags.like(like),
        ))

    sort_map = {
        "created_at": FileAsset.created_at,
        "size": FileAsset.size_bytes,
        "download_count": FileAsset.download_count,
        "name": FileAsset.name,
    }
    col = sort_map.get(sort, FileAsset.created_at)
    query = query.order_by(asc(col) if order == "asc" else desc(col))

    total = query.count()
    rows = query.offset((page - 1) * size).limit(size).all()

    return {
        "code": 0,
        "data": {
            "items": [_record_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "size": size,
        },
    }


@router.get("/downloads", summary="下载审计日志")
def download_logs(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _u=Depends(require_any_perm("filelib", "resources")),
):
    rows = db.query(FileDownloadLog, FileAsset.name).outerjoin(
        FileAsset, FileAsset.id == FileDownloadLog.file_id
    ).order_by(desc(FileDownloadLog.created_at)).limit(limit).all()
    return {
        "code": 0,
        "data": [{
            "id": log.id,
            "file_id": log.file_id,
            "file_name": fname or "(已删除)",
            "username": log.username,
            "client_ip": log.client_ip,
            "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "",
        } for log, fname in rows],
    }


@router.get("/{fid}", summary="文件详情")
def get_file(
    fid: int,
    db: Session = Depends(get_db),
    _u=Depends(require_any_perm("filelib", "resources")),
):
    r = db.query(FileAsset).filter(
        FileAsset.id == fid, FileAsset.deleted_at.is_(None)
    ).first()
    if not r:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"code": 0, "data": _record_to_dict(r)}


# ═══════════════════════════════════════════════════════════
#  写接口（仅 admin）
# ═══════════════════════════════════════════════════════════

@router.post("/upload", summary="上传文件（仅 admin）")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("doc-other"),
    distro: str = Form(""),
    version: str = Form(""),
    arch: str = Form("any"),
    note: str = Form(""),
    tags: str = Form(""),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """流式上传：1MB 分块读 → 线程池写盘 → 边写边算 sha256。

    三道防写满闸门：
      ① 磁盘余量 < MIN_FREE_BYTES → 直接 507
      ② 累计字节 > MAX_BYTES → 中断 + 删临时文件 → 413
      ③ 总量软上限在 meta 里暴露，前端红条 + 企微告警（阶段 3）
    """
    _ensure_dirs()

    if category not in VALID_CATS:
        raise HTTPException(status_code=400, detail="非法分类")

    raw_name = file.filename or "unnamed"
    ext = _safe_ext(raw_name)
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="不支持的文件类型：%s" % (ext or "(无扩展名)"))

    free = _disk_free()
    if free >= 0 and free < MIN_FREE_BYTES:
        raise HTTPException(
            status_code=507,
            detail="磁盘余量不足（剩余 %s），拒绝上传" % _human(free),
        )

    _gc_tickets()
    tmp_path = os.path.join(TMP_DIR, uuid.uuid4().hex + ".part")
    h = hashlib.sha256()
    total = 0

    # 打开文件句柄（同步）→ 用 to_thread 卸载
    fh = await asyncio.to_thread(open, tmp_path, "wb")
    try:
        while True:
            # await file.read 本身走 Starlette 线程池，安全
            chunk = await file.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="文件超过单文件上限 %s" % _human(MAX_BYTES),
                )
            h.update(chunk)
            # 同步写盘 → 卸载到线程池，不卡事件循环
            await asyncio.to_thread(_write_chunk, fh, chunk)
    except HTTPException:
        try:
            await asyncio.to_thread(fh.close)
        except Exception:
            pass
        try:
            await asyncio.to_thread(os.remove, tmp_path)
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            await asyncio.to_thread(fh.close)
        except Exception:
            pass
        try:
            await asyncio.to_thread(os.remove, tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="写入失败：%s" % str(e)[:200])

    await asyncio.to_thread(fh.close)

    if total == 0:
        try:
            await asyncio.to_thread(os.remove, tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="空文件")

    digest = h.hexdigest()

    # 重复检测：同 sha256 已存在 → 提示（不阻止，管理员可能确实要再存一份）
    dup = db.query(FileAsset).filter(
        FileAsset.sha256 == digest, FileAsset.deleted_at.is_(None)
    ).first()

    # 组装落盘名：sha16_ts_安全名（扩展名保留；原始名只用于展示）
    # ⚠ 必须先剥掉完整扩展名（.tar.gz 算一个整体），否则会出现 xxx.tar.tar.gz
    base_name = raw_name
    for two in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if base_name.lower().endswith(two):
            base_name = base_name[: -len(two)]
            break
    else:
        base_name = os.path.splitext(base_name)[0]
    safe_base = "".join(ch for ch in base_name
                        if ch.isalnum() or ch in "-_.")[:80] or "file"
    stored_name = "%s_%d_%s%s" % (digest[:16], int(time.time()), safe_base, ext)

    dest_dir = os.path.join(LIB_DIR, category)
    dest_path = os.path.join(dest_dir, stored_name)

    try:
        await asyncio.to_thread(os.replace, tmp_path, dest_path)
        # 去执行位
        await asyncio.to_thread(os.chmod, dest_path, 0o644)
    except Exception as e:
        try:
            await asyncio.to_thread(os.remove, tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="落盘失败：%s" % str(e)[:200])

    rec = FileAsset(
        name=raw_name,
        stored_name=stored_name,
        category=category,
        distro=(distro or "")[:64],
        version=(version or "")[:64],
        arch=(arch or "any")[:24],
        size_bytes=total,
        sha256=digest,
        note=(note or "")[:500],
        tags=(tags or "")[:255],
        uploader=admin.get("username", ""),
        created_at=datetime.now(),
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    return {
        "code": 0,
        "data": _record_to_dict(rec),
        "duplicate_of": dup.id if dup else None,
        "message": ("已存在相同文件（id=%d），本次仍保存为新记录" % dup.id) if dup else "上传成功",
    }


class FilePatch(BaseModel):
    category: Optional[str] = None
    distro: Optional[str] = Field(None, max_length=64)
    version: Optional[str] = Field(None, max_length=64)
    arch: Optional[str] = Field(None, max_length=24)
    note: Optional[str] = Field(None, max_length=500)
    tags: Optional[str] = Field(None, max_length=255)


@router.patch("/{fid}", summary="修改元数据（仅 admin）")
def patch_file(
    fid: int,
    body: FilePatch,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    r = db.query(FileAsset).filter(
        FileAsset.id == fid, FileAsset.deleted_at.is_(None)
    ).first()
    if not r:
        raise HTTPException(status_code=404, detail="文件不存在")

    if body.category is not None:
        if body.category not in VALID_CATS:
            raise HTTPException(status_code=400, detail="非法分类")
        r.category = body.category
    if body.distro is not None:
        r.distro = body.distro
    if body.version is not None:
        r.version = body.version
    if body.arch is not None:
        r.arch = body.arch
    if body.note is not None:
        r.note = body.note
    if body.tags is not None:
        r.tags = body.tags

    db.commit()
    db.refresh(r)
    return {"code": 0, "data": _record_to_dict(r)}


@router.delete("/{fid}", summary="删除文件（仅 admin）")
def delete_file(
    fid: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    r = db.query(FileAsset).filter(
        FileAsset.id == fid, FileAsset.deleted_at.is_(None)
    ).first()
    if not r:
        raise HTTPException(status_code=404, detail="文件不存在")

    path = _locate(r.stored_name, r.category)
    if path:
        try:
            os.remove(path)
        except Exception:
            # 文件已不在（可能手工删过）→ 仍允许删元数据，不让用户卡住
            pass

    db.delete(r)
    db.commit()
    return {"code": 0, "message": "已删除", "id": fid, "file_removed": bool(path)}


# ═══════════════════════════════════════════════════════════
#  下载：签名 ticket 方案
# ═══════════════════════════════════════════════════════════

@router.post("/{fid}/download-ticket", summary="申请一次性下载票据")
def create_ticket(
    fid: int,
    request: Request,
    db: Session = Depends(get_db),
    u=Depends(require_any_perm("filelib", "resources")),
):
    """一次性票据：5 分钟有效、用后即焚。

    为什么不让下载接口直接带 JWT？
      <a href> 无法带 Authorization 头，而 fetch+blob 对大文件会整块进内存
      （5G ISO 直接崩浏览器）。故用短票据走原生下载，兼顾安全与体验。
    """
    _gc_tickets()

    r = db.query(FileAsset).filter(
        FileAsset.id == fid, FileAsset.deleted_at.is_(None)
    ).first()
    if not r:
        raise HTTPException(status_code=404, detail="文件不存在")

    path = _locate(r.stored_name, r.category)
    if not path:
        raise HTTPException(status_code=410, detail="文件已丢失，请联系管理员核对磁盘")

    tk = secrets.token_urlsafe(32)
    _TICKETS[tk] = {
        "file_id": fid,
        "username": u.get("username", ""),
        "ip": _client_ip(request),
        "exp": time.time() + TICKET_TTL,
        # 记录申请时票据绑定的文件名，下载时严格比对（防篡改 / 防路径穿越）
        "name": r.name or "",
    }
    # URL 形态：/{fid}/d/{票据}/{文件名}
    #   ⚠ 文件名**必须在最后一段**：wget / curl -O 都按「URL 末段」命名，
    #     若把票据放最后，落盘就是一串 ticket（实测踩到）。
    #   - 末段 = 真实文件名 → wget 默认落盘即正确文件名（不看 Content-Disposition）
    #   - 票据在中间段 → 不影响命名，且不会像 ?t= 那样被拖进文件名
    # 兼容保留旧的 /{fid}/download?t=… 与 /{fid}/download/{name}?t=… 两种形态。
    from urllib.parse import quote as _q
    tail = _q(r.name or "download", safe="")
    return {
        "code": 0,
        "data": {
            "ticket": tk,
            "url": "/api/files/%d/d/%s/%s" % (fid, tk, tail),
            "filename": r.name or "",
            "expires_in": TICKET_TTL,
        },
    }


@router.get("/{fid}/d/{t}/{fname}", summary="流式下载（票据+文件名都在路径里，wget 友好）")
@router.get("/{fid}/download", summary="流式下载（凭一次性票据）")
@router.get("/{fid}/download/{fname}", summary="流式下载（带文件名，可读性更好）", include_in_schema=False)
def download_file(
    fid: int,
    request: Request,
    t: str = "",
    fname: str = "",
):
    """校验票据 → 流式吐文件 → 写审计 + download_count++。

    注意：本接口不挂 require_any_perm（票据本身即凭证），
    但票据校验失败一律 403，且票据绑定了 file_id，不能跨文件复用。

    三种 URL 形态（同一实现）：
      1. /{fid}/d/{票据}/{文件名}   ← 推荐，wget 默认落盘就是纯文件名
      2. /{fid}/download/{文件名}?t={票据}
      3. /{fid}/download?t={票据}    ← 最早期形态，保留兼容

    `fname` 是「给人看 / 给下载器用」的装饰，不参与磁盘寻址
    （磁盘路径始终由 DB 的 stored_name 决定），但与票据里记录的文件名
    **严格比对**，不一致直接 403（防拿合法票据改末段做钓鱼/混淆）。
    """
    _gc_tickets()

    info = _TICKETS.get(t)
    if not info:
        raise HTTPException(status_code=403, detail="票据无效或已过期，请重新申请")
    if info["file_id"] != fid:
        raise HTTPException(status_code=403, detail="票据与文件不匹配")
    if info["exp"] < time.time():
        _TICKETS.pop(t, None)
        raise HTTPException(status_code=403, detail="票据已过期")

    # 名字比对（只在客户端传了才校验；兼容最早期不带名字的链接）
    if fname:
        from urllib.parse import unquote as _uq
        if _uq(fname) != info.get("name", ""):
            raise HTTPException(status_code=403, detail="URL 中的文件名与票据不匹配")

    # 用后即焚（一次性）
    _TICKETS.pop(t, None)

    db = SessionLocal()
    try:
        r = db.query(FileAsset).filter(
            FileAsset.id == fid, FileAsset.deleted_at.is_(None)
        ).first()
        if not r:
            raise HTTPException(status_code=404, detail="文件不存在")

        path = _locate(r.stored_name, r.category)
        if not path:
            raise HTTPException(status_code=410, detail="文件已丢失")

        # 审计
        db.add(FileDownloadLog(
            file_id=fid,
            username=info.get("username", ""),
            client_ip=_client_ip(request),
            created_at=datetime.now(),
        ))
        r.download_count = int(r.download_count or 0) + 1
        db.commit()

        fname = r.name
        fsize = int(r.size_bytes or 0)
    finally:
        db.close()

    # RFC 5987：非 ASCII 文件名用 filename*，兼容中文
    from urllib.parse import quote
    ascii_fallback = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in fname)
    disposition = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
        ascii_fallback or "download", quote(fname)
    )

    def gen():
        """分块读文件；用 iter 而非一次性 read，避免大文件进内存"""
        with open(path, "rb") as f:
            while True:
                b = f.read(CHUNK)
                if not b:
                    break
                yield b

    headers = {
        "Content-Disposition": disposition,
        "Content-Length": str(fsize),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.get("/{fid}/url", summary="获取平台内直链")
def file_url(
    fid: int,
    request: Request,
    db: Session = Depends(get_db),
    u=Depends(require_any_perm("filelib", "resources")),
):
    """给同事贴的直链（仍需票据，故这里返回的是需要二次申请票据的说明页地址）"""
    r = db.query(FileAsset).filter(
        FileAsset.id == fid, FileAsset.deleted_at.is_(None)
    ).first()
    if not r:
        raise HTTPException(status_code=404, detail="文件不存在")
    host = request.headers.get("host", "")
    scheme = "http"
    return {
        "code": 0,
        "data": {
            "page_url": "%s://%s/u/filelib?focus=%d" % (scheme, host, fid),
            "note": "下载需在平台内点击（需登录态），直链不携带长期凭证",
        },
    }
