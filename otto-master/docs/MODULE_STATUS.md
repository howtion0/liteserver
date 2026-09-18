# Module Status

当前已完成Phase 1、Phase 2、Phase 3、Phase 4A-4D实现；Phase 4D本地80个测试、macOS/Windows探针与PyInstaller smoke通过。“文件存在”不代表固件、真机或后续云业务已经实现。

| 模块 | 文件 | 状态 |
|---|---|---|
| Package entry | `__main__.py` | Phase 1已实现 |
| Runtime | `runtime.py` | Phase 1-4D已实现（含MQTT/TCP/设备WS、Verifier/Dispatcher有序启停与失败回滚） |
| Messages | `messages.py` | Phase 1已实现 |
| Message Bus | `message_bus.py` | Phase 1+2已实现（含全消息observer） |
| Config | `config.py` | Phase 1+3+4A-4D已实现（含查询/命令超时、TCP与设备WS严格边界） |
| Structured logging | `structured_logging.py` | Phase 1已实现 |
| Device WebSocket Gateway | `gateways/device_ws.py` | Phase 4D已实现Xiaozhi v1认证、hello/listen/abort、Otto扩展与有界Opus引用缓冲 |
| Embedded MQTT Broker | `gateways/mqtt_broker.py` | Phase 3已实现；macOS/Windows CI与PyInstaller smoke通过 |
| Device protocol adapter | `gateways/device_protocol.py` | Phase 4D已实现三种传输共享的严格JSON上行翻译与白名单命令编码 |
| Device MQTT Gateway | `gateways/device_mqtt.py` | Phase 4A-4D已实现严格上行、精确下行与transport过滤 |
| Legacy Device TCP Gateway | `gateways/device_tcp.py` | Phase 4D已实现认证的`otto-master/1`换行JSON、替换连接与命令生命周期 |
| Web/REST/OTA Gateway | `gateways/web.py` | Phase 3+4A-4D已实现控制面、设备/事件读取、验证、命令API及TCP/WS受保护发放 |
| Cloud Gateway | `gateways/cloud.py` | 空占位；火山ASR/TTS协议与API Key鉴权已独立烟测，待Phase 5实现 |
| mDNS Gateway | `gateways/mdns.py` | Phase 3已实现并通过本机注册/解析/注销smoke |
| Device Manager | `devices/manager.py` | Phase 4A+4D已实现设备去重、每传输快照、固定优先级选择、故障隔离与降级 |
| Device Session | `devices/session.py` | Phase 4A+4D已实现身份、每传输在线/心跳/Profile及首选传输快照隔离 |
| Device States | `devices/states.py` | Phase 4A已实现连接与动作状态枚举/迁移规则 |
| Device Verifier | `devices/verifier.py` | Phase 4B+4D已实现只读预检、锁定transport、严格响应关联与超时/断线中断 |
| ASR Service | `services/asr.py` | 空占位；火山ASR 1.0时长版真实API烟测通过，Otto Master链路未实现 |
| TTS Service | `services/tts.py` | 空占位；火山TTS 2.0 PCM真实API烟测通过，设备播放链未实现 |
| LLM Service | `services/llm.py` | 空占位 |
| WakeGate | `services/wake_gate.py` | 空占位 |
| Command contracts/repository | `dispatch/commands.py` | Phase 4C已实现状态机、幂等创建、历史和重启失败关闭 |
| Dispatcher | `dispatch/dispatcher.py` | Phase 4C+4D已实现队列/stop/超时及transport锁定、切换失败关闭和三Gateway精确路由 |
| Opus | `audio/opus.py` | 空占位；独立烟测已用`opuslib-next`完成24 kHz PCM→60 ms Opus→PCM首帧验证，待Phase 5正式实现与跨平台验收 |
| Database | `storage/database.py` | Phase 2-4C已实现（设备/动作快照及命令/结果持久） |
| Migrations | `storage/migrations.py` | Phase 2+4A+4C已实现，当前schema v3 |
| WebUI HTML | `web/index.html` | Phase 3 P0骨架已实现 |
| WebUI JavaScript | `web/app.js` | Phase 3健康/设备/OTA/事件恢复已实现 |
| WebUI CSS | `web/style.css` | Phase 3离线响应式样式已实现 |
| Message Bus tests | `tests/test_message_bus.py` | Phase 1已实现 |
| Message contract tests | `tests/test_messages.py` | Phase 1已实现 |
| Config tests | `tests/test_config.py` | Phase 1+3+4B已实现 |
| Runtime tests | `tests/test_runtime.py` | Phase 1-4D已实现，含双fake真Broker查询、动作/stop、去重、隔离与端口闭环 |
| Storage tests | `tests/test_storage.py` | Phase 2+4A+4C已实现 |
| Command repository tests | `tests/test_command_repository.py` | Phase 4C已实现幂等、转移、历史与重启恢复 |
| Web tests | `tests/test_web.py` | Phase 3+4A-4D已实现 |
| MQTT Broker tests | `tests/test_mqtt_broker.py` | Phase 3+4D已实现凭据与三传输发放边界 |
| Device MQTT tests | `tests/test_device_mqtt.py` | Phase 4A-4D已实现上下行映射、身份、白名单、ACK/state与transport边界 |
| Device TCP tests | `tests/test_device_tcp.py` | Phase 4D已实现真实Socket认证、边界、替换、查询、动作/stop与关闭覆盖 |
| Device WebSocket tests | `tests/test_device_ws.py` | Phase 4D已实现真实Runtime握手、音频引用、三传输隔离、动作/stop与资源释放覆盖 |
| Device Manager tests | `tests/test_device_manager.py` | Phase 4A+4D已实现会话去重、每传输隔离、优先级切换、超时和重启恢复 |
| Device Verifier tests | `tests/test_device_verifier.py` | Phase 4B+4D已实现关联、错误目标/ID/transport、超时与关闭中断 |
| mDNS tests | `tests/test_mdns.py` | Phase 3已实现 |
| WakeGate tests | `tests/test_wake_gate.py` | Phase 6待实现 |
| Dispatcher tests | `tests/test_dispatcher.py` | Phase 4C+4D已实现队列/stop/超时、transport锁定、切换断线与集群拆分 |
