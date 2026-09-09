"""AES 密码加解密工具 - 密钥从环境变量 SECRET_KEY 读取"""
import os
import base64
import hashlib
from cryptography.fernet import Fernet


def _get_key() -> bytes:
    raw = os.environ.get("SECRET_KEY", "").strip()
    if not raw:
        raise RuntimeError("SECRET_KEY environment variable not set")
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


_fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_get_key())
    return _fernet


def encrypt_password(plain: str) -> str:
    """加密密码，空字符串返回空"""
    if not plain:
        return ""
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """解密密码，空字符串返回空；非加密格式返回原值（兼容历史明文）"""
    if not encrypted:
        return ""
    # 如果已经是 Fernet 格式（含前缀 gAAAAA），解密
    if encrypted.startswith("gAAAAA") and len(encrypted) > 50:
        try:
            return _get_fernet().decrypt(encrypted.encode()).decode()
        except Exception:
            pass
    # 历史明文，直接返回（迁移后不再出现）
    return encrypted
