"""Celery 应用工厂和 Worker 基础配置。"""

from celery import Celery

from etl_agent.config import Settings, get_settings


def create_celery_app(settings: Settings | None = None) -> Celery:
    """根据配置创建 Celery 实例，任务状态由 Redis 保存而非进程内存。"""
    app_settings = settings or get_settings()
    app = Celery(
        app_settings.app_name,
        broker=app_settings.celery_broker_url,
        backend=app_settings.celery_result_backend,
        include=["etl_agent.workers.tasks"],
    )
    app.conf.update(
        task_default_queue=app_settings.celery_task_default_queue,
        worker_concurrency=app_settings.celery_worker_concurrency,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        beat_schedule={
            "etl-agent-publish-outbox": {
                "task": "etl_agent.workers.publish_pending_outbox",
                "schedule": max(1, app_settings.outbox_poll_interval_seconds),
            }
        },
    )
    return app


celery_app = create_celery_app()
