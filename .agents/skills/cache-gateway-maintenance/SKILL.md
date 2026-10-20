---
name: cache-gateway-maintenance
description: Safely maintain or deploy the AI Mobile Agent OpenAI-compatible Ollama cache gateway. Use when changing gateway code, caching policy, Compose, tests, or deployment steps.
---

# Cache gateway maintenance

## First inspect
- Read gateway/README.md, AGENTS.md, gateway/main.py, gateway/tests/test_cache.py, gateway/docker-compose.yml, and the latest workflow result.
- Treat the public HTML app, Cloudflare Tunnel, and VPS as separate deployment surfaces.

## Cache semantics
- L0 browser persistence, Ollama model/runtime KV reuse, and this exact-response cache are different mechanisms.
- Cache keys include method, route, query, and canonical request JSON.
- Only complete finish_reason=stop responses on /v1/chat/completions can be stored.
- Streaming, tools/functions, tool messages, multimodal requests, temperature > 0.2, dynamic/live/search turns, unknown endpoints, and client bypass headers must avoid cache storage.
- A HIT skips inference; a MISS means the request was eligible but no entry existed. BYPASS means policy intentionally skipped the response cache.

## Safety checks
- API calls require Authorization: Bearer. /health is intentionally public and exposes no configuration.
- .env is a secret-bearing, untracked server file; never add it to git.
- CORS must be exact-origin. 127.0.0.1:8787 is the only published host binding.
- Bind Ollama to the Docker bridge gateway IP for container-to-host access rather than the public IP or 0.0.0.0.
- Test stream cancellation and large requests if changing proxy behavior.

## Verify
Run from gateway/:

    python -m compileall -q main.py tests
    python -m unittest discover -s tests -v

Then run the live acceptance test from gateway/README.md on an authorized server. If the server isn't available, mark deployment and end-to-end behavior as unverified.
