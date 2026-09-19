# Otto Master

Otto Master 是面向 Otto/EVA 机器人阵列的跨平台 Python Runtime。一个进程统一承担内嵌MQTT Broker、设备会话、Message Bus、动作Dispatcher、火山ASR/TTS、DeepSeek、WebUI、知乎官方只读API、OTA、mDNS和SQLite。

## 当前能力

- MQTT控制/信令与AES-CTR UDP Opus语音，兼容设备WebSocket和TCP诊断回退
- EVA1/EVA2/EVA3稳定`device_id`、独立状态、批量动作、stop和正式对话start/stop
- 每轮本地笑声门禁、火山ASR、DeepSeek流式短回答/安全工具、火山TTS和循环对话
- Forge电台3D WebUI、真实Server健康、设备选择、动作目录交集、批量控制、对话文字和OTA
- 知乎开放平台额度探针、只读单页查询、人设设置和显式目标设备朗读
- macOS/Windows自动测试、原生Opus和PyInstaller smoke

仍未完成的整体验收包括多设备并发语音工具隔离、WebSocket真机Profile和实体Windows局域网部署；精确状态见 [docs/DEV_PROGRESS.md](docs/DEV_PROGRESS.md)。

## 架构

```text
ESP32 MQTT/TCP/WS ─┐
WebUI HTTP/WS ─────┼─ Gateways ─ Message Bus ─ Device Sessions / WakeGate
Cloud HTTP/WS ─────┤                         ├─ Dispatcher / TTS / Zhihu Service
mDNS / OTA ────────┘                         └─ SQLite
```

这是模块化单体。浏览器不直连MQTT或设备，知乎/语音/LLM凭据只从环境读取，机器人动作只能经过Dispatcher。

## 启动

```bash
cp .env.example .env
uv sync --locked
uv run python -m otto_master
```

Windows PowerShell使用 `Copy-Item .env.example .env`。默认Web端口为 `8081`、MQTT为 `1883`、UDP音频为 `8884`，服务通过 `master.local` 发布。暴露到局域网前必须配置高强度 `OTTO_CONSOLE_TOKEN` 和 `OTTO_PROVISIONING_TOKEN`。

打开 `http://127.0.0.1:8081`。生产静态资源已经包含在Python包中，不需要Node；只有修改仓库根 `../webui/` 时才使用Node.js 22.12+运行：

```bash
cd ../webui
npm ci
npm run build
```

## 环境变量

参照 `.env.example` 配置：

- `OTTO_CONSOLE_TOKEN`、`OTTO_PROVISIONING_TOKEN`、`OTTO_MQTT_MASTER_PASSWORD`
- `OTTO_ASR_API_KEY`、`OTTO_TTS_API_KEY`、`DEEPSEEK_API_KEY`
- `ZHIHU_ACCESS_SECRET`

真实值不得进入YAML、SQLite、日志、消息、浏览器存储、Git或文档。

## 开发检查

```bash
uv sync --all-extras --locked
uv run ruff check src tests
uv run mypy src
uv run pytest -q
uv run python tests/packaging/static_assets_smoke.py
```

外部API检查必须显式运行：

```bash
uv run python tests/external/zhihu_smoke.py
uv run python tests/external/voice_cloud_smoke.py
```

## 文档

- [ONBOARD.md](ONBOARD.md)：新会话入口
- [CODEX_ARCHITECTURE.md](CODEX_ARCHITECTURE.md)：模块与副作用边界
- [docs/CONSTRUCTION_PLAN.md](docs/CONSTRUCTION_PLAN.md)：施工路线
- [docs/DEV_PROGRESS.md](docs/DEV_PROGRESS.md)：当前进度
- [docs/MESSAGE_CONTRACTS.md](docs/MESSAGE_CONTRACTS.md)：内部消息合同
- [docs/DEVICE_TRANSPORT_CONTRACT.md](docs/DEVICE_TRANSPORT_CONTRACT.md)：设备传输合同
- [docs/MQTT_CONTROL_CONTRACT.md](docs/MQTT_CONTROL_CONTRACT.md)：MQTT控制合同
- [docs/VOLCENGINE_SPEECH_INTEGRATION.md](docs/VOLCENGINE_SPEECH_INTEGRATION.md)：语音数据流
- [docs/SERVER_CONSOLE_REQUIREMENTS.md](docs/SERVER_CONSOLE_REQUIREMENTS.md)：控制台与Windows验收
