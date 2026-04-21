import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from .config import load_settings
from .engine import NoHealthyBackendError, RoutingEngine
from .metrics import router_metrics


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


class OverrideRequest(BaseModel):
    backend: str
    strict: bool = False


class OverrideResponse(BaseModel):
    manual_override_backend: str | None
    manual_override_strict: bool
    active_backend: str | None


CONSOLE_HTML_PATH = Path(__file__).parent / "static" / "console.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    engine = RoutingEngine(settings=settings, metrics=router_metrics)
    proxy_client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
    await engine.start()
    app.state.engine = engine
    app.state.proxy_client = proxy_client
    app.state.settings = settings
    yield
    await proxy_client.aclose()
    await engine.stop()


app = FastAPI(title="Intelligent Traffic Router", version="1.0.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/health")
async def health(request: Request) -> dict:
    engine: RoutingEngine = request.app.state.engine
    status = engine.status()
    healthy_backends = sum(1 for backend in status["backends"] if backend["healthy"])
    return {
        "status": "ok" if healthy_backends > 0 else "degraded",
        "healthy_backends": healthy_backends,
        "active_backend": status["active_backend"],
    }


@app.get("/router/status")
async def router_status(request: Request) -> dict:
    engine: RoutingEngine = request.app.state.engine
    return engine.status()


@app.get("/", include_in_schema=False)
async def console() -> Response:
    return Response(content=CONSOLE_HTML_PATH.read_text(encoding="utf-8"), media_type="text/html")


@app.get("/router/backends")
async def router_backends(request: Request) -> dict:
    engine: RoutingEngine = request.app.state.engine
    return {"backends": engine.status()["backends"]}


@app.post("/router/override", response_model=OverrideResponse)
async def set_router_override(payload: OverrideRequest, request: Request) -> OverrideResponse:
    engine: RoutingEngine = request.app.state.engine
    settings = request.app.state.settings
    if not settings.allow_manual_override:
        raise HTTPException(status_code=403, detail="manual override is disabled")

    try:
        engine.set_manual_override(payload.backend, strict=payload.strict)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    status = engine.status()
    return OverrideResponse(
        manual_override_backend=status["manual_override_backend"],
        manual_override_strict=status["manual_override_strict"],
        active_backend=status["active_backend"],
    )


@app.delete("/router/override", response_model=OverrideResponse)
async def clear_router_override(request: Request) -> OverrideResponse:
    engine: RoutingEngine = request.app.state.engine
    engine.clear_manual_override()
    status = engine.status()
    return OverrideResponse(
        manual_override_backend=status["manual_override_backend"],
        manual_override_strict=status["manual_override_strict"],
        active_backend=status["active_backend"],
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_request(path: str, request: Request) -> Response:
    engine: RoutingEngine = request.app.state.engine
    proxy_client: httpx.AsyncClient = request.app.state.proxy_client

    try:
        backend = engine.select_backend()
    except NoHealthyBackendError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": "no_healthy_backend",
                "reasons": exc.reasons,
            },
        )

    target_url = backend.config.base_url
    if path:
        target_url = f"{target_url}/{path}"

    outbound_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }

    engine.request_started(backend.config.name)
    started_at = time.perf_counter()
    try:
        upstream_response = await proxy_client.request(
            request.method,
            target_url,
            headers=outbound_headers,
            params=request.query_params,
            content=await request.body(),
        )
        latency_ms = (time.perf_counter() - started_at) * 1000
        success = upstream_response.status_code < 500
        engine.request_finished(
            backend_name=backend.config.name,
            success=success,
            latency_ms=latency_ms,
            status_code=upstream_response.status_code,
        )
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers={
                key: value
                for key, value in upstream_response.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            },
            media_type=upstream_response.headers.get("content-type"),
        )
    except httpx.HTTPError:
        latency_ms = (time.perf_counter() - started_at) * 1000
        engine.request_finished(
            backend_name=backend.config.name,
            success=False,
            latency_ms=latency_ms,
            status_code=503,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "upstream_unavailable",
                "backend": backend.config.name,
                "cloud": backend.config.cloud,
            },
        )
