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
    from arq import create_pool
    from arq.connections import RedisSettings

    async def _enqueue():
        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        try:
            await pool.enqueue_job("run_job", job_id, _job_id=f"cf-job-{job_id}")
        finally:
            await pool.aclose()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(_enqueue())
    else:
        asyncio.run(_enqueue())
