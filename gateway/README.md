# AI Mobile Agent — response-cache gateway

The gateway is an optional OpenAI-compatible layer between the static mobile app and Ollama. It adds no new always-on database: SQLite is stored on a named Docker volume. It is not the same thing as Ollama's model/prefix/KV cache.

## Architecture

    AI Mobile Agent
      -> HTTPS hostname on Cloudflare Tunnel
      -> cache-gateway (127.0.0.1:8787, Bearer token)
      -> Ollama bound to the Docker bridge IP on the host
      -> model inference

- Exact response cache: canonical payload + HTTP method + endpoint path + query string, SHA-256, SQLite WAL, 15-minute TTL, max 1,000 entries.
- Cache HIT only for completed, ordinary, non-streaming /v1/chat/completions answers.
- Bypass: streaming, tool/function calls, tool messages, multimodal content, temperature > 0.2 or invalid temperature, other API routes, explicit no-cache/no-store headers, X-AI-Cache-Bypass: 1.
- Dynamic/live/search requests should send X-AI-Cache-Bypass: 1 even if the request body would otherwise be cacheable.
- Cache responses include X-AI-Cache: HIT, MISS, or BYPASS and Cache-Control: no-store.
- Cache stores only finish_reason=stop, not truncated responses or tool-call outputs.
- Gateway uses a 192 MiB memory cap, half a CPU, a read-only filesystem, a non-root user, dropped Linux capabilities, and a loopback-only published port.

## Prerequisites

- Ubuntu 24.04 (or compatible Ubuntu/Debian host), Docker Engine + Compose v2.
- Ollama working on the host, with one small model installed.
- A publicly reachable HTTPS hostname in a Cloudflare-managed domain for production. Cloudflare Tunnel itself does not provide a free registered domain. A Quick Tunnel is temporary and not a production replacement.
- Access to the GitHub repository and VPS console.

## Deploy

1. See ../docs/DEPLOYMENT_RU.md for the complete Russian, beginner-friendly walkthrough. It covers account registration, secure Ollama binding, secret creation, Compose, Cloudflare Tunnel, browser settings, acceptance checks, and rollback.
2. Never publish port 11434. Prefer a native cloudflared service on the host so the Tunnel route can target http://localhost:8787 while Docker publishes the gateway only on 127.0.0.1.
3. Copy .env.example to .env. Generate GATEWAY_API_KEY with openssl rand -hex 32. Set ALLOWED_ORIGINS to the exact HTTPS origin of the deployed client (scheme + host only, no path). The placeholder origin deliberately will not authorize a real site.
4. Ensure Ollama listens on the Docker bridge gateway IP only, not 0.0.0.0 or the public IP; see the deployment guide.
5. Start and inspect:

       docker compose up -d --build
       docker compose ps
       curl --fail http://127.0.0.1:8787/health

## Test cache and authorization

Run on the VPS after editing .env:

    cd gateway
    KEY="$(grep '^GATEWAY_API_KEY=' .env | cut -d= -f2-)"
    curl -i http://127.0.0.1:8787/v1/models
    curl -i -H "Authorization: Bearer $KEY" http://127.0.0.1:8787/v1/models

The first request must return 401. The second should list the models installed in Ollama. Pick an exact model tag from that response or from ollama list.

To prove the cache, send the same low-temperature prompt twice:

    BODY='{"model":"qwen3:1.7b","messages":[{"role":"user","content":"Reply exactly CACHE-CHECK-42"}],"temperature":0}'
    for i in 1 2; do
      curl -sS -D - -o /tmp/cache-test-$i.json \
        -H "Authorization: Bearer $KEY" \
        -H "Content-Type: application/json" \
        --data "$BODY" http://127.0.0.1:8787/v1/chat/completions \
        | grep -i '^x-ai-cache:'
    done

Expected header sequence: MISS, then HIT. Change qwen3:1.7b to an exact installed model tag if needed. A tool call, streamed call, high-temperature call, incomplete response, or bypass header should not be cached.

Statistics:

    curl -sS -H "Authorization: Bearer $KEY" \
      http://127.0.0.1:8787/admin/cache/stats

## Routine operations

    docker compose logs --tail=100 cache-gateway
    docker compose ps
    free -h
    docker stats --no-stream

- docker compose restart cache-gateway restarts the process and keeps the cache.
- docker compose down stops the gateway and preserves the named cache volume.
- docker compose down -v deletes the cache volume and all cached responses.
- Observe memory under both idle and inference load. Swap is a last-resort safety net, not extra physical RAM.

## Security boundaries

- Never embed GATEWAY_API_KEY into index.html or commit .env. The app stores the key in the local browser profile; do not use an untrusted/public device or share browser storage.
- CORS is a browser control, not authentication. Bearer authentication is required on /v1 and /admin.
- Keep the admin stats endpoint behind the same bearer auth. Do not expose Ollama's port publicly.
- Do not use wildcard CORS. Use the exact app origin; use Cloudflare Access or another identity perimeter if the hostname is for more than a personal single-user app.
- The gateway does not attempt semantic cache matching. Similar words do not count as an identical request.
