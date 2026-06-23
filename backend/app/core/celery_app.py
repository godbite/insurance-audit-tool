"""
Celery application with two queues:
  - extraction  : LLM-bound, slow, horizontally scalable
  - decisioning : CPU-bound, fast, deterministic

Scale independently at 10x load by adding more extraction workers.
"""
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "plum_claims",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.pipeline"],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Queues — separate LLM work from fast decisioning
    task_default_queue="decisioning",
    task_routes={
        "app.tasks.pipeline.classify_documents_task": {"queue": "extraction"},
        "app.tasks.pipeline.extract_documents_task": {"queue": "extraction"},
        "app.tasks.pipeline.verify_documents_task": {"queue": "decisioning"},
        "app.tasks.pipeline.consistency_check_task": {"queue": "decisioning"},
        "app.tasks.pipeline.make_decision_task": {"queue": "decisioning"},
        "app.tasks.pipeline.run_claim_pipeline": {"queue": "decisioning"},
    },
    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # don't prefetch — extraction tasks are slow
    task_reject_on_worker_lost=True,
    # Retry on transient failures
    task_max_retries=3,
    task_default_retry_delay=5,
    # Result TTL (keep for 24h for trace replay)
    result_expires=86400,
)
