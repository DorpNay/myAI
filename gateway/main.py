"""OpenAI-compatible proxy with a bounded SQLite exact-response cache."""
from __future__ import annotations
import hashlib, json, os, sqlite3, time
from contextlib import asynccontextmanager
from typing import Any
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

CACHE_PATH = os.getenv("CACHE_PATH", "/data/cache.sqlite3")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "900"))
CACHE_MAX_ENTRIES = int(os.getenv("CACHE_MAX_ENTRIES", "1000"))
CACHE_MAX_RESPONSE_BYTES = int(os.getenv("CACHE_MAX_RESPONSE_BYTES", "1000000"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
ALLOWED_ORIGINS = [v.strip() for v in os.getenv("ALLOWED_ORIGINS", "*").split(",") if v.strip()]
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "180"))

def connect_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    db = sqlite3.connect(CACHE_PATH, timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("""CREATE TABLE IF NOT EXISTS response_cache (
      cache_key TEXT PRIMARY KEY, response_json TEXT NOT NULL,
      created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
      response_bytes INTEGER NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cache_expiry ON response_cache(expires_at)")
    db.commit()
    return db

def canonical_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def is_cacheable(payload: dict[str, Any]) -> bool:
    if payload.get("stream") is True or payload.get("tools") or payload.get("tool_choice") not in (None, "none"):
        return False
    if payload.get("functions") or payload.get("function_call") not in (None, "none"):
        return False
    if payload.get("temperature", 1) > 0.2:
        return False
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    if any(not isinstance(m, dict) or m.get("tool_calls") or m.get("role") == "tool" for m in messages):
        return False
    return True

def cache_get(key: str) -> dict[str, Any] | None:
    now = int(time.time())
    with connect_db() as db:
        row = db.execute("SELECT response_json FROM response_cache WHERE cache_key=? AND expires_at>?", (key, now)).fetchone()
        db.execute("DELETE FROM response_cache WHERE expires_at<=?", (now,))
    return json.loads(row[0]) if row else None

def cache_put(key: str, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    size = len(encoded.encode("utf-8"))
    if size > CACHE_MAX_RESPONSE_BYTES:
        return
    now = int(time.time())
    with connect_db() as db:
        db.execute("""INSERT OR REPLACE INTO response_cache
          (cache_key,response_json,created_at,expires_at,response_bytes) VALUES (?,?,?,?,?)""",
          (key, encoded, now, now + CACHE_TTL_SECONDS, size))
        db.execute("DELETE FROM response_cache WHERE expires_at<=?", (now,))
        db.execute("""DELETE FROM response_cache WHERE cache_key IN
          (SELECT cache_key FROM response_cache ORDER BY created_at DESC LIMIT -1 OFFSET ?)""",
          (max(1, CACHE_MAX_ENTRIES),))

@asynccontextmanager
async def lifespan(app: FastAPI):
    with connect_db():
        pass
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS))
    yield
    await app.state.client.aclose()

app = FastAPI(title="AI Mobile Agent Cache Gateway", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])

def authorize(request: Request) -> None:
    if not GATEWAY_API_KEY:
        raise HTTPException(status_code=503, detail="Gateway API key is not configured")
    if request.headers.get("authorization", "") != f"Bearer {GATEWAY_API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    authorize(request)
    body = await request.body()
    payload: dict[str, Any] | None = None
    if request.method == "POST" and body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                payload = parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    cache_key = canonical_key(payload) if payload is not None and is_cacheable(payload) else None
    if cache_key:
        cached = cache_get(cache_key)
        if cached is not None:
            return JSONResponse(cached, headers={"X-AI-Cache": "HIT", "Cache-Control": "no-store"})
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in {"host", "authorization", "content-length", "connection"}}
    try:
        upstream = await request.app.state.client.request(
            request.method, f"{OLLAMA_BASE_URL.rstrip('/')}/v1/{path}",
            params=request.query_params, content=body if body else None, headers=headers)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Inference upstream timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Inference upstream unavailable") from exc
    response_headers = {k: v for k, v in upstream.headers.items()
                        if k.lower() in {"content-type", "cache-control"}}
    if upstream.status_code >= 400:
        return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
    content_type = upstream.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        async def stream_body():
            yield upstream.content
        return StreamingResponse(stream_body(), status_code=upstream.status_code, headers=response_headers)
    if cache_key and "application/json" in content_type:
        try:
            result = upstream.json()
            choices = result.get("choices", []) if isinstance(result, dict) else []
            if isinstance(result, dict) and choices and choices[0].get("finish_reason") != "tool_calls":
                cache_put(cache_key, result)
            return JSONResponse(result, status_code=upstream.status_code,
                headers={**response_headers, "X-AI-Cache": "MISS", "Cache-Control": "no-store"})
        except (ValueError, IndexError, AttributeError):
            pass
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)

@app.get("/admin/cache/stats")
async def cache_stats(request: Request) -> dict[str, int]:
    authorize(request)
    with connect_db() as db:
        db.execute("DELETE FROM response_cache WHERE expires_at<=?", (int(time.time()),))
        count, size = db.execute("SELECT COUNT(*), COALESCE(SUM(response_bytes),0) FROM response_cache").fetchone()
    return {"entries": int(count), "response_bytes": int(size), "ttl_seconds": CACHE_TTL_SECONDS}
