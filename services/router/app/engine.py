import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import BackendConfig, Settings
from .metrics import RouterMetrics


class NoHealthyBackendError(RuntimeError):
    def __init__(self, reasons: dict[str, list[str]]):
        self.reasons = reasons
        super().__init__("No backend satisfies routing policy")


@dataclass
class BackendRuntime:
    config: BackendConfig
    healthy: bool = True
    last_probe_ok: bool = True
    last_probe_message: str = "startup"
    last_probe_latency_ms: float = 0.0
    ewma_latency_ms: float = 0.0
    inflight_requests: int = 0
    consecutive_failures: int = 0
    total_requests: int = 0
    total_errors: int = 0
    decision_score: float = 0.0
    recent_outcomes: deque[tuple[float, bool]] = field(default_factory=deque)

    def current_error_rate(self, now: float, window_seconds: int) -> float:
        while self.recent_outcomes and now - self.recent_outcomes[0][0] > window_seconds:
            self.recent_outcomes.popleft()
        if not self.recent_outcomes:
            return 0.0
        failures = sum(1 for _, success in self.recent_outcomes if not success)
        return failures / len(self.recent_outcomes)

    def effective_latency_ms(self) -> float:
        candidates = [value for value in (self.last_probe_latency_ms, self.ewma_latency_ms) if value > 0]
        return max(candidates) if candidates else 0.0

    def to_state(self, now: float, settings: Settings) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "cloud": self.config.cloud,
            "base_url": self.config.base_url,
            "healthy": self.healthy,
            "probe_ok": self.last_probe_ok,
            "probe_message": self.last_probe_message,
            "probe_latency_ms": round(self.last_probe_latency_ms, 2),
            "ewma_latency_ms": round(self.ewma_latency_ms, 2),
            "error_rate": round(self.current_error_rate(now, settings.error_window_seconds), 4),
            "inflight_requests": self.inflight_requests,
            "consecutive_failures": self.consecutive_failures,
            "score": round(self.decision_score, 4),
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
        }


