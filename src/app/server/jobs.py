from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class JobRecord:
    job_id: str
    service_id: str
    project_id: str
    status: str = "queued"
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    subscribers: List[asyncio.Queue] = field(default_factory=list)

class JobStore:
    def __init__(self):
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, job_id: str, service_id: str, project_id: str, inputs: Dict[str, Any]) -> JobRecord:
        async with self._lock:
            rec = JobRecord(job_id=job_id, service_id=service_id, project_id=project_id, inputs=inputs)
            self._jobs[job_id] = rec
            return rec

    def get(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    async def update_status(self, job_id: str, status: str, error: Optional[Dict[str, Any]] = None):
        async with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            rec.status = status
            rec.updated_at = time.time()
            if error is not None:
                rec.error = error

    async def set_outputs(self, job_id: str, outputs: Dict[str, Any]):
        async with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            rec.outputs = outputs
            rec.updated_at = time.time()

    async def append_event(self, job_id: str, event_type: str, payload: Dict[str, Any]):
        async with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            evt = {"type": event_type, "payload": payload, "ts": time.time()}
            rec.events.append(evt)
            # fan out to subscribers
            for q in list(rec.subscribers):
                try:
                    q.put_nowait(evt)
                except Exception:
                    pass

    async def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return q
            rec.subscribers.append(q)
        return q

    async def unsubscribe(self, job_id: str, q: asyncio.Queue):
        async with self._lock:
            rec = self._jobs.get(job_id)
            if not rec:
                return
            if q in rec.subscribers:
                rec.subscribers.remove(q)
