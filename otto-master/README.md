# Otto Master

Otto Master 是一个面向 Otto 机器人集群的跨平台 Python Runtime。目标是在单个 Python 进程中完成内嵌MQTT Broker、设备连接、消息总线、云端 ASR/LLM/TTS、Siri 式唤醒、集群动作分发、WebUI、OTA、mDNS 和 SQLite 持久化。

## 当前状态

当前为 **Phase 1 / 0.1.0 Runtime 与 Message Bus**：配置加载、消息合同、异步消息总线、Runtime 生命周期和结构化日志已经实现并通过自动测试。硬件、Web、SQLite 和真实云服务仍未接入。

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

入口会持续运行，收到 `SIGINT` 或 `SIGTERM` 后优雅关闭。当前阶段只启动进程内 Runtime，不监听 Web 或设备端口。

## Phase 1 开发检查

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

## 运行产物

以下文件不会在 Phase 0 伪造，运行后再生成或由构建流程放入：

- `data/otto.db`
- `logs/otto-master.jsonl`
- `firmware/xiaozhi.bin`

## 参考来源

文档治理方式参考用户提供的 `catnipthon-backup-20250524-phase0.zip`，但已按 Otto Master 的消息总线和机器人集群场景重新编写，没有迁移 Catnipthon 业务代码。
