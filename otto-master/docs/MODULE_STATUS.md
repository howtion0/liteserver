# Module Status

当前已完成Phase 1、Phase 2、Phase 3、Phase 4A-4C实现；Phase 4C本地门禁通过、远程矩阵待验收。“文件存在”不代表后续业务已经实现。

| 模块 | 文件 | 状态 |
|---|---|---|
| Package entry | `__main__.py` | Phase 1已实现 |
| Runtime | `runtime.py` | Phase 1-4C已实现（含Verifier/Dispatcher有序启停、pending取消与失败回滚） |
| Messages | `messages.py` | Phase 1已实现 |
| Message Bus | `message_bus.py` | Phase 1+2已实现（含全消息observer） |
| Config | `config.py` | Phase 1+3+4A-4C已实现（含查询、ACK/完成超时和队列配置） |
| Structured logging | `structured_logging.py` | Phase 1已实现 |
| Device WebSocket Gateway | `gateways/device_ws.py` | 空占位 |
| Embedded MQTT Broker | `gateways/mqtt_broker.py` | Phase 3已实现；macOS/Windows CI与PyInstaller smoke通过 |
| Device MQTT Gateway | `gateways/device_mqtt.py` | Phase 4A-4C已实现严格上行及查询/动作/stop白名单精确下行 |
| Legacy Device TCP Gateway | `gateways/device_tcp.py` | Phase 4计划；文件待建 |
| Web/REST/OTA Gateway | `gateways/web.py` | Phase 3+4A-4C已实现控制面、设备/事件读取、验证与受保护命令API |
| Cloud Gateway | `gateways/cloud.py` | 空占位 |
| mDNS Gateway | `gateways/mdns.py` | Phase 3已实现并通过本机注册/解析/注销smoke |
| Device Manager | `devices/manager.py` | Phase 4A已实现设备去重、消息消费、在线判定、持久化和Gateway故障降级 |
| Device Session | `devices/session.py` | Phase 4A已实现身份、hello/heartbeat/state/actions快照；动作队列由Phase 4C Dispatcher按device_id管理 |
| Device States | `devices/states.py` | Phase 4A已实现连接与动作状态枚举/迁移规则 |
| Device Verifier | `devices/verifier.py` | Phase 4B已实现只读预检、correlation、超时/断线中断和逐步报告 |
| ASR Service | `services/asr.py` | 空占位 |
| TTS Service | `services/tts.py` | 空占位 |
| LLM Service | `services/llm.py` | 空占位 |
| WakeGate | `services/wake_gate.py` | 空占位 |
| Command contracts/repository | `dispatch/commands.py` | Phase 4C已实现状态机、幂等创建、历史和重启失败关闭 |
| Dispatcher | `dispatch/dispatcher.py` | Phase 4C已实现校验、每设备队列、stop抢占、超时/断线与集群stop拆分 |
| Opus | `audio/opus.py` | 空占位 |
| Database | `storage/database.py` | Phase 2-4C已实现（设备/动作快照及命令/结果持久） |
| Migrations | `storage/migrations.py` | Phase 2+4A+4C已实现，当前schema v3 |
| WebUI HTML | `web/index.html` | Phase 3 P0骨架已实现 |
| WebUI JavaScript | `web/app.js` | Phase 3健康/设备/OTA/事件恢复已实现 |
| WebUI CSS | `web/style.css` | Phase 3离线响应式样式已实现 |
| Message Bus tests | `tests/test_message_bus.py` | Phase 1已实现 |
| Message contract tests | `tests/test_messages.py` | Phase 1已实现 |
| Config tests | `tests/test_config.py` | Phase 1+3+4B已实现 |
| Runtime tests | `tests/test_runtime.py` | Phase 1-4C已实现，含双fake真Broker查询、动作/stop、去重、隔离与端口闭环 |
| Storage tests | `tests/test_storage.py` | Phase 2+4A+4C已实现 |
| Command repository tests | `tests/test_command_repository.py` | Phase 4C已实现幂等、转移、历史与重启恢复 |
| Web tests | `tests/test_web.py` | Phase 3+4A-4C已实现 |
| MQTT Broker tests | `tests/test_mqtt_broker.py` | Phase 3已实现 |
| Device MQTT tests | `tests/test_device_mqtt.py` | Phase 4A-4C已实现上下行映射、身份、白名单、ACK/state与参数边界 |
| Device Manager tests | `tests/test_device_manager.py` | Phase 4A已实现会话去重、超时和重启恢复 |
| Device Verifier tests | `tests/test_device_verifier.py` | Phase 4B已实现关联、错误目标/ID、超时与关闭中断 |
| mDNS tests | `tests/test_mdns.py` | Phase 3已实现 |
| WakeGate tests | `tests/test_wake_gate.py` | Phase 6待实现 |
| Dispatcher tests | `tests/test_dispatcher.py` | Phase 4C已实现串行/并行、乱序、有界队列、stop、超时、断线与集群拆分 |
