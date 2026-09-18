# Module Status

当前已完成Phase 1-4的软件基座和Phase 4E双机控制门禁；`test0.9`又完成EVA1的火山ASR/TTS、DeepSeek文本/工具流、MQTT UDP音频和循环WakeGate纵向链。当前本地144个测试通过，但第二台语音工具隔离、WebSocket真机、正式CI与Windows实体部署仍未完成。

| 模块 | 文件 | 状态 |
|---|---|---|
| Package entry | `__main__.py` | Phase 1已实现 |
| Runtime | `runtime.py` | Phase 1-5纵向链已组装（含三设备Gateway、MQTT UDP、Cloud/ASR/LLM/TTS/WakeGate有序启停与失败回滚） |
| Messages | `messages.py` | Phase 1已实现 |
| Message Bus | `message_bus.py` | Phase 1+2已实现（含全消息observer） |
| Config | `config.py` | 已实现设备传输、语音Provider、有界队列、8秒静默与笑声门禁配置；Key只从环境读取 |
| Structured logging | `structured_logging.py` | Phase 1已实现 |
| Device WebSocket Gateway | `gateways/device_ws.py` | 已实现Xiaozhi v1认证、hello/listen/VAD/abort/goodbye、Otto扩展与有界Opus引用缓冲；语音真机Profile未验收 |
| Embedded MQTT Broker | `gateways/mqtt_broker.py` | Phase 3已实现；macOS/Windows CI与PyInstaller smoke通过 |
| Device protocol adapter | `gateways/device_protocol.py` | 已实现三种传输共享的严格JSON翻译、白名单命令、VAD和session关闭合同 |
| Device MQTT Gateway | `gateways/device_mqtt.py` | 已实现严格上行、精确下行、transport过滤及语音JSON路由 |
| Device UDP Gateway | `gateways/device_udp.py` | 已实现按设备/session隔离的AES-128-CTR UDP Opus、序号校验、有界缓冲和清理 |
| Device audio router | `gateways/device_audio.py` | 已实现WebSocket与MQTT UDP统一帧引用读取和TTS播放选择 |
| Legacy Device TCP Gateway | `gateways/device_tcp.py` | Phase 4D已实现认证的`otto-master/1`换行JSON、替换连接与命令生命周期 |
| Web/REST/OTA Gateway | `gateways/web.py` | Phase 3+4A-4D已实现控制面、设备/事件读取、验证、命令API及TCP/WS受保护发放 |
| Cloud Gateway | `gateways/cloud.py` | 已实现火山ASR二进制WS、火山TTS流式HTTP、DeepSeek文本/工具SSE与稳定错误映射 |
| mDNS Gateway | `gateways/mdns.py` | Phase 3已实现并通过本机注册/解析/注销smoke |
| Device Manager | `devices/manager.py` | Phase 4A+4D已实现设备去重、每传输快照、固定优先级选择、故障隔离与降级 |
| Device Session | `devices/session.py` | Phase 4A+4D已实现身份、每传输在线/心跳/Profile及首选传输快照隔离 |
| Device States | `devices/states.py` | Phase 4A已实现连接与动作状态枚举/迁移规则 |
| Device Verifier | `devices/verifier.py` | Phase 4B+4D已实现只读预检、锁定transport、严格响应关联与超时/断线中断 |
| ASR Service | `services/asr.py` | 已实现每设备arm、当前750帧队列、180 ms块、partial稳定端点、final/取消/清理 |
| TTS Service | `services/tts.py` | 已实现句子4/PCM16/Opus48有界流水线、24 kHz PCM、60 ms Opus和paced播放 |
| LLM Service | `services/llm.py` | 已实现DeepSeek文本/单工具SSE、有界生产消费、严格增量组装、短文本历史和输入/输出截断 |
| Robot Tool Bridge | `services/robot_tools.py` | 已实现设备目录到安全Schema、当前设备锁定、参数二次校验、确定性命令ID、Dispatcher终态等待和失败安全stop |
| WakeGate | `services/wake_gate.py` | 已实现每轮真实笑声门禁、循环问答、文本/TTS或工具分流、成功无复述、笑声复用、8秒静默、按钮goodbye和新session失败自恢复 |
| Command contracts/repository | `dispatch/commands.py` | Phase 4C已实现状态机、幂等创建、历史和重启失败关闭 |
| Dispatcher | `dispatch/dispatcher.py` | Phase 4C+4D已实现队列/stop/超时及transport锁定、切换失败关闭和三Gateway精确路由 |
| Opus | `audio/opus.py` | 已实现16/24 kHz单声道解码、60 ms编码、跨块缓冲和尾帧补齐；Windows打包待验收 |
| Database | `storage/database.py` | Phase 2-4C已实现（设备/动作快照及命令/结果持久） |
| Migrations | `storage/migrations.py` | Phase 2+4A+4C已实现，当前schema v3 |
| WebUI HTML | `web/index.html` | Phase 3 P0骨架已实现 |
| WebUI JavaScript | `web/app.js` | Phase 3健康/设备/OTA/事件恢复已实现 |
| WebUI CSS | `web/style.css` | Phase 3离线响应式样式已实现 |
| Message Bus tests | `tests/test_message_bus.py` | Phase 1已实现 |
| Message contract tests | `tests/test_messages.py` | Phase 1已实现 |
| Config tests | `tests/test_config.py` | Phase 1+3+4B已实现 |
| Runtime tests | `tests/test_runtime.py` | 覆盖三Gateway、Broker、语音组件装配、动作/stop、隔离与端口闭环 |
| Storage tests | `tests/test_storage.py` | Phase 2+4A+4C已实现 |
| Command repository tests | `tests/test_command_repository.py` | Phase 4C已实现幂等、转移、历史与重启恢复 |
| Web tests | `tests/test_web.py` | Phase 3+4A-4D已实现 |
| MQTT Broker tests | `tests/test_mqtt_broker.py` | Phase 3+4D已实现凭据与三传输发放边界 |
| Device MQTT tests | `tests/test_device_mqtt.py` | 覆盖上下行身份、命令、语音控制、VAD/session边界与transport隔离 |
| Device UDP tests | `tests/test_device_udp.py` | 覆盖AES-CTR数据包、session、frame_ref、VAD、乱序和关闭清理 |
| Device TCP tests | `tests/test_device_tcp.py` | Phase 4D已实现真实Socket认证、边界、替换、查询、动作/stop与关闭覆盖 |
| Device WebSocket tests | `tests/test_device_ws.py` | 覆盖真实Runtime握手、音频引用、VAD/goodbye、三传输隔离、动作/stop与资源释放 |
| Device Manager tests | `tests/test_device_manager.py` | Phase 4A+4D已实现会话去重、每传输隔离、优先级切换、超时和重启恢复 |
| Device Verifier tests | `tests/test_device_verifier.py` | Phase 4B+4D已实现关联、错误目标/ID/transport、超时与关闭中断 |
| mDNS tests | `tests/test_mdns.py` | Phase 3已实现 |
| Cloud/Opus/ASR/LLM/TTS tests | `tests/test_cloud.py`等 | 覆盖文本/工具流协议、严格JSON、编解码、背压、截断、取消、超时和失败清理 |
| Robot Tool tests | `tests/test_robot_tools.py` | 5项覆盖目录收窄、目标锁定、完成、参数拒绝、失败/取消安全stop |
| WakeGate tests | `tests/test_wake_gate.py` | 16项覆盖笑声顺序、循环、上屏、VAD、8秒静默、文本/工具分流、失败、笑声复用和新session恢复 |
| Dispatcher tests | `tests/test_dispatcher.py` | Phase 4C+4D已实现队列/stop/超时、transport锁定、切换断线与集群拆分 |

