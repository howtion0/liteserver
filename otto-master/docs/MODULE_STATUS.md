# Module Status

当前已完成Phase 1、Phase 2和Phase 3的macOS本地实现；Phase 3等待Windows矩阵。“文件存在”不代表后续业务已经实现。

| 模块 | 文件 | 状态 |
|---|---|---|
| Package entry | `__main__.py` | Phase 1已实现 |
| Runtime | `runtime.py` | Phase 1-3已实现（网络组件有序启停与失败回滚） |
| Messages | `messages.py` | Phase 1已实现 |
| Message Bus | `message_bus.py` | Phase 1+2已实现（含全消息observer） |
| Config | `config.py` | Phase 1+3已实现（Web/MQTT/OTA安全配置） |
| Structured logging | `structured_logging.py` | Phase 1已实现 |
| Device WebSocket Gateway | `gateways/device_ws.py` | 空占位 |
| Embedded MQTT Broker | `gateways/mqtt_broker.py` | Phase 3已实现；Windows CI待验收 |
| Device MQTT Gateway | `gateways/device_mqtt.py` | Phase 4计划；文件待建 |
| Legacy Device TCP Gateway | `gateways/device_tcp.py` | Phase 4计划；文件待建 |
| Web/REST/OTA Gateway | `gateways/web.py` | Phase 3已实现控制面与后续业务API骨架 |
| Cloud Gateway | `gateways/cloud.py` | 空占位 |
| mDNS Gateway | `gateways/mdns.py` | Phase 3已实现并通过本机注册/解析/注销smoke |
| Device Manager | `devices/manager.py` | 空占位 |
| Device Session | `devices/session.py` | 空占位 |
| Device States | `devices/states.py` | 空占位 |
| ASR Service | `services/asr.py` | 空占位 |
| TTS Service | `services/tts.py` | 空占位 |
| LLM Service | `services/llm.py` | 空占位 |
| WakeGate | `services/wake_gate.py` | 空占位 |
| Dispatcher | `dispatch/dispatcher.py` | 空占位 |
| Opus | `audio/opus.py` | 空占位 |
| Database | `storage/database.py` | Phase 2+3已实现（控制台只读查询/安全设置） |
| Migrations | `storage/migrations.py` | Phase 2已实现 |
| WebUI HTML | `web/index.html` | Phase 3 P0骨架已实现 |
| WebUI JavaScript | `web/app.js` | Phase 3健康/设备/OTA/事件恢复已实现 |
| WebUI CSS | `web/style.css` | Phase 3离线响应式样式已实现 |
| Message Bus tests | `tests/test_message_bus.py` | Phase 1已实现 |
| Message contract tests | `tests/test_messages.py` | Phase 1已实现 |
| Config tests | `tests/test_config.py` | Phase 1+3已实现 |
| Runtime tests | `tests/test_runtime.py` | Phase 1-3已实现，含真实HTTP/WS/MQTT端口生命周期 |
| Storage tests | `tests/test_storage.py` | Phase 2已实现 |
| Web tests | `tests/test_web.py` | Phase 3已实现 |
| MQTT Broker tests | `tests/test_mqtt_broker.py` | Phase 3已实现 |
| mDNS tests | `tests/test_mdns.py` | Phase 3已实现 |
| WakeGate tests | `tests/test_wake_gate.py` | Phase 6待实现 |
| Dispatcher tests | `tests/test_dispatcher.py` | Phase 7待实现 |
