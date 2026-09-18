# Module Status

当前已完成Phase 1、Phase 2、Phase 3、Phase 4A和Phase 4B实现；Phase 4B本地与macOS/Windows探针矩阵均通过。“文件存在”不代表后续业务已经实现。

| 模块 | 文件 | 状态 |
|---|---|---|
| Package entry | `__main__.py` | Phase 1已实现 |
| Runtime | `runtime.py` | Phase 1-4B已实现（含Verifier有序启停、pending取消与失败回滚） |
| Messages | `messages.py` | Phase 1已实现 |
| Message Bus | `message_bus.py` | Phase 1+2已实现（含全消息observer） |
| Config | `config.py` | Phase 1+3+4A+4B已实现（含查询超时配置） |
| Structured logging | `structured_logging.py` | Phase 1已实现 |
| Device WebSocket Gateway | `gateways/device_ws.py` | 空占位 |
| Embedded MQTT Broker | `gateways/mqtt_broker.py` | Phase 3已实现；macOS/Windows CI与PyInstaller smoke通过 |
| Device MQTT Gateway | `gateways/device_mqtt.py` | Phase 4A+4B已实现严格上行及白名单状态/动作目录精确下行；动作/stop待后续检查点 |
| Legacy Device TCP Gateway | `gateways/device_tcp.py` | Phase 4计划；文件待建 |
| Web/REST/OTA Gateway | `gateways/web.py` | Phase 3+4A+4B已实现控制面、实时设备读取、事件流和受保护只读验证API |
| Cloud Gateway | `gateways/cloud.py` | 空占位 |
| mDNS Gateway | `gateways/mdns.py` | Phase 3已实现并通过本机注册/解析/注销smoke |
| Device Manager | `devices/manager.py` | Phase 4A已实现设备去重、消息消费、在线判定、持久化和Gateway故障降级 |
| Device Session | `devices/session.py` | Phase 4A已实现身份、hello/heartbeat/state/actions快照；动作队列待后续检查点 |
| Device States | `devices/states.py` | Phase 4A已实现连接与动作状态枚举/迁移规则 |
| Device Verifier | `devices/verifier.py` | Phase 4B已实现只读预检、correlation、超时/断线中断和逐步报告 |
| ASR Service | `services/asr.py` | 空占位 |
| TTS Service | `services/tts.py` | 空占位 |
| LLM Service | `services/llm.py` | 空占位 |
| WakeGate | `services/wake_gate.py` | 空占位 |
| Dispatcher | `dispatch/dispatcher.py` | 空占位 |
| Opus | `audio/opus.py` | 空占位 |
| Database | `storage/database.py` | Phase 2-4A已实现（设备快照、动作目录、重启离线降级） |
| Migrations | `storage/migrations.py` | Phase 2+4A已实现，当前schema v2 |
| WebUI HTML | `web/index.html` | Phase 3 P0骨架已实现 |
| WebUI JavaScript | `web/app.js` | Phase 3健康/设备/OTA/事件恢复已实现 |
| WebUI CSS | `web/style.css` | Phase 3离线响应式样式已实现 |
| Message Bus tests | `tests/test_message_bus.py` | Phase 1已实现 |
| Message contract tests | `tests/test_messages.py` | Phase 1已实现 |
| Config tests | `tests/test_config.py` | Phase 1+3+4B已实现 |
| Runtime tests | `tests/test_runtime.py` | Phase 1-4B已实现，含双fake真实Broker上下行验证、隔离、超时与端口闭环 |
| Storage tests | `tests/test_storage.py` | Phase 2+4A已实现 |
| Web tests | `tests/test_web.py` | Phase 3+4A+4B已实现 |
| MQTT Broker tests | `tests/test_mqtt_broker.py` | Phase 3已实现 |
| Device MQTT tests | `tests/test_device_mqtt.py` | Phase 4A+4B已实现上下行映射、身份、白名单与边界拒绝 |
| Device Manager tests | `tests/test_device_manager.py` | Phase 4A已实现会话去重、超时和重启恢复 |
| Device Verifier tests | `tests/test_device_verifier.py` | Phase 4B已实现关联、错误目标/ID、超时与关闭中断 |
| mDNS tests | `tests/test_mdns.py` | Phase 3已实现 |
| WakeGate tests | `tests/test_wake_gate.py` | Phase 6待实现 |
| Dispatcher tests | `tests/test_dispatcher.py` | Phase 7待实现 |
