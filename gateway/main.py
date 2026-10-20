"""OpenAI-compatible Ollama proxy with a bounded SQLite exact-response cache."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

CACHE_PATH = os.getenv("CACHE_PATH", "/data/cache.sqlite3")
CACHE_TTL_SECONDS = max(1, int(os.getenv("CACHE_TTL_SECONDS", "900")))
CACHE_MAX_ENTRIES = max(1, int(os.getenv("CACHE_MAX_ENTRIES", "1000")))
CACHE_MAX_RESPONSE_BYTES = max(1024, int(os.getenv("CACHE_MAX_RESPONSE_BYTES", "1000000")))
MAX_REQUEST_BYTES = max(1024, int(os.getenv("MAX_REQUEST_BYTES", "4000000")))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
ALLOWED_ORIGINS = [
    value.strip()
    for value in os.getenv(
        "ALLOWED_ORIGINS", "https://replace-with-your-app-origin.invalid"
    ).split(",")
    if value.strip()
]
UPSTREAM_TIMEOUT_SECONDS = max(1.0, float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "180")))

if not ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS:
    raise RuntimeError(
        "ALLOWED_ORIGINS must contain exact origins; wildcard CORS is disabled"
    )


def connect_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    db = sqlite3.connect(CACHE_PATH, timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute(
        """CREATE TABLE IF NOT EXISTS response_cache (
          cache_key TEXT PRIMARY KEY, response_json TEXT NOT NULL,
          created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
          response_bytes INTEGER NOT NULL)"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_cache_expiry ON response_cache(expires_at)"
    )
    db.commit()
    return db


def canonical_key(
    payload: dict[str, Any],
    *,
    method: str = "POST",
    path: str = "chat/completions",
    query: str = "",
) -> str:
    """Hash the canonical payload together with its HTTP route identity."""
    envelope = {
        "method": method.upper(),
        "path": path.strip("/"),
        "query": query,
        "payload": payload,
    }
    canonical = json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_cacheable(payload: dict[str, Any]) -> bool:
    """Return true only for conservative, non-streaming chat requests."""
    if payload.get("stream") is True:
        return False
    if payload.get("tools") or payload.get("functions"):
        return False
    if payload.get("tool_choice") not in (None, "none"):
        return False
    if payload.get("function_call") not in (None, "none"):
        return False

    # The caller must explicitly set a conservative temperature; implicit provider
    # defaults may be stochastic and are not safe for exact-response reuse.
    temperature = payload.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        return False
    if not 0 <= temperature <= 0.2:
        return False

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    for message in messages:
        if not isinstance(message, dict):
            return False
        if message.get("role") == "tool" or message.get("tool_calls"):
            return False
        if message.get("function_call"):
            return False

        content = message.get("content")
        if isinstance(content, list):
            # Do not persist image/audio/video or other multimodal requests by default.
            if any(
                isinstance(part, dict)
                and part.get("type") in {
                    "image_url", "image", "input_audio", "audio", "video"
                }
                for part in content
            ):
                return False
    return True


def should_bypass_cache(
    payload: dict[str, Any] | None,
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
) -> bool:
    """Keep cache policy explicit and allow clients to force a live response."""
    if method.upper() != "POST" or path.strip("/") != "chat/completions":
        return True
    if payload is None or not is_cacheable(payload):
        return True

    bypass_header = headers.get("x-ai-cache-bypass", "").strip().lower()
    if bypass_header in {"1", "true", "yes", "on"}:
        return True

    cache_control = {
        directive.strip().lower().split("=", 1)[0]
        for directive in headers.get("cache-control", "").split(",")
        if directive.strip()
    }
    return bool({"no-cache", "no-store"} & cache_control)


def cache_get(key: str) -> dict[str, Any] | None:
    now = int(time.time())
    with connect_db() as db:
        row = db.execute(
            "SELECT response_json FROM response_cache "
            "WHERE cache_key=? AND expires_at>?",
            (key, now),
        ).fetchone()
        db.execute("DELETE FROM response_cache WHERE expires_at<=?", (now,))
    return json.loads(row[0]) if row else None


