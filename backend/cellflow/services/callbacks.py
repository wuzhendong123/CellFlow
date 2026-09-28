"""结果回调：Outbox 投递、签名、指数退避重试（TECH_DESIGN §6.5.1、§10.7；v1 只回调提交方，D18）。"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request
from urllib.parse import urlparse

from sqlalchemy import and_, or_, select

from cellflow.config import ConfigError, resolve_ref
from cellflow.meta.db import get_engine
from cellflow.meta.tables import client_app, outbox, parse_job, pipeline, release
from cellflow.services import auth, settings

ISSUES_IN_CALLBACK = 50


def payload_for(job_id: int) -> dict:
    from cellflow.services.jobs import list_issues

    with get_engine().connect() as c:
        j = c.execute(select(parse_job).where(parse_job.c.id == job_id)).mappings().first()
        code = c.execute(select(pipeline.c.code).where(pipeline.c.id == j["pipeline_id"])).scalar()
        prev = None
        if j["release_id"]:
            prev = c.execute(select(release.c.prev_release_id).where(release.c.id == j["release_id"])).scalar()
    result = j["result"] or {}
    tables = [{"table": t["table"], "rows": t["rows"], "changes": t["changes"]} for t in result.get("tables", [])]
    body = {
        "event": "JOB_FINISHED", "jobId": j["id"], "pipelineCode": code, "mode": j["mode"], "status": j["status"],
        "releaseId": j["release_id"], "prevReleaseId": prev, "tables": tables,
        "issues": {"error": (j["error_summary"] or {}).get("error", 0), "warn": (j["error_summary"] or {}).get("warn", 0)},
    }
    if j["superseded_by"]:
        body["supersededBy"] = j["superseded_by"]
    if result.get("blockedBy"):
        body["blockedBy"] = result["blockedBy"]
        body["guards"] = [g for g in result.get("guards", []) if not g["passed"]]
    if result.get("forecast"):
        body["forecast"] = result["forecast"]
    if j["status"] not in ("PUBLISHED", "NO_CHANGE", "VALIDATED", "SUPERSEDED"):
        body["issueList"] = list_issues(job_id, limit=ISSUES_IN_CALLBACK)["rows"]
        if (j["error_summary"] or {}).get("code"):
            body["error"] = {"code": j["error_summary"]["code"], "message": j["error_summary"].get("message")}
    return body


def enqueue_job_callback(job_id: int) -> None:
    with get_engine().begin() as c:
        j = c.execute(select(parse_job.c.callback_url, parse_job.c.client_app_id, parse_job.c.mode).where(
            parse_job.c.id == job_id)).first()
        if not j or not j[0] or j[2] == "TEST":
            return
        c.execute(outbox.insert().values(job_id=job_id, channel="CALLBACK", target=j[0], payload={"jobId": job_id},
                                         status="PENDING", attempts=0, next_retry_at=dt.datetime.now()))


def allowed_callback(url: str, allowlist: list[str]) -> bool:
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme not in ("http", "https"):
        return False
    return any(host == d or host.endswith("." + d) for d in allowlist or [])


def _post(url: str, body: bytes, headers: dict, timeout: float = 5.0) -> int:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 回调地址已按白名单校验
        return resp.status


def deliver_due(now: dt.datetime | None = None, poster=None) -> int:
    """投递到期的回调；返回成功条数。"""
    now = now or dt.datetime.now()
    poster = poster or _post
    max_attempts = settings.get("callback.maxAttempts")
    done = 0
    with get_engine().connect() as c:
        rows = [dict(r) for r in c.execute(select(outbox).where(and_(
            outbox.c.status == "PENDING", or_(outbox.c.next_retry_at.is_(None), outbox.c.next_retry_at <= now)))
            .order_by(outbox.c.id).limit(100)).mappings()]
    for r in rows:
        body = json.dumps(payload_for(r["job_id"]), ensure_ascii=False, default=str).encode()
        with get_engine().connect() as c:
            app = c.execute(select(client_app).join(parse_job, parse_job.c.client_app_id == client_app.c.id)
                            .where(parse_job.c.id == r["job_id"])).mappings().first()
        headers = {}
        if app:
            try:
                secret = resolve_ref(app["secret_ref"])
                ts, nonce = str(int(time.time())), f"cb-{r['id']}-{r['attempts']}"
                path = urlparse(r["target"]).path or "/"
                headers = {"X-CF-AppKey": app["app_key"], "X-CF-Timestamp": ts, "X-CF-Nonce": nonce,
                           "X-CF-Signature": auth.sign(secret, "POST", path, ts, nonce, body)}
            except ConfigError:
                headers = {}
        err = None
        try:
            status = poster(r["target"], body, headers)
            if not 200 <= status < 300:
                err = f"HTTP {status}"
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:200]}"
        with get_engine().begin() as c:
            if err is None:
                c.execute(outbox.update().where(outbox.c.id == r["id"]).values(status="DELIVERED", attempts=r["attempts"] + 1,
                                                                               last_error=None))
                done += 1
            else:
                attempts = r["attempts"] + 1
                gave_up = attempts >= max_attempts
                c.execute(outbox.update().where(outbox.c.id == r["id"]).values(
                    status="FAILED" if gave_up else "PENDING", attempts=attempts, last_error=err[:512],
                    next_retry_at=None if gave_up else now + dt.timedelta(seconds=min(2 ** attempts * 5, 3600))))
    return done


def attempts_for_job(job_id: int) -> list[dict]:
    with get_engine().connect() as c:
        rows = [dict(r) for r in c.execute(select(outbox).where(outbox.c.job_id == job_id).order_by(outbox.c.id)).mappings()]
    return [{"id": r["id"], "target": r["target"], "status": r["status"], "attempts": r["attempts"], "lastError": r["last_error"],
             "nextRetryAt": r["next_retry_at"].isoformat() if r["next_retry_at"] else None} for r in rows]
