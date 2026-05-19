from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from swallow.core.config import IngestConfig
from swallow.core.queue_store import QueueStore
from swallow.core.runner import IngestRunner, PreparedIngestJob
from swallow.sdk.result_mapper import map_result


class QueueWorker:
    def __init__(
        self,
        store_root: Path | str = ".",
        *,
        config: IngestConfig | None = None,
        worker_id: str | None = None,
        lease_seconds: float = 300.0,
        heartbeat_interval: float = 30.0,
        poll_interval: float = 1.0,
    ) -> None:
        self.store_root = Path(store_root)
        self.config = config or IngestConfig()
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = heartbeat_interval
        self.poll_interval = poll_interval
        self.queue = QueueStore(self.store_root)

    def run_once(self) -> dict[str, Any]:
        item = self.queue.claim_next(worker_id=self.worker_id, lease_seconds=self.lease_seconds)
        if item is None:
            return {"status": "idle", "worker_id": self.worker_id}
        return self._run_item(item.job_id)

    def run_forever(self) -> None:
        while True:
            result = self.run_once()
            if result["status"] == "idle":
                time.sleep(self.poll_interval)

    def _run_item(self, job_id: str) -> dict[str, Any]:
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat_loop, args=(job_id, stop_heartbeat), daemon=True)
        heartbeat.start()
        try:
            prepared = self.queue.read_prepared_job(job_id)
            runner = IngestRunner(store_root=self.store_root, config=self.config)
            if prepared.job.source_type != "url":
                runner._enforce_sync_size_limit(runner.raw_store.resolve_path(prepared.raw))
            result = runner.run_prepared_job(PreparedIngestJob(raw=prepared.raw, job=prepared.job))
            sdk_result = map_result(self.store_root, result.job.id, content="none")
            self.queue.complete_job(result.job.id, status=sdk_result.status)
            return {"status": sdk_result.status, "job_id": result.job.id, "worker_id": self.worker_id}
        except Exception as error:
            status = self.queue.fail_or_retry_job(job_id, error)
            return {"status": status, "job_id": job_id, "worker_id": self.worker_id, "error": type(error).__name__}
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1)

    def _heartbeat_loop(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_interval):
            self.queue.heartbeat(job_id, worker_id=self.worker_id, lease_seconds=self.lease_seconds)
