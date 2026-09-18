# Otto Master

Otto Master 是一个面向 Otto 机器人集群的跨平台 Python Runtime。目标是在单个 Python 进程中完成内嵌MQTT Broker、设备连接、消息总线、云端 ASR/LLM/TTS、Siri 式唤醒、集群动作分发、WebUI、OTA、mDNS 和 SQLite 持久化。

## 当前状态

当前为 **Phase 4B / 0.4.1 本地与 macOS/Windows 跨平台探针通过**：已接入Master MQTT Gateway、Device Session和只读双向连接验证；两个fake设备通过真实内嵌Broker完成精确状态/动作目录查询、ID关联、隔离和超时路径。MQTT动作/stop闭环、TCP/WebSocket兼容、语音云服务和EVA真机接入仍属于后续检查点。

## 核心架构

```text
ESP32 ─ MQTT / TCP / WebSocket ─┐
                                ▼
WebUI ─────────────────────── Gateways
      │
      ▼
 Message Bus
   ├── Device Sessions
   ├── WakeGate
   ├── Dispatcher
   ├── Cloud Services
   └── SQLite Store
```

工程采用“模块化单体”：一个工程、一个进程、一个启动命令，但模块职责和副作用边界保持清晰。

## 启动方式

```bash
uv run --project . python -m otto_master
```

入口会持续运行，默认监听Web `8080` 和MQTT `1883`，发布 `master.local`，收到 `SIGINT` 或 `SIGTERM` 后按依赖反向关闭。

默认配置绑定局域网地址。暴露到局域网前，在本地 `.env` 设置高强度 `OTTO_CONSOLE_TOKEN` 和 `OTTO_PROVISIONING_TOKEN`；未配置时，状态修改、WebSocket事件流和设备发放接口会失败关闭。MQTT禁止匿名连接，Master缺少显式密码时会在 `.local-secrets/` 生成随机本地凭据。

## 开发检查

```bash
uv run --project . --extra dev ruff check src tests
uv run --project . --extra dev mypy src
uv run --project . --extra dev pytest -q
```

## 文档入口

- [ONBOARD.md](ONBOARD.md)：新会话快速入口
- [CODEX_MASTER_REQUIREMENTS.md](CODEX_MASTER_REQUIREMENTS.md)：项目宪法
- [CODEX_CONSTRUCTION_WORKFLOW.md](CODEX_CONSTRUCTION_WORKFLOW.md)：每轮GitHub备份、计划、测试返工、日志与上传的强制门禁
- [CODEX_ARCHITECTURE.md](CODEX_ARCHITECTURE.md)：模块契约和依赖规则
- [docs/CONSTRUCTION_PLAN.md](docs/CONSTRUCTION_PLAN.md)：施工路线
- [docs/DEV_PROGRESS.md](docs/DEV_PROGRESS.md)：当前进度
- [docs/MESSAGE_CONTRACTS.md](docs/MESSAGE_CONTRACTS.md)：内部消息格式
- [docs/MQTT_CONTROL_CONTRACT.md](docs/MQTT_CONTROL_CONTRACT.md)：MQTT Topic、动作协议、迁移与EVA真机验收
- [docs/SERVER_CONSOLE_REQUIREMENTS.md](docs/SERVER_CONSOLE_REQUIREMENTS.md)：打包前Web控制台、设备接入、连接验证、动作闭环和验收清单

## 运行产物

以下文件不会在 Phase 0 伪造，运行后再生成或由构建流程放入：

- `data/otto.db`
- `logs/otto-master.jsonl`
- `firmware/xiaozhi.bin`

## 参考来源

文档治理方式参考用户提供的 `catnipthon-backup-20250524-phase0.zip`，但已按 Otto Master 的消息总线和机器人集群场景重新编写，没有迁移 Catnipthon 业务代码。
