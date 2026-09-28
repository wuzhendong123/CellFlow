"""控制台录入的敏感信息（如数据源口令）加密存储：主密钥只来自环境变量 CF_SECRET_KEY，库里只存密文。"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from cellflow.errors import CFError


def _fernet() -> Fernet:
    secret = os.environ.get("CF_SECRET_KEY", "")
    if not secret:
        raise CFError("SECRET_KEY_MISSING", "服务端未配置 CF_SECRET_KEY，无法保存或使用直接填写的数据库口令", 422)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise CFError("SECRET_DECRYPT_FAILED", "数据源口令无法解密（CF_SECRET_KEY 可能已更换），请在控制台重新填写口令", 422) from None
