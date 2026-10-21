"""Optional operational export; no memory content or raw identities cross here."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import queue
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


_LOGGER = logging.getLogger(__name__)
_LANES = {"primary", "formation", "feedback", "recollection"}
_STATUSES = {"requested", "no_change", "produced", "input_budget", "output_budget",
             "rejected_format", "rejected_qualification", "rejected_material",
             "no_eligible_material", "model_failure"}
_ACTIVE_WINDOW = ContextVar("memory_worker_telemetry", default=None)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _event(kind, body):
    return {"id": str(uuid4()), "timestamp": _now(), "type": kind, "body": body}


@dataclass
class _Window:
    trace_id: str
    started: str
    metadata: dict
    events: list = field(default_factory=list)
    result_bytes: int = 0
    diagnostics: list = field(default_factory=list)


def record_diagnostic(lane: str, status: str, count: int) -> None:
    window = _ACTIVE_WINDOW.get()
    if window is not None and lane in _LANES and status in _STATUSES and type(count) is int and count >= 0 and len(window.diagnostics) < 128:
        window.diagnostics.append({"lane": lane, "status": status, "count": count})


class LangfuseTelemetry:
    """One trace per Worker RPC attempt, one generation per actual model call.

    A Worker result is a proposal; only Core can qualify and commit it.
    The adapter is disabled by default and intentionally exports no prompts.
    """

    def __init__(self, host, public_key, secret_key, model, *, sender=None):
        self._host = host.rstrip("/")
        self._authorization = "Basic " + base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._model = model
        self._sender = sender or self._post
        self._queue = queue.Queue(maxsize=32)
        self._closed = threading.Event()
        self._shutdown_deadline = 0.0
        self._thread = threading.Thread(target=self._export, name="memory-telemetry", daemon=True)
        self._thread.start()

    @classmethod
    def from_environment(cls, environment=os.environ, *, model=None):
        if environment.get("MEMORY_WORKER_LANGFUSE_ENABLED", "").lower() != "true":
            return None
        host = environment.get("LANGFUSE_HOST", "")
        public = environment.get("LANGFUSE_PUBLIC_KEY", "")
        secret = environment.get("LANGFUSE_SECRET_KEY", "")
        try:
            parsed = urlsplit(host)
        except ValueError:
            _LOGGER.warning("memory_worker lane=telemetry status=configuration_missing")
            return None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or not public or not secret:
            _LOGGER.warning("memory_worker lane=telemetry status=configuration_missing")
            return None
        return cls(host, public, secret, model or environment.get("MEMORY_WORKER_OPENAI_MODEL", ""))

    @asynccontextmanager
    async def window(self, request):
        window = _Window(uuid4().hex, _now(), {
            "job_hash": hashlib.sha256(request.job_ref.encode()).hexdigest(),
            "target_count": len(request.allowed_target_refs),
            "basis_count": len(request.allowed_basis_refs),
            "episode_count": len(request.evidence.episodes),
            "outcome_count": len(request.evidence.outcomes),
        })
        token = _ACTIVE_WINDOW.set(window)
        status = "completed"
        try:
            yield window
        except BaseException:
            status = "failed"
            raise
        finally:
            _ACTIVE_WINDOW.reset(token)
            metadata = {**window.metadata, "status": status,
                        "diagnostics": window.diagnostics,
                        "worker_result": "uncommitted_proposal" if window.result_bytes else "no_change"}
            if status == "failed":
                metadata["worker_result"] = "failed"
            trace = _event("trace-create", {
                "id": window.trace_id, "name": "memory.consolidation",
                "timestamp": window.started, "metadata": metadata,
                "tags": ["memory-core", "background"],
            })
            span = _event("span-create", {
                "id": str(uuid4()), "traceId": window.trace_id,
                "name": "memory.worker", "startTime": window.started,
                "endTime": _now(), "level": "ERROR" if status == "failed" else "DEFAULT",
                "metadata": {"status": status, "result_bytes": window.result_bytes},
            })
            if not self._closed.is_set():
                try:
                    self._queue.put_nowait([trace, span, *window.events])
                except queue.Full:
                    _LOGGER.warning("memory_worker lane=telemetry status=telemetry_dropped")

    def _export(self):
        while not self._closed.is_set() or (not self._queue.empty() and time.monotonic() < self._shutdown_deadline):
            try:
                batch = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._sender(batch)
            except Exception:
                _LOGGER.warning("memory_worker lane=telemetry status=telemetry_unavailable")
            finally:
                self._queue.task_done()

    def close(self, timeout=1):
        """Best-effort drain with bounded shutdown; exporter never holds up RPCs.

        One daemon thread and a bounded queue keep unavailable telemetry from
        occupying the model's executor or accumulating unbounded requests.
        """
        self._shutdown_deadline = time.monotonic() + timeout
        self._closed.set()
        self._thread.join(timeout=timeout)

    async def complete(self, model, text, lane):
        window = _ACTIVE_WINDOW.get()
        if window is None:
            return await model.complete(text)
        started = _now()
        status = "completed"
        result_bytes = 0
        try:
            result = await model.complete(text)
            if not isinstance(result, str):
                status = "invalid_result"
            else:
                result_bytes = len(result.encode("utf-8"))
            return result
        except BaseException:
            status = "failed"
            raise
        finally:
            window.events.append(_event("generation-create", {
                "id": str(uuid4()), "traceId": window.trace_id,
                "name": "memory.consolidation." + (lane if lane in _LANES else "other"),
                "model": self._model, "startTime": started, "endTime": _now(),
                "level": "DEFAULT" if status == "completed" else "ERROR",
                "metadata": {"status": status, "input_bytes": len(text.encode("utf-8")),
                             "output_bytes": result_bytes},
            }))

    def _post(self, batch):
        request = Request(self._host + "/api/public/ingestion", data=json.dumps({"batch": batch}).encode(),
                          headers={"Authorization": self._authorization, "Content-Type": "application/json"})
        with urlopen(request, timeout=2) as response:
            result = json.load(response)
        # Ingestion can return HTTP 207 with a partial failure.
        if result.get("errors"):
            raise RuntimeError("telemetry ingestion failed")
