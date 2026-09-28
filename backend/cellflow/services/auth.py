"""控制台高危操作口令（D19）与 Open API 签名鉴权（§6.5.1）。"""

from __future__ import annotations

import hashlib
import hmac
import time

from cellflow.config import ConfigError, get_settings, resolve_ref
from cellflow.errors import CFError
from cellflow.redis_client import get_redis

LOCK_AFTER = 5
LOCK_SECONDS = 600


def check_op_token(token: str | None, operator: str, ip: str) -> None:
    """校验操作口令；同一来源连续 5 次错误锁定 10 分钟。接入 SSO 后替换为角色校验，接口不变。"""
    r = get_redis()
    lock_key, fail_key = f"cf:optoken:lock:{ip}", f"cf:optoken:fail:{ip}"
    if r.exists(lock_key):
        raise CFError("OP_TOKEN_LOCKED", "口令错误次数过多，请 10 分钟后再试", 403, {"retryAfterSec": r.ttl(lock_key)})
    if not operator or operator == "anonymous":
        raise CFError("OP_TOKEN_INVALID", "请填写操作人", 403)
    expected = get_settings().op_token
    if not expected:
        raise CFError("OP_TOKEN_NOT_CONFIGURED", "服务端未配置操作口令（CF_OP_TOKEN）", 500)
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        n = r.incr(fail_key)
        r.expire(fail_key, LOCK_SECONDS)
        if n >= LOCK_AFTER:
            r.setex(lock_key, LOCK_SECONDS, 1)
            r.delete(fail_key)
            raise CFError("OP_TOKEN_LOCKED", "口令错误次数过多，请 10 分钟后再试", 403, {"retryAfterSec": LOCK_SECONDS})
        raise CFError("OP_TOKEN_INVALID", "操作口令错误", 403, {"remaining": LOCK_AFTER - n})
    r.delete(fail_key)


def sign(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    msg = "\n".join([method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()]).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(app: dict, method: str, path: str, timestamp: str | None, nonce: str | None,
                     signature: str | None, body: bytes) -> None:
    if not (timestamp and nonce and signature):
        raise CFError("AUTH_INVALID_SIGNATURE", "缺少签名请求头", 401)
    try:
        ts = int(timestamp)
    except ValueError:
        raise CFError("AUTH_INVALID_SIGNATURE", "时间戳格式错误", 401) from None
    if abs(time.time() - ts) > 300:
        raise CFError("AUTH_INVALID_SIGNATURE", "请求已过期（时间戳偏差超过 5 分钟）", 401)
    try:
        secret = resolve_ref(app["secret_ref"])
    except ConfigError:
        raise CFError("AUTH_INVALID_SIGNATURE", "调用方密钥未配置", 401) from None
    expected = sign(secret, method, path, timestamp, nonce, body)
    if not hmac.compare_digest(expected, signature):
        raise CFError("AUTH_INVALID_SIGNATURE", "签名错误", 401)
    if not get_redis().set(f"cf:nonce:{app['app_key']}:{nonce}", 1, nx=True, ex=600):
        raise CFError("AUTH_INVALID_SIGNATURE", "请求重放（nonce 重复）", 401)


def rate_limit(app: dict) -> None:
    r = get_redis()
    key = f"cf:rate:{app['app_key']}:{int(time.time() // 60)}"
    n = r.incr(key)
    r.expire(key, 120)
    if n > int(app["rate_limit_per_min"]):
        raise CFError("RATE_LIMITED", "超过调用频率限制", 429)
