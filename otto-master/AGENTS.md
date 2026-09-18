# Otto Master repository rules

## Read order

Before implementation work, read:

1. `ONBOARD.md`
2. `CODEX_CONSTRUCTION_WORKFLOW.md`
3. `CODEX_MASTER_REQUIREMENTS.md`
4. `CODEX_ARCHITECTURE.md`
5. `docs/DEV_PROGRESS.md`
6. The relevant phase in `docs/CONSTRUCTION_PLAN.md`
7. For Phase 3 or 4, `docs/MQTT_CONTROL_CONTRACT.md`
8. `CODEX_RULES_TESTING.md` and `CODEX_RULES_GIT.md`

## Project identity

- Product: Otto robot cluster master runtime
- Language: Python only
- Runtime shape: one cross-platform process on macOS and Windows
- Architecture: modular monolith connected through an in-process Message Bus
- Persistence: SQLite for durable records; live sockets and audio buffers stay in memory
- External AI: cloud ASR, LLM and TTS adapters
- Device compatibility target: MQTT 3.1.1 cluster control, Xiaozhi WebSocket compatibility, encrypted UDP or binary WebSocket Opus, and the temporary Otto TCP fallback

## Architectural rules

1. `runtime.py` assembles dependencies and owns lifecycle; it must not accumulate business logic.
2. Cross-module business communication goes through `MessageBus` messages.
3. Gateways translate external protocols into internal messages and translate outbound messages back.
4. External protocol payloads must not leak into service or dispatcher internals.
5. Every device has an isolated session and command queue.
6. `Dispatcher` is the only component that selects one device, a group, or the whole cluster.
7. Cloud calls belong behind service or gateway adapters; API keys come only from environment variables.
8. SQLite access is centralized under `storage/`; socket objects are never persisted.
9. Worker threads are for blocking libraries and CPU-bound conversion only. Async network I/O stays on the asyncio loop.
10. The server must fail closed: an unverified wake request must never reach LLM, MCP or robot actions.
11. MQTT topics identify devices by stable MAC-derived `device_id`, never by DHCP address or mutable display name.
12. MQTT action acknowledgements mean accepted, not completed; completion requires a state transition back to idle.

## Current phase rule

Phase 0 documentation and Phase 1 Runtime/Message Bus are implemented locally. Later-phase modules remain placeholders. Do not claim a module or the full service works until its construction phase and required tests are complete.

## Cross-platform rules

- Use `pathlib.Path`; do not hard-code Windows or POSIX separators.
- Use the Python `zeroconf` package rather than macOS-only `/usr/bin/dns-sd`.
- Keep process startup and shutdown compatible with Windows event-loop behavior.
- Do not require Docker, Redis, MySQL, Node.js, Java or a separately installed MQTT broker for the MVP; the MQTT broker is embedded in the Python process behind an adapter.

## Secrets and generated data

- Never commit `.env` or real API keys.
- Never commit `data/*.db`, `logs/*.jsonl` or `firmware/*.bin` unless the user explicitly requests an artifact release.
- `config.yaml` contains non-sensitive defaults only.

## Change workflow

- `CODEX_CONSTRUCTION_WORKFLOW.md` is a mandatory gate, not optional guidance. No implementation edit starts until the current accepted baseline is verified on GitHub and the Session Contract contains binary pass criteria.
- Preserve user changes and inspect Git status before edits once Git is initialized.
- A user request to begin Otto Master construction is standing authorization for the workflow's required backup branch, checkpoint commit and push. Merge, tag, release, force-push and remote deletion still require explicit authorization.
- Every phase checkpoint commit must use a new sequential branch named `testN.N`, starting at `test0.1` and increasing naturally through `test0.9`, `test1.0`, `test1.1`, and beyond.
- One `testN.N` branch contains one accepted checkpoint commit. Any follow-up commit uses the next branch number; branch numbers are never reused or rewritten.
- Create each new iteration branch from the latest accepted `main`. Never commit directly to `main`.
- The `testN.N` counter records construction order and is independent from the product version in `pyproject.toml`.
- Use `pyproject.toml` as the single version source.
- Update `docs/DEV_PROGRESS.md`, `docs/MODULE_STATUS.md` and `docs/LOG.md` when a phase changes state.
- Run only the tests justified by the changed phase and report unrun checks honestly.
- Any required FAIL or NOT RUN keeps the phase incomplete. Fix and rerun until PASS; never weaken, delete or skip a required test to close a phase.
- A construction turn is complete only after the checkpoint branch is pushed and its remote hash matches local HEAD.
