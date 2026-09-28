"""arq Worker：执行任务、投递回调、崩溃恢复、数据清理。

启动：arq cellflow.runtime.worker.WorkerSettings
"""

from __future__ import annotations

import asyncio

from arq import cron
from arq.connections import RedisSettings

from cellflow.config import get_settings


async def run_job(ctx, job_id: int) -> str:
    from cellflow.runtime.executor import run_job as _run

    return await asyncio.to_thread(_run, job_id)


async def release_changes(ctx, release_id: int) -> None:
    from cellflow.runtime.executor import fill_release_changes

    await asyncio.to_thread(fill_release_changes, release_id)


async def deliver_callbacks(ctx) -> int:
    from cellflow.services.callbacks import deliver_due

    return await asyncio.to_thread(deliver_due)


async def recover(ctx) -> list:
    from cellflow.runtime.executor import recover_writing

    return await asyncio.to_thread(recover_writing)


async def cleanup(ctx) -> dict:
    from cellflow.services.cleanup import run_cleanup

    return await asyncio.to_thread(run_cleanup)


async def startup(ctx) -> None:
    await recover(ctx)


class WorkerSettings:
    functions = [run_job, release_changes]
    cron_jobs = [
        cron(deliver_callbacks, second={0, 10, 20, 30, 40, 50}),
        cron(recover, minute=set(range(0, 60, 5)), second=5),
        cron(cleanup, hour=3, minute=17),
    ]
    on_startup = startup
    max_jobs = 4
    job_timeout = 1800
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
