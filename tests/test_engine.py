import time

from prometheus_client import CollectorRegistry

from services.router.app.config import BackendConfig, Settings
from services.router.app.engine import NoHealthyBackendError, RoutingEngine
from services.router.app.metrics import RouterMetrics


def build_settings() -> Settings:
    return Settings(
        backends=[
            BackendConfig(name="aws-primary", cloud="aws", base_url="http://aws.example", base_weight=1.0),
            BackendConfig(name="gcp-secondary", cloud="gcp", base_url="http://gcp.example", base_weight=1.0),
        ],
        health_check_path="/health",
        probe_interval_seconds=2.0,
        failure_threshold=2,
        target_latency_ms=250,
        max_error_rate=0.05,
        max_inflight=100,
        error_window_seconds=60,
        request_timeout_seconds=3.0,
        primary_preference_ratio=1.2,
        allow_manual_override=True,
    )


def test_prefers_healthy_backend():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]

    aws_backend.last_probe_ok = True
    aws_backend.healthy = True
    aws_backend.last_probe_latency_ms = 40

    gcp_backend.last_probe_ok = False
    gcp_backend.healthy = False
    gcp_backend.last_probe_latency_ms = 2000

    selected = engine.select_backend()

    assert selected.config.name == "aws-primary"
    assert engine.active_backend_name == "aws-primary"


def test_failover_records_event_when_primary_breaks():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]

    aws_backend.last_probe_ok = True
    aws_backend.healthy = True
    aws_backend.last_probe_latency_ms = 30
    gcp_backend.last_probe_ok = True
    gcp_backend.healthy = True
    gcp_backend.last_probe_latency_ms = 80
    gcp_backend.inflight_requests = 50

    first = engine.select_backend()
    assert first.config.name == "aws-primary"

    aws_backend.last_probe_ok = False
    aws_backend.healthy = False
    second = engine.select_backend()

    assert second.config.name == "gcp-secondary"
    assert engine.active_backend_name == "gcp-secondary"
    assert engine.events[0]["from_backend"] == "aws-primary"
    assert engine.events[0]["to_backend"] == "gcp-secondary"


def test_excludes_backend_with_latency_above_threshold():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]

    aws_backend.last_probe_ok = True
    aws_backend.healthy = True
    aws_backend.last_probe_latency_ms = 500

    gcp_backend.last_probe_ok = True
    gcp_backend.healthy = True
    gcp_backend.last_probe_latency_ms = 40

    selected = engine.select_backend()

    assert selected.config.name == "gcp-secondary"


def test_excludes_backend_with_error_rate_above_threshold():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]
    now = time.time()

    aws_backend.last_probe_ok = True
    aws_backend.healthy = True
    aws_backend.last_probe_latency_ms = 20
    aws_backend.recent_outcomes.extend(
        [
            (now - 1, False),
            (now - 2, False),
            (now - 3, False),
            (now - 4, False),
            (now - 5, False),
            (now - 6, True),
        ]
    )

    gcp_backend.last_probe_ok = True
    gcp_backend.healthy = True
    gcp_backend.last_probe_latency_ms = 30
    gcp_backend.recent_outcomes.extend(
        [
            (now - 1, True),
            (now - 2, True),
            (now - 3, True),
        ]
    )

    selected = engine.select_backend()

    assert selected.config.name == "gcp-secondary"


def test_raises_when_no_backend_satisfies_policy():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]

    aws_backend.last_probe_ok = False
    aws_backend.healthy = False
    gcp_backend.last_probe_ok = False
    gcp_backend.healthy = False

    try:
        engine.select_backend()
        assert False, "Expected NoHealthyBackendError"
    except NoHealthyBackendError as exc:
        assert "aws-primary" in exc.reasons
        assert "gcp-secondary" in exc.reasons


def test_manual_override_forces_selected_backend_when_healthy():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]

    aws_backend.last_probe_ok = True
    aws_backend.healthy = True
    aws_backend.last_probe_latency_ms = 120
    gcp_backend.last_probe_ok = True
    gcp_backend.healthy = True
    gcp_backend.last_probe_latency_ms = 20

    engine.set_manual_override("aws-primary")
    selected = engine.select_backend()

    assert selected.config.name == "aws-primary"
    assert engine.manual_override_backend_name == "aws-primary"


def test_manual_override_falls_back_if_selected_backend_unhealthy():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]

    aws_backend.last_probe_ok = False
    aws_backend.healthy = False
    aws_backend.last_probe_latency_ms = 1000
    gcp_backend.last_probe_ok = True
    gcp_backend.healthy = True
    gcp_backend.last_probe_latency_ms = 30

    engine.set_manual_override("aws-primary")
    selected = engine.select_backend()

    assert selected.config.name == "gcp-secondary"


def test_manual_override_strict_forces_selected_backend_even_if_unhealthy():
    engine = RoutingEngine(build_settings(), RouterMetrics(registry=CollectorRegistry()))
    aws_backend = engine.backends["aws-primary"]
    gcp_backend = engine.backends["gcp-secondary"]

    aws_backend.last_probe_ok = False
    aws_backend.healthy = False
    aws_backend.last_probe_latency_ms = 1200
    gcp_backend.last_probe_ok = True
    gcp_backend.healthy = True
    gcp_backend.last_probe_latency_ms = 20

    engine.set_manual_override("aws-primary", strict=True)
    selected = engine.select_backend()

    assert selected.config.name == "aws-primary"
    assert engine.manual_override_strict is True