## 真机与固件状态

| 项目 | 状态 |
|---|---|
| EVA固件 | `2.0.11`已用ESP-IDF 5.5.5构建；应用镜像3,788,720字节，SHA256 `4c4298363b621599ea11c8daba2dc3efbc644538666a9f10f7115ece49e200bf`；源码远端`codex/otto-portable@abb769f1` |
| EVA1 | `e072a1f71184`，MQTT online，固件2.0.11；真实流式问答、循环笑声、按钮/静默退出及DeepSeek `laugh/walk`工具通过；最后一次工具验收后只读确认idle |
| EVA2 | `aca704ed89a8`，固件2.0.9但由用户关机；本轮未做语音验收 |
| 双机隔离 | EVA1与EVA2分别动作时另一台保持idle；身份、凭据与精确topic按MAC隔离 |
| 本地音效 | 奶龙笑声约2.0065秒、24 kHz OpusHead/34个60 ms包；EVA1连续两轮观测`moving/laugh/busy=true → idle/false`，不再产生15秒Dispatcher悬挂 |
| 安全与恢复 | 无`current_token`改配被拒绝；相同命令ID仅重放缓存ACK；Server/Broker重启后双机重新hello、verify与WebUI恢复 |
| 未完成 | EVA1真实按钮/麦克风工具回合的用户主观确认、EVA2语音工具隔离、WebSocket真机Profile、`test0.9`正式CI及实体Windows局域网 |