class RoutingEngine:
    def __init__(self, settings: Settings, metrics: RouterMetrics) -> None:
        self.settings = settings
        self.metrics = metrics
        self.backends: dict[str, BackendRuntime] = {
            backend.name: BackendRuntime(config=backend) for backend in settings.backends
        }
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self.active_backend_name: str | None = None
        self.manual_override_backend_name: str | None = None
        self.manual_override_strict: bool = False
        self._probe_task: asyncio.Task | None = None
        self._probe_client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._probe_client is None:
            self._probe_client = httpx.AsyncClient(timeout=self.settings.request_timeout_seconds)
        await self.probe_once()
        if self._probe_task is None:
            self._probe_task = asyncio.create_task(self._probe_loop())

    async def stop(self) -> None:
        if self._probe_task is not None:
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass
            self._probe_task = None

        if self._probe_client is not None:
            await self._probe_client.aclose()
            self._probe_client = None

    async def _probe_loop(self) -> None:
        while True:
            await self.probe_once()
            await asyncio.sleep(self.settings.probe_interval_seconds)

    async def probe_once(self) -> None:
        await asyncio.gather(*(self._probe_backend(backend) for backend in self.backends.values()))
        self._refresh_metrics()

    async def _probe_backend(self, backend: BackendRuntime) -> None:
        assert self._probe_client is not None
        health_url = f"{backend.config.base_url}{self.settings.health_check_path}"
        started_at = time.perf_counter()
        try:
            response = await self._probe_client.get(health_url)
            probe_latency_ms = (time.perf_counter() - started_at) * 1000
            backend.last_probe_latency_ms = probe_latency_ms
            if response.status_code == 200:
                backend.last_probe_ok = True
                backend.healthy = True
                backend.consecutive_failures = 0
                backend.last_probe_message = "ok"
            else:
                backend.last_probe_ok = False
                backend.consecutive_failures += 1
                backend.healthy = backend.consecutive_failures < self.settings.failure_threshold
                backend.last_probe_message = f"http_{response.status_code}"
        except Exception as exc:
            backend.last_probe_ok = False
            backend.consecutive_failures += 1
            backend.healthy = backend.consecutive_failures < self.settings.failure_threshold
            backend.last_probe_latency_ms = self.settings.request_timeout_seconds * 1000
            backend.last_probe_message = type(exc).__name__

    def request_started(self, backend_name: str) -> None:
        self.backends[backend_name].inflight_requests += 1
        self._refresh_metrics()

    def request_finished(self, backend_name: str, success: bool, latency_ms: float, status_code: int) -> None:
        backend = self.backends[backend_name]
        backend.inflight_requests = max(backend.inflight_requests - 1, 0)
        backend.total_requests += 1
        if not success:
            backend.total_errors += 1
        now = time.time()
        backend.recent_outcomes.append((now, success))
        if backend.ewma_latency_ms == 0:
            backend.ewma_latency_ms = latency_ms
        else:
            backend.ewma_latency_ms = (backend.ewma_latency_ms * 0.7) + (latency_ms * 0.3)
        self.metrics.record_request(
            backend=backend.config.name,
            cloud=backend.config.cloud,
            status_code=status_code,
            success=success,
            latency_s=latency_ms / 1000,
        )
        self._refresh_metrics()

    def select_backend(self) -> BackendRuntime:
        now = time.time()
        candidates: list[tuple[BackendRuntime, float]] = []
        reasons_by_backend: dict[str, list[str]] = {}
        for backend in self.backends.values():
            score, reasons = self._calculate_score(backend, now)
            backend.decision_score = score
            reasons_by_backend[backend.config.name] = reasons
            if score > 0:
                candidates.append((backend, score))

        self._refresh_metrics()

        if not candidates:
            if self.manual_override_backend_name and self.manual_override_strict:
                override_backend = self.backends.get(self.manual_override_backend_name)
                if override_backend:
                    self._maybe_record_failover(
                        override_backend.config.name,
                        reasons_by_backend,
                        "manual_override_strict",
                    )
                    return override_backend
            raise NoHealthyBackendError(reasons_by_backend)

        if self.manual_override_backend_name:
            override_backend = self.backends.get(self.manual_override_backend_name)
            if override_backend:
                if self.manual_override_strict:
                    self._maybe_record_failover(override_backend.config.name, reasons_by_backend, "manual_override_strict")
                    return override_backend

                if any(backend.config.name == override_backend.config.name for backend, _ in candidates):
                    self._maybe_record_failover(override_backend.config.name, reasons_by_backend, "manual_override")
                    return override_backend

        candidates.sort(key=lambda item: item[1], reverse=True)
        primary_backend = candidates[0][0]
        self._maybe_record_failover(primary_backend.config.name, reasons_by_backend)

        if len(candidates) == 1:
            return primary_backend

        if candidates[0][1] >= candidates[1][1] * self.settings.primary_preference_ratio:
            return primary_backend

        selected_backend = random.choices(
            [candidate[0] for candidate in candidates],
            weights=[candidate[1] for candidate in candidates],
            k=1,
        )[0]
        return selected_backend

    def status(self) -> dict[str, Any]:
        now = time.time()
        backends = [backend.to_state(now, self.settings) for backend in self.backends.values()]
        return {
            "active_backend": self.active_backend_name,
            "manual_override_backend": self.manual_override_backend_name,
            "manual_override_strict": self.manual_override_strict,
            "backends": backends,
            "recent_events": list(self.events),
            "thresholds": {
                "target_latency_ms": self.settings.target_latency_ms,
                "max_error_rate": self.settings.max_error_rate,
                "max_inflight": self.settings.max_inflight,
                "failure_threshold": self.settings.failure_threshold,
                "probe_interval_seconds": self.settings.probe_interval_seconds,
            },
        }

    def set_manual_override(self, backend_name: str | None, strict: bool = False) -> None:
        if backend_name is None:
            self.manual_override_backend_name = None
            self.manual_override_strict = False
            return

        if backend_name not in self.backends:
            raise ValueError(f"unknown backend: {backend_name}")

        self.manual_override_backend_name = backend_name
        self.manual_override_strict = strict

    def clear_manual_override(self) -> None:
        self.manual_override_backend_name = None
        self.manual_override_strict = False

    def _calculate_score(self, backend: BackendRuntime, now: float) -> tuple[float, list[str]]:
        reasons: list[str] = []
        error_rate = backend.current_error_rate(now, self.settings.error_window_seconds)
        effective_latency_ms = backend.effective_latency_ms()

        if not backend.last_probe_ok or not backend.healthy:
            reasons.append("health_check_failed")
        if effective_latency_ms > self.settings.target_latency_ms:
            reasons.append("latency_above_threshold")
        if error_rate > self.settings.max_error_rate:
            reasons.append("error_rate_above_threshold")
        if backend.inflight_requests > self.settings.max_inflight:
            reasons.append("load_above_threshold")

        if reasons:
            return 0.0, reasons

        latency_penalty = (
            max(0.0, effective_latency_ms - self.settings.target_latency_ms) / self.settings.target_latency_ms
            if self.settings.target_latency_ms
            else 0.0
        )
        error_penalty = error_rate / self.settings.max_error_rate if self.settings.max_error_rate else 0.0
        load_penalty = backend.inflight_requests / self.settings.max_inflight if self.settings.max_inflight else 0.0
        score = backend.config.base_weight / (1 + latency_penalty * 2 + error_penalty * 4 + load_penalty * 1.5)
        return score, []

    def _maybe_record_failover(
        self,
        next_backend_name: str,
        reasons_by_backend: dict[str, list[str]],
        forced_reason: str | None = None,
    ) -> None:
        if self.active_backend_name == next_backend_name:
            return

        previous_backend = self.active_backend_name or "none"
        previous_reasons = reasons_by_backend.get(self.active_backend_name or "", ["initial_selection"])
        reason = forced_reason or (",".join(previous_reasons) if previous_reasons else "higher_score")
        event = {
            "timestamp": time.time(),
            "from_backend": previous_backend,
            "to_backend": next_backend_name,
            "reason": reason,
        }
        self.events.appendleft(event)
        self.metrics.record_failover(previous_backend, next_backend_name, reason)
        self.active_backend_name = next_backend_name
        self._refresh_metrics()

    def _refresh_metrics(self) -> None:
        now = time.time()
        states = [backend.to_state(now, self.settings) for backend in self.backends.values()]
        for state in states:
            self.metrics.update_backend_state(state)
        self.metrics.set_active_backend(self.active_backend_name, states)
