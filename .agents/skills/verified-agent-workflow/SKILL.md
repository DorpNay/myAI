---
name: verified-agent-workflow
description: Apply a bounded, evidence-gated coding workflow inspired by Harness², Claudex Loop, and curated agent-skill systems. Use for multi-file changes, architecture decisions, security-sensitive code, or agent orchestration.
---

# Verified agent workflow

## Purpose
Increase task success through better prompts, tool scope, memory, delegation, skills, and verification—not by changing model weights or spawning agents without controls.

## Procedure
1. Read AGENTS.md and inspect the current implementation and its callers.
2. Write a short plan, a list of assumptions, acceptance checks, a rollback path, and non-goals.
3. Generate a small number of candidate approaches. Prefer one baseline plus at most two meaningful alternatives; do not equate parallel exploration with correctness.
4. Probe candidates using existing unit tests, deterministic test fixtures, syntax checks, and a bounded smoke test. Never let a proposal grade itself without independent evidence.
5. Compose the final change from the evidence-supported parts. Do not automatically merge every candidate.
6. Request an independent review for architecture/security-sensitive work when another model or reviewer is available. It must inspect the final diff, not only the plan.
7. Persist only specific, reproducible findings. Include evidence, scope, confidence, and a revisit condition. Keep personal secrets and transcripts out of repository memory.
8. Report checks that passed and checks not run. Never imply production deployment without live validation.

## Hard limits
- Do not change authentication, network exposure, CORS, resource limits, or deployment targets without explicit justification and tests.
- No autonomous push, merge, release, or production deploy as a side effect of a successful model response.
- Parallel writers must use separate branches/worktrees and distinct file ownership.
- Keep the iteration budget small. Stop when acceptance criteria are met or a safety blocker is found.
