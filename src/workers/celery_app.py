from __future__ import annotations

from celery import Celery

from src.core.config import settings

celery_app = Celery(
    "influencer_ops",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["src.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,  # 10 minutes
    task_soft_time_limit=540,  # 9 minutes
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "poll-gmail-every-minute": {
            "task": "src.workers.tasks.poll_gmail",
            "schedule": 60.0,
        },
        "check-pending-reminders": {
            "task": "src.workers.tasks.check_pending_reminders",
            "schedule": 300.0,  # Every 5 minutes
        },
        "sync-hubspot-hourly": {
            "task": "src.workers.tasks.sync_hubspot_task",
            "schedule": 3600.0,  # Every hour
        },
        "run-prospecting-campaigns": {
            "task": "src.workers.tasks.run_active_campaigns",
            "schedule": 1800.0,  # Every 30 minutes
        },
    },
)
