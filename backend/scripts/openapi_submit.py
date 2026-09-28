"""命令行提交文件到 Open API（./cellflow.sh submit <文件>），签名方式与业务服务接入完全一致，并等待结果。

文件内容从标准输入读取（这样宿主机任意路径的文件都能提交）。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

from cellflow.config import resolve_ref
from cellflow.services import client_apps


def _sign(secret: str, method: str, path: str, ts: str, nonce: str, body: bytes) -> str:
    msg = "\n".join([method, path, ts, nonce, hashlib.sha256(body).hexdigest()]).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _call(base: str, app: dict, secret: str, method: str, path: str, body: bytes = b"", ctype: str | None = None) -> tuple[int, dict]:
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    headers = {"X-CF-AppKey": app["appKey"], "X-CF-Timestamp": ts, "X-CF-Nonce": nonce,
               "X-CF-Signature": _sign(secret, method, path, ts, nonce, body)}
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(base + path, data=body if method == "POST" else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="upload.xlsx")
    ap.add_argument("--pipeline", default="hero_config")
    ap.add_argument("--mode", default="EXECUTE", choices=["EXECUTE", "VALIDATE_ONLY"])
    ap.add_argument("--app", default="演示调用方", help="调用方名称")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--console", default="http://localhost:8000")
    a = ap.parse_args()

    app = next((x for x in client_apps.list_all() if x["name"] == a.app), None)
    if not app:
        print(f"找不到调用方「{a.app}」，先运行 ./cellflow.sh demo 或在控制台新建", file=sys.stderr)
        return 2
    secret = resolve_ref(app["secretRef"])
    data = sys.stdin.buffer.read()
    boundary = "cf" + uuid.uuid4().hex
    meta = {"pipelineCode": a.pipeline, "mode": a.mode, "idempotencyKey": uuid.uuid4().hex, "operator": "cli"}
    body = b"".join([
        f'--{boundary}\r\nContent-Disposition: form-data; name="meta"\r\n\r\n{json.dumps(meta, ensure_ascii=False)}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{a.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode(),
        data, f"\r\n--{boundary}--\r\n".encode(),
    ])
    code, r = _call(a.base, app, secret, "POST", "/open/v1/jobs", body, f"multipart/form-data; boundary={boundary}")
    if code != 202:
        print(f"提交失败（HTTP {code}）：{r.get('code')} {r.get('message')}", file=sys.stderr)
        return 1
    jid = r["data"]["jobId"]
    print(f"已提交：任务 #{jid}（{a.mode}），等待执行…")
    for _ in range(600):
        _, r = _call(a.base, app, secret, "GET", f"/open/v1/jobs/{jid}")
        d = r["data"]
        if d["status"] not in ("SUBMITTED", "QUEUED", "RUNNING"):
            break
        time.sleep(0.5)
    print(f"状态：{d['status']}   问题：ERROR {d['issues'].get('error', 0)} / WARN {d['issues'].get('warn', 0)}")
    for t in d.get("tables") or []:
        ch = t.get("changes") or {}
        print(f"  {t['table']:<20} 行数 {t.get('rows')}  新增 {ch.get('c', 0)} / 修改 {ch.get('u', 0)} / 删除 {ch.get('d', 0)}")
    for t in (d.get("forecast") or {}).get("tables") or []:
        ch = t["changes"]
        print(f"  预判 {t['table']:<17} 新增 {ch['c']} / 修改 {ch['u']} / 删除 {ch['d']}  {'被拦截：' + ','.join(t['blockedBy']) if t['blockedBy'] else ''}")
    if d.get("blockedBy"):
        print(f"  被安全闸拦截：{', '.join(d['blockedBy'])}（可在控制台任务详情里放行）")
    print(f"详情：{a.console}/#/jobs/{jid}")
    return 0 if d["status"] in ("PUBLISHED", "NO_CHANGE", "VALIDATED") else 3


if __name__ == "__main__":
    sys.exit(main())
