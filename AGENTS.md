# Agent instructions for AI Mobile Agent

## Architecture boundaries

- Keep the client as a static HTML/JavaScript app unless a measured requirement proves a build framework necessary. Do not migrate to Hono or add a runtime server just for fashion.
- The optional Python gateway is under gateway/. It is OpenAI-compatible, proxies to host Ollama, and caches only exact, completed, low-temperature /v1/chat/completions responses.
- Keep app, answer-cache, and Ollama runtime/KV caches conceptually separate. Never claim one cache substitutes for another.
- The 8 GB VPS is a hard production budget. Do not add always-on Redis, vector databases, worker fleets, or model runtimes without a benchmark and a memory budget.
- Do not expose Ollama port 11434 or gateway port 8787 on a public interface. Compose intentionally publishes 8787 only to 127.0.0.1.
- Never commit API keys, .env files, Cloudflare Tunnel tokens, cookies, or user data. Never embed the gateway key in index.html. CORS is not authentication.
- Keep exact CORS origins. Wildcard CORS is rejected by gateway/main.py.

## Required engineering loop (Harness²-inspired, deliberately bounded)

1. **Recon:** inspect callers, tests, configuration, data flow, and official docs. Record assumptions that could change the design.
2. **Plan:** write a small plan with observable acceptance criteria and explicit non-goals. Prefer a no-dependency change if it solves the issue.
3. **Probe:** add or update the failing test/check first where feasible. Use a deterministic fixture or safe mock; don't make live network calls part of unit tests.
4. **Implement:** make the smallest complete change, preserving mobile-client compatibility and rollback paths.
5. **Independent inspection:** inspect the diff from a fresh context or a different reviewer/model when actually available. The reviewer must cite concrete files/tests; never label self-review as independent review.
6. **Compose:** keep only changes supported by tests or other observable evidence. Run compile checks, unit tests, and relevant smoke tests. Do not merge or deploy merely because generated code looks plausible.
7. **Learn:** record only repeatable, evidence-backed decisions in .trail or project docs. Distinguish observations from hypotheses. Never auto-apply a learned rule that weakens security, broadens authority, or changes production settings.
8. **Report:** state the exact checks run, their results, and what remains unverified. VPS/Cloudflare deployment is not complete unless the live target was actually tested.

## Gateway invariants

- Streaming must be streamed upstream and downstream; never read a whole SSE body into memory first.
- The response-cache key includes method, endpoint path, query string, and the entire canonical JSON payload.
- Bypass cache for stream/tool/function/multimodal requests, live or dynamic work, unknown routes, no-cache/no-store, and explicit X-AI-Cache-Bypass requests.
- Cache only completed finish_reason=stop answers. Do not serve truncated output or tool-call messages from the cache.
- Bound request size, response size, cache entry count, TTL, CPU and memory.
- Test keys across routes and query strings, CORS fail-closed behavior, auth, and cache bypass.

## Parallel agents

- Split work only when tasks are independent and file ownership is clear.
- Use read-only explorer/researcher and reviewer roles before granting a writer role. Give each worker a narrow objective and relevant evidence.
- Never have multiple agents edit the same file concurrently. Integrate changes through a parent-owned review, run tests once combined, and record which agent authored versus inspected each change.
- For risky changes, ask a second model to challenge the plan and inspect the final diff; if that is unavailable, state the limitation rather than inventing a second review.
