# liteserver

Liteserver contains **Otto Master**, a portable Python control plane for an Otto/EVA robot cluster, plus the independent **Forge Radio WebUI** source. One Otto Master process owns the embedded MQTT broker, device sessions, REST/WebSocket control plane, mDNS, SQLite, OTA, voice pipeline, robot actions, and the official read-only Zhihu integration.

The current MVP path has been exercised with EVA1, EVA2, and EVA3 over MQTT. The voice path uses Volcengine ASR/TTS and DeepSeek; the new Forge Radio UI adds a 3D robot workbench, real multi-device controls, conversation state, and Zhihu Open Platform queries without introducing a second production server.

## Repository layout

```text
liteserver/
├── otto-master/   Python runtime, APIs, tests, packaged WebUI snapshot and docs
└── webui/         Vite/TypeScript source for the Forge Radio interface
```

The browser only talks to same-origin `/api/v1/*` endpoints. It never connects directly to MQTT, device sockets, Zhihu, Volcengine, or DeepSeek.

## Quick start

Requirements:

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Native Opus library when using voice

macOS:

```bash
brew install opus
cd otto-master
cp .env.example .env
uv sync --locked
uv run python -m otto_master
```

Windows PowerShell:

```powershell
cd otto-master
Copy-Item .env.example .env
uv sync --locked
uv run python -m otto_master
```

For source-based voice development on Windows, install `opus:x64-windows` with vcpkg and put the directory containing `opus.dll` on `PATH`. The packaging workflow separately verifies native Opus and bundled static assets on Windows.

Open [http://127.0.0.1:8081](http://127.0.0.1:8081). The same process also exposes the embedded authenticated MQTT broker on port `1883` and advertises `master.local` through mDNS.

## Local environment

Fill only the values you use in `otto-master/.env`:

```dotenv
OTTO_PROVISIONING_TOKEN=replace_me
OTTO_MQTT_MASTER_PASSWORD=replace_me
OTTO_ASR_API_KEY=replace_me
OTTO_TTS_API_KEY=replace_me
DEEPSEEK_API_KEY=replace_me
ZHIHU_ACCESS_SECRET=replace_me
```

The WebUI uses direct control by default on the trusted home LAN, so no console token is needed. To opt into console authentication, set `server.console_auth_required: true` in `otto-master/config.yaml` and define `OTTO_CONSOLE_TOKEN`; OTA provisioning remains independently protected by `OTTO_PROVISIONING_TOKEN`.

Secrets are loaded only by the Python runtime. `.env`, databases, logs, firmware binaries, audio, and credentials are Git-ignored. Zhihu access is limited to official read-only Open Platform endpoints; the server does not use cookies, scrape pages, publish content, auto-page, or automatically retry uncertain requests.

## WebUI development

The checked-in Python package already contains a built, offline WebUI, so production does **not** require Node.js. Node.js 22.12 or newer is needed only when changing the frontend:

```bash
cd webui
npm ci
npm run build
```

The build runs TypeScript checks and writes the reproducible deployment snapshot to `otto-master/src/otto_master/web/`, including the 3D models, art, GIFs, CSS, and JavaScript used by macOS and Windows packaging.

## Verification

```bash
cd otto-master
uv sync --all-extras --locked
uv run ruff check src tests scripts
uv run mypy src
uv run pytest -q
```

Real cloud checks are explicit and never run as part of ordinary pytest:

```bash
uv run python tests/external/zhihu_smoke.py
uv run python tests/external/voice_cloud_smoke.py
```

These checks report only bounded diagnostics and must never print API credentials or raw user audio.

## Architecture and documentation

Otto Master is a modular monolith: gateways translate external protocols, the in-process Message Bus carries domain events, per-device sessions isolate state, and Dispatcher is the only route to robot actions.

- [Otto Master overview](otto-master/README.md)
- [Onboarding and current state](otto-master/ONBOARD.md)
- [Architecture contracts](otto-master/CODEX_ARCHITECTURE.md)
- [Construction plan](otto-master/docs/CONSTRUCTION_PLAN.md)
- [Development progress](otto-master/docs/DEV_PROGRESS.md)
- [Server console requirements](otto-master/docs/SERVER_CONSOLE_REQUIREMENTS.md)
- [Volcengine speech integration](otto-master/docs/VOLCENGINE_SPEECH_INTEGRATION.md)
- [Windows portable source guide](otto-master/docs/WINDOWS_PORTABLE_GUIDE.md)

The Windows handoff is source-only: export the committed tree as a ZIP and deliver
the API/device secrets in a separate local TXT. No EXE or secret is included in the
source archive.

## Security note

Direct control is intended only for a trusted LAN; do not expose port `8081` directly to the internet. Never commit a real `.env` or paste production credentials into issues, logs, screenshots, or browser storage. Rotate any credential that has been exposed outside the local environment before a production deployment.
