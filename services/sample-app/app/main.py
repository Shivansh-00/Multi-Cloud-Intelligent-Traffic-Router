import asyncio
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app


SERVICE_NAME = os.getenv("SERVICE_NAME", "traffic-demo-api")
CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", "local")
REGION = os.getenv("REGION", "local")
VERSION = os.getenv("VERSION", "1.0.0")

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests served by the application",
    ["method", "path", "status_code", "cloud_provider"],
)
REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "Application request latency",
    ["method", "path", "cloud_provider"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
INFLIGHT_REQUESTS = Gauge(
    "http_inflight_requests",
    "Current inflight application requests",
    ["cloud_provider"],
)
APP_INFO = Gauge(
    "traffic_demo_app_info",
    "Static app metadata",
    ["service_name", "cloud_provider", "region", "version", "hostname"],
)


def build_health_payload() -> dict:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "cloud_provider": CLOUD_PROVIDER,
        "region": REGION,
        "version": VERSION,
        "hostname": socket.gethostname(),
        "timestamp": time.time(),
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    APP_INFO.labels(
        service_name=SERVICE_NAME,
        cloud_provider=CLOUD_PROVIDER,
        region=REGION,
        version=VERSION,
        hostname=socket.gethostname(),
    ).set(1)
    yield


app = FastAPI(title="Traffic Demo App", version=VERSION, lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    route_path = request.url.path
    start_time = time.perf_counter()
    INFLIGHT_REQUESTS.labels(cloud_provider=CLOUD_PROVIDER).inc()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed = time.perf_counter() - start_time
        REQUEST_LATENCY.labels(
            method=request.method,
            path=route_path,
            cloud_provider=CLOUD_PROVIDER,
        ).observe(elapsed)
        REQUEST_COUNT.labels(
            method=request.method,
            path=route_path,
            status_code=str(status_code),
            cloud_provider=CLOUD_PROVIDER,
        ).inc()
        INFLIGHT_REQUESTS.labels(cloud_provider=CLOUD_PROVIDER).dec()


@app.get("/")
async def root() -> dict:
    return {
        "message": "request served",
        "request_id": str(uuid.uuid4()),
        **build_health_payload(),
    }


@app.get("/health")
async def health() -> dict:
    return build_health_payload()


@app.get("/api/echo")
async def echo(delay_ms: int = 0, payload_size: int = 32) -> dict:
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)

    payload = "x" * min(max(payload_size, 1), 4096)
    return {
        "echo": payload,
        **build_health_payload(),
    }


def _run_cpu_work(work_units: int) -> int:
    total = 0
    for value in range(max(work_units, 1)):
        total += (value * 31) % 17
    return total


@app.get("/api/process")
async def process(work_units: int = 15000) -> dict:
    total = await asyncio.to_thread(_run_cpu_work, work_units)
    return {
        "work_units": work_units,
        "checksum": total,
        **build_health_payload(),
    }


@app.get("/ready")
async def ready() -> Response:
    return Response(status_code=204)
