# AI Mobile Agent — lightweight response-cache gateway

Optional OpenAI-compatible gateway between the static web client and Ollama. It adds an authenticated, bounded exact-request cache backed by SQLite; it does not add Redis/Valkey or another always-on database to the 8 GB VPS.

## Architecture

    AI Mobile Agent (HTTPS)
      -> Cloudflare Tunnel / HTTPS ingress
      -> cache-gateway (127.0.0.1:8787; Bearer token)
      -> Ollama (host:11434; never publish this port)
      -> model weights + Ollama runtime KV/prefix reuse

- Browser cache: client storage remains independent.
- Runtime cache: Ollama manages loaded model memory and runtime context/KV behavior.
- Gateway cache: exact request-body SHA-256 key, SQLite WAL, 15-minute default TTL, bounded to 1,000 entries.
- Excluded: streaming requests, tool/function calls, tool messages, and requests with temperature above 0.2.
- Cache keys include the entire JSON request, including model, messages, and generation options.

## Deploy on Ubuntu 24.04

1. Install Docker Compose and make sure Ollama is healthy on the host. Do not open port 11434 in UFW or the cloud firewall.
2. Copy the gateway directory to the server, then run:

       cd gateway
       cp .env.example .env
       nano .env

3. Generate a key with openssl rand -hex 32 and put it in GATEWAY_API_KEY. Replace ALLOWED_ORIGINS=* with the exact HTTPS origin of the deployed static app.
4. Start and verify:

       docker compose up -d --build
       docker compose ps
       curl --fail http://127.0.0.1:8787/health

5. In Cloudflare Tunnel, add a hostname route to http://localhost:8787. If another hostname already routes to Dify, use a separate hostname such as llm.example.com. Do not expose the Ollama port.
6. In AI Mobile Agent settings set Base URL to https://YOUR-GATEWAY-HOST/v1, API Key to GATEWAY_API_KEY, and Model to the exact tag installed in Ollama.
7. Send the same low-temperature, non-streaming prompt twice. Inspect response headers: the first should be X-AI-Cache: MISS and the second X-AI-Cache: HIT. Authenticated stats are at /admin/cache/stats.

## Operations and tests

       docker compose logs --tail=100 cache-gateway
       docker compose restart cache-gateway
       docker compose down       # keeps named cache volume
       docker compose down -v    # WARNING: deletes the cache volume

Install dependencies and run tests from repository root:

       python -m pip install -r gateway/requirements.txt
       python -m unittest discover -s gateway/tests -v

## Constraints

- This is exact-match caching, not semantic caching. Similar wording will not match.
- Cached answers can be stale until TTL expiry. Search, live research, changing memory, and tool-enabled turns should bypass response caching.
- Do not use wildcard CORS in production. Set the exact app origin.
- The browser app stores its API key in local storage. Treat this as a personal single-user setup; never publish the key or use the gateway as a public shared endpoint.
- A cache hit saves inference only when the complete request JSON matches. Prompt-prefix/KV reuse is a separate optimization.
- Compose assumes Ollama runs on the host. If Ollama is a Docker service, use its internal Compose service name instead.
