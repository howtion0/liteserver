# Phase 1施工契约

## 基本信息

- 日期：2026-09-18
- 当前版本：`0.0.0` → `0.1.0`
- 当前Phase：Phase 1 Runtime与Message Bus
- Git迭代支线：未创建；未commit、未push

## 本轮目标

- 实现配置加载和环境变量替换。
- 实现可校验的内部Message合同。
- 实现精确主题的异步Message Bus和异常隔离。
- 实现Runtime依赖组装、后台任务、线程池和优雅关闭。
- 实现JSONL结构化日志。

## 输入与外部依赖

- ESP32：否
- 真实云API：否
- 局域网：否
- Windows验证：否
- MQTT Broker：否
- EVA真机：否
- 安全清理：不涉及设备动作

## 非目标

- 不实现SQLite、Web、设备Gateway、ASR、LLM、TTS、WakeGate或Dispatcher。
- 不提交或推送Git检查点。

## 实际结果

- Ruff：通过。
- mypy strict：通过。
- 单元测试：10通过。
- 入口冒烟：`python -m otto_master` 启动并在Ctrl-C后输出停止日志，干净退出。
