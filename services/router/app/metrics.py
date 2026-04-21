from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, REGISTRY


class RouterMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        registry = registry or REGISTRY
        self.backend_health = Gauge(
            "router_backend_health",
            "Backend health state as evaluated by the router",
            ["backend", "cloud"],
            registry=registry,
        )
        self.backend_latency_ms = Gauge(
            "router_backend_latency_ms",
            "Observed backend latency in milliseconds",
            ["backend", "cloud", "source"],
            registry=registry,
        )
        self.backend_error_rate = Gauge(
            "router_backend_error_rate",
            "Rolling backend error rate",
            ["backend", "cloud"],
            registry=registry,
        )
        self.backend_inflight = Gauge(
            "router_backend_inflight_requests",
            "Current router inflight requests per backend",
            ["backend", "cloud"],
            registry=registry,
        )
        self.backend_score = Gauge(
            "router_backend_score",
            "Decision engine score per backend",
            ["backend", "cloud"],
            registry=registry,
        )
        self.active_backend = Gauge(
            "router_active_backend",
            "Marks the backend currently considered primary by the router",
            ["backend", "cloud"],
            registry=registry,
        )
        self.proxy_requests = Counter(
            "router_proxy_requests_total",
            "Requests proxied by the router",
            ["backend", "cloud", "status_code", "outcome"],
            registry=registry,
        )
        self.proxy_latency = Histogram(
            "router_proxy_request_latency_seconds",
            "Latency observed by the router while proxying requests",
            ["backend", "cloud"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
            registry=registry,
        )
        self.failovers = Counter(
            "router_failover_events_total",
            "Failover transitions triggered by the decision engine",
            ["from_backend", "to_backend", "reason"],
            registry=registry,
        )

    def set_active_backend(self, active_backend_name: str | None, states: list[dict]) -> None:
        for state in states:
            self.active_backend.labels(state["name"], state["cloud"]).set(
                1 if state["name"] == active_backend_name else 0
            )

    def update_backend_state(self, state: dict) -> None:
        backend = state["name"]
        cloud = state["cloud"]
        self.backend_health.labels(backend, cloud).set(1 if state["healthy"] else 0)
        self.backend_latency_ms.labels(backend, cloud, "probe").set(state["probe_latency_ms"])
        self.backend_latency_ms.labels(backend, cloud, "ewma").set(state["ewma_latency_ms"])
        self.backend_error_rate.labels(backend, cloud).set(state["error_rate"])
        self.backend_inflight.labels(backend, cloud).set(state["inflight_requests"])
        self.backend_score.labels(backend, cloud).set(state["score"])

    def record_request(self, backend: str, cloud: str, status_code: int, success: bool, latency_s: float) -> None:
        outcome = "success" if success else "failure"
        self.proxy_requests.labels(backend, cloud, str(status_code), outcome).inc()
        self.proxy_latency.labels(backend, cloud).observe(latency_s)

    def record_failover(self, from_backend: str, to_backend: str, reason: str) -> None:
        self.failovers.labels(from_backend, to_backend, reason).inc()


router_metrics = RouterMetrics()