def cache_put(key: str, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    size = len(encoded.encode("utf-8"))
    if size > CACHE_MAX_RESPONSE_BYTES:
        return

    now = int(time.time())
    with connect_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO response_cache
               (cache_key,response_json,created_at,expires_at,response_bytes)
               VALUES (?,?,?,?,?)""",
            (key, encoded, now, now + CACHE_TTL_SECONDS, size),
        )
        db.execute("DELETE FROM response_cache WHERE expires_at<=?", (now,))
        db.execute(
            """DELETE FROM response_cache WHERE cache_key IN (
                 SELECT cache_key FROM response_cache
                 ORDER BY created_at DESC LIMIT -1 OFFSET ?
               )""",
            (CACHE_MAX_ENTRIES,),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    with connect_db():
        pass
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS)
    )
    yield
    await app.state.client.aclose()


app = FastAPI(
    title="AI Mobile Agent Cache Gateway",
    version="1.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Authorization", "Content-Type", "Cache-Control", "X-AI-Cache-Bypass"
    ],
)


def authorize(request: Request) -> None:
    if not GATEWAY_API_KEY or GATEWAY_API_KEY == "replace-with-a-random-secret":
        raise HTTPException(
            status_code=503, detail="Gateway API key is not configured"
        )
    if request.headers.get("authorization", "") != f"Bearer {GATEWAY_API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid API key")


def upstream_headers(upstream: httpx.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() in {"content-type", "cache-control"}
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route(
    "/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
)
async def proxy(path: str, request: Request) -> Response:
    authorize(request)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Request body is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc

    # Enforce the limit while consuming the ASGI stream instead of buffering an
    # arbitrarily large body and checking its size afterwards.
    body_buffer = bytearray()
    async for chunk in request.stream():
        body_buffer.extend(chunk)
        if len(body_buffer) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Request body is too large")
    body = bytes(body_buffer)

    payload: dict[str, Any] | None = None
    if request.method == "POST" and body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                payload = parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    bypass = should_bypass_cache(
        payload,
        method=request.method,
        path=path,
        headers=request.headers,
    )
    cache_key = None
    if not bypass and payload is not None:
        cache_key = canonical_key(
            payload,
            method=request.method,
            path=path,
            query=request.url.query,
        )
        cached = cache_get(cache_key)
        if cached is not None:
            return JSONResponse(
                cached,
                headers={
                    "X-AI-Cache": "HIT",
                    "Cache-Control": "no-store",
                },
            )

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower()
        not in {"host", "authorization", "content-length", "connection"}
    }
    upstream_url = f"{OLLAMA_BASE_URL.rstrip('/')}/v1/{path}"

    # Preserve real token-by-token SSE delivery. Do not buffer a stream into RAM.
    if payload is not None and payload.get("stream") is True:
        client = request.app.state.client
        upstream_request = client.build_request(
            request.method,
            upstream_url,
            params=request.query_params,
            content=body if body else None,
            headers=headers,
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=504, detail="Inference upstream timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail="Inference upstream unavailable"
            ) from exc

        response_headers = upstream_headers(upstream)
        response_headers["Cache-Control"] = "no-store"
        response_headers["X-AI-Cache"] = "BYPASS"

        if upstream.status_code >= 400:
            try:
                content = await upstream.aread()
            finally:
                await upstream.aclose()
            return Response(
                content=content,
                status_code=upstream.status_code,
                headers=response_headers,
            )

        async def stream_body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    try:
        upstream = await request.app.state.client.request(
            request.method,
            upstream_url,
            params=request.query_params,
            content=body if body else None,
            headers=headers,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504, detail="Inference upstream timed out"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Inference upstream unavailable"
        ) from exc

    response_headers = upstream_headers(upstream)
    response_headers["Cache-Control"] = "no-store"

    if upstream.status_code >= 400:
        response_headers["X-AI-Cache"] = "BYPASS"
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    response_headers["X-AI-Cache"] = "MISS" if cache_key else "BYPASS"
    content_type = upstream.headers.get("content-type", "")
    if cache_key and "application/json" in content_type:
        try:
            result = upstream.json()
            choices = result.get("choices", []) if isinstance(result, dict) else []
            # Cache only complete, ordinary answers, never truncated or tool-call output.
            if (
                isinstance(choices, list)
                and choices
                and isinstance(choices[0], dict)
                and choices[0].get("finish_reason") == "stop"
            ):
                cache_put(cache_key, result)
            return JSONResponse(
                result,
                status_code=upstream.status_code,
                headers=response_headers,
            )
        except (ValueError, TypeError):
            pass

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )


@app.get("/admin/cache/stats")
async def cache_stats(request: Request) -> dict[str, int]:
    authorize(request)
    with connect_db() as db:
        db.execute(
            "DELETE FROM response_cache WHERE expires_at<=?", (int(time.time()),)
        )
        count, size = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(response_bytes),0) FROM response_cache"
        ).fetchone()
    return {
        "entries": int(count),
        "response_bytes": int(size),
        "ttl_seconds": CACHE_TTL_SECONDS,
    }
