import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendConfig:
    name: str
    cloud: str
    base_url: str
    base_weight: float = 1.0


@dataclass(frozen=True)
class Settings:
    backends: list[BackendConfig]
    health_check_path: str
    probe_interval_seconds: float
    failure_threshold: int
    target_latency_ms: float
    max_error_rate: float
    max_inflight: int
    error_window_seconds: int
    request_timeout_seconds: float
    primary_preference_ratio: float
    allow_manual_override: bool


def _parse_backends() -> list[BackendConfig]:
    default_backends = [
        {
            "name": "aws-primary",
            "cloud": "aws",
            "base_url": "http://sample-app-aws.aws-sim.svc.cluster.local:8000",
            "base_weight": 1.0,
        },
        {
            "name": "gcp-secondary",
            "cloud": "gcp",
            "base_url": "http://sample-app-gcp.gcp-sim.svc.cluster.local:8000",
            "base_weight": 1.0,
        },
    ]
    payload = os.getenv("BACKENDS_JSON", json.dumps(default_backends))
    raw_backends = json.loads(payload)
    return [
        BackendConfig(
            name=item["name"],
            cloud=item["cloud"],
            base_url=item["base_url"].rstrip("/"),
            base_weight=float(item.get("base_weight", 1.0)),
        )
        for item in raw_backends
    ]


def load_settings() -> Settings:
    manual_override = os.getenv("ALLOW_MANUAL_OVERRIDE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return Settings(
        backends=_parse_backends(),
        health_check_path=os.getenv("HEALTH_CHECK_PATH", "/health"),
        probe_interval_seconds=float(os.getenv("PROBE_INTERVAL_SECONDS", "2")),
        failure_threshold=int(os.getenv("FAILURE_THRESHOLD", "2")),
        target_latency_ms=float(os.getenv("TARGET_LATENCY_MS", "250")),
        max_error_rate=float(os.getenv("MAX_ERROR_RATE", "0.05")),
        max_inflight=int(os.getenv("MAX_INFLIGHT", "100")),
        error_window_seconds=int(os.getenv("ERROR_WINDOW_SECONDS", "60")),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3")),
        primary_preference_ratio=float(os.getenv("PRIMARY_PREFERENCE_RATIO", "1.35")),
        allow_manual_override=manual_override,
    )
