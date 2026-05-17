from __future__ import annotations

from abc import ABC, abstractmethod

from swallow.core.models import WorkerCapability, WorkerInput, WorkerResult


class BaseWorker(ABC):
    name: str
    version: str
    capability = WorkerCapability()

    @abstractmethod
    def can_handle(self, input: WorkerInput) -> bool:
        raise NotImplementedError

    @abstractmethod
    def run(self, input: WorkerInput) -> WorkerResult:
        raise NotImplementedError
