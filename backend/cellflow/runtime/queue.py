"""任务派发：生产环境入 arq 队列由 Worker 执行；CF_JOB_INLINE=1 时在当前进程同步执行（测试/单机调试）。"""

from __future__ import annotations

import asyncio
import os

from cellflow.config import get_settings


def inline() -> bool:
    return os.environ.get("CF_JOB_INLINE") == "1"


def dispatch(job_id: int) -> None:
    if inline():
        from cellflow.runtime.executor import run_job

        run_job(job_id)
        from cellflow.services.callbacks import deliver_due

        deliver_due()
        return
    _enqueue("run_job", job_id, key=f"cf-job-{job_id}")


def dispatch_release_changes(release_id: int) -> None:
    """快路径回滚后补算变更明细（不阻塞回滚请求）。"""
    if inline():
        from cellflow.runtime.executor import fill_release_changes

        fill_release_changes(release_id)
        return
    _enqueue("release_changes", release_id, key=f"cf-changes-{release_id}")


def _enqueue(function: str, *args, key: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    async def _run():
        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        try:
            await pool.enqueue_job(function, *args, _job_id=key)
        finally:
            await pool.aclose()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(_run())
    else:
        asyncio.run(_run())
