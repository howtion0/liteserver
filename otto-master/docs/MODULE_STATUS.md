# Module Status

当前已完成Phase 1-4的软件基座和Phase 4E双机控制门禁；`test0.9`完成EVA1语音/工具纵向链，`test1.0`新增多设备控制、对话/动作加固、看山表情和mDNS换网刷新，`test1.1`把Forge电台、知乎官方只读服务和静态资源跨平台交付纳入同一Python进程。`test1.2`进一步实现可信LAN默认免令牌直控、可选安全模式、在线空动作目录自动只读恢复和Windows源码迁移脚手架。当前本地207个测试、Ruff、mypy、干净前端构建、Runtime HTTP和静态资源smoke均通过；EVA2/EVA3免令牌一步前进均completed并回idle，本轮远程macOS/Windows CI待push后确认。动作图目视、多设备并发语音工具隔离、WebSocket真机与实体Windows部署仍未完成。

| 模块 | 文件 | 状态 |
|---|---|---|
| Package entry | `__main__.py` | Phase 1已实现 |
| Runtime | `runtime.py` | Phase 1-5纵向链已组装（含三设备Gateway、MQTT UDP、Cloud/ASR/LLM/TTS/WakeGate、Zhihu Gateway/Service有序启停与失败回滚） |
| Messages | `messages.py` | Phase 1已实现 |
| Message Bus | `message_bus.py` | Phase 1+2已实现（含全消息observer） |
| Config | `config.py` | 已实现设备传输、语音Provider、有界队列、TTS PCM增益、对话门禁、知乎边界和`console_auth_required`可选安全模式；云端Key与Access Secret只从环境读取 |
| Structured logging | `structured_logging.py` | Phase 1已实现 |
| Device WebSocket Gateway | `gateways/device_ws.py` | 已实现Xiaozhi v1认证、hello/listen/VAD/abort/goodbye、Otto扩展与有界Opus引用缓冲；语音真机Profile未验收 |
| Embedded MQTT Broker | `gateways/mqtt_broker.py` | Phase 3已实现；macOS/Windows CI与PyInstaller smoke通过 |
| Device protocol adapter | `gateways/device_protocol.py` | 已实现三种传输共享的严格JSON翻译、白名单命令、VAD和session关闭合同 |
| Device MQTT Gateway | `gateways/device_mqtt.py` | 已实现严格上行、精确下行、transport过滤及语音JSON路由 |
| Device UDP Gateway | `gateways/device_udp.py` | 已实现按设备/session隔离的AES-128-CTR UDP Opus、序号校验、有界缓冲和清理 |
| Device audio router | `gateways/device_audio.py` | 已实现WebSocket与MQTT UDP统一帧引用读取和TTS播放选择 |
| Legacy Device TCP Gateway | `gateways/device_tcp.py` | Phase 4D已实现认证的`otto-master/1`换行JSON、替换连接与命令生命周期 |
| Web/REST/OTA Gateway | `gateways/web.py` | 已实现控制面、设备/事件读取、验证、命令API、TCP/WS受保护发放、多设备动作/stop、对话投影、知乎API、设备朗读和递归静态资源；默认可信LAN直控仍校验Origin，可选Bearer模式失败关闭 |
| Cloud Gateway | `gateways/cloud.py` | 已实现火山ASR二进制WS、火山TTS流式HTTP、DeepSeek文本/工具SSE与稳定错误映射 |
| Zhihu Gateway | `gateways/zhihu.py` | 已实现官方域名锁定、Bearer/时间戳、只读工具白名单、严格URL/分页参数、2路并发、超时、2 MiB响应上限、无重试和稳定错误映射 |
| mDNS Gateway | `gateways/mdns.py` | 已实现注册/解析/注销，并以可配置周期监视IPv4、原位更新服务、保留最后有效LAN记录及公开刷新健康状态 |
| Device Manager | `devices/manager.py` | Phase 4A+4D已实现设备去重、每传输快照、固定优先级选择、故障隔离与降级 |
| Device Session | `devices/session.py` | Phase 4A+4D已实现身份、每传输在线/心跳/Profile及首选传输快照隔离；当前快照包含本地音效、显示别名和输出音量 |
| Device States | `devices/states.py` | Phase 4A已实现连接与动作状态枚举/迁移规则 |
| Device Verifier | `devices/verifier.py` | Phase 4B+4D已实现只读预检、锁定transport、严格响应关联与超时/断线中断 |
| ASR Service | `services/asr.py` | 已实现每设备arm、当前750帧队列、180 ms块、相互独立的partial稳定/VAD静音/12秒硬上限三端点、尾帧排空、原子取消清理及丢帧分类指标 |
| TTS Service | `services/tts.py` | 已实现句子4/PCM16/Opus48有界流水线、24 kHz S16LE饱和增益、跨块半采样保留、60 ms Opus和paced播放 |
| Zhihu Service | `services/zhihu.py` | 已实现官方能力探针、单页查询、非敏感画像SQLite设置和仅含元数据的有界内存事件；查询结果不持久化 |
| Device narration | `services/narration.py` | 已实现稳定12位device_id校验、600字符边界并复用既有TTS Service，不增加第二条浏览器音频链 |
| LLM Service | `services/llm.py` | 已实现DeepSeek文本/单工具SSE、有界生产消费、工具前未成句前缀丢弃、已成句前缀有序输出、短文本历史和输入/输出截断 |
| Robot Tool Bridge | `services/robot_tools.py` | 已实现设备目录到安全Schema、当前设备锁定、参数二次校验、确定性命令ID、Dispatcher终态等待和失败安全stop |
| WakeGate | `services/wake_gate.py` | 已实现每轮真实笑声门禁、瞬时状态查询超时重试、动作提交前持续等待action与sound共同空闲、循环问答、首次空final笑一次/连续第二次正常退出、TTS stop后串行工具、成功无结果复述、笑声复用、仅由非空partial取消的8秒静默、按钮goodbye和新session失败自恢复 |
| Command contracts/repository | `dispatch/commands.py` | Phase 4C已实现状态机、幂等创建、历史和重启失败关闭 |
| Dispatcher | `dispatch/dispatcher.py` | Phase 4C+4D已实现队列/stop/超时及transport锁定、切换失败关闭和三Gateway精确路由；有本地音效时等待action idle与sound非busy后才completed；生产完成时限30秒、ACK时限3秒 |
| Opus | `audio/opus.py` | 已实现16/24 kHz单声道解码、60 ms编码、跨块缓冲和尾帧补齐；Windows打包待验收 |
| Database | `storage/database.py` | Phase 2-4C已实现（设备/动作快照及命令/结果持久） |
| Migrations | `storage/migrations.py` | Phase 2+4A+4C已实现，当前schema v3 |
| WebUI source | `../webui/` | 独立Vite/TypeScript工程，以Forge电台3D工作台为主；默认直接控制，在线空动作目录自动只读verify/refetch；不含参考假后端、Worker、浏览器MQTT或小智音频桥 |
| WebUI runtime snapshot | `web/` | 由`npm run build`生成的离线哈希JS/CSS、HTML、3D模型、图片和GIF；递归纳入wheel与PyInstaller，生产无需Node |
| Message Bus tests | `tests/test_message_bus.py` | Phase 1已实现 |
| Message contract tests | `tests/test_messages.py` | Phase 1已实现 |
| Config tests | `tests/test_config.py` | 覆盖既有配置、默认免令牌模式、TTS增益、对话端点与云超时余量、mDNS刷新周期及知乎官方域名/边界配置 |
| Runtime tests | `tests/test_runtime.py` | 覆盖三Gateway、Broker、语音组件装配、动作/stop、隔离与端口闭环 |
| Storage tests | `tests/test_storage.py` | Phase 2+4A+4C已实现 |
| Command repository tests | `tests/test_command_repository.py` | Phase 4C已实现幂等、转移、历史与重启恢复 |
| Web tests | `tests/test_web.py` | 覆盖默认直控、Origin限制、可选Bearer失败关闭、既有控制面、批量结果、对话投影、知乎错误/朗读，以及Forge首页和嵌套静态资源 |
| Zhihu tests | `tests/test_zhihu.py` | 覆盖官方URL、只读参数、额度、错误、浏览器安全整数、响应上限、无重试、并发上限、密钥脱敏和非持久查询结果 |
| Narration tests | `tests/test_narration.py` | 覆盖稳定设备目标、文本边界、空文本拒绝和TTS复用 |
| Static packaging smoke | `tests/packaging/static_assets_smoke.py` | 本机源码与PyInstaller onefile实跑均通过；GitHub矩阵已配置macOS/Windows构建及执行，远程结果待本轮push |
| Windows source handoff | `scripts/portable/`、`scripts/windows/`、`docs/WINDOWS_PORTABLE_GUIDE.md` | 源码ZIP与密钥TXT分离；恢复现有EVA身份并完成锁定依赖、Opus、测试和启动，明确不生成EXE；实体Windows待验收 |
| MQTT Broker tests | `tests/test_mqtt_broker.py` | Phase 3+4D已实现凭据与三传输发放边界 |
| Device MQTT tests | `tests/test_device_mqtt.py` | 覆盖上下行身份、命令、语音控制、VAD/session边界与transport隔离 |
| Device UDP tests | `tests/test_device_udp.py` | 覆盖AES-CTR数据包、session、frame_ref、VAD、乱序和关闭清理 |
| Device TCP tests | `tests/test_device_tcp.py` | Phase 4D已实现真实Socket认证、边界、替换、查询、动作/stop与关闭覆盖 |
| Device WebSocket tests | `tests/test_device_ws.py` | 覆盖真实Runtime握手、音频引用、VAD/goodbye、三传输隔离、动作/stop与资源释放 |
| Device Manager tests | `tests/test_device_manager.py` | Phase 4A+4D已实现会话去重、每传输隔离、优先级切换、超时和重启恢复 |
| Device Verifier tests | `tests/test_device_verifier.py` | Phase 4B+4D已实现关联、错误目标/ID/transport、超时与关闭中断 |
| mDNS tests | `tests/test_mdns.py` | 覆盖注册/注销、自动地址更新、瞬时回环保护、更新失败保留旧记录与后续重试恢复 |
| Cloud/Opus/ASR/LLM/TTS tests | `tests/test_cloud.py`等 | 覆盖文本/工具流协议、完整句后工具顺序、严格JSON、PCM增益/跨块采样、编解码、三端点并发、VAD抖动、硬上限、背压、截断、原子取消和失败清理 |
| Robot Tool tests | `tests/test_robot_tools.py` | 5项覆盖目录收窄、目标锁定、完成、参数拒绝、失败/取消安全stop |
| WakeGate tests | `tests/test_wake_gate.py` | 覆盖笑声顺序/跨平台查询重试、循环、上屏、VAD-only不续时、非空partial取消计时、连续空final熔断、8秒静默、TTS后串行工具、失败、笑声复用和新session恢复 |
| Dispatcher tests | `tests/test_dispatcher.py` | Phase 4C+4D已实现队列/stop/超时、transport锁定、切换断线、音效排空完成判据与集群拆分 |

## 真机与固件状态

| 项目 | 状态 |
|---|---|
| EVA固件 | `2.0.16`已用ESP-IDF 5.5.5完整构建；应用镜像3,831,136字节，SHA256 `03377107cb829ce741dd5239d6d87c0d207b0f8a889532c45b953d9ae44ba9a1`；正式MQTT显式mDNS重连源码已推送`codex/otto-portable@c4ad28e45adb5f565469d4c14b52aedcb74c1ffb` |
| EVA1 | `e072a1f71184`，`192.168.122.127`、MQTT online、固件2.0.16；无需写死IP或重新配网即自动hello，单机换网walk 5.238秒完成；三机批次命令`c0ea38d2-18ca-4935-95c7-fd96b75e3875`在5.222秒完成 |
| EVA2 | `aca704ed89a8`，`192.168.122.117`、MQTT online、固件2.0.16；串口完整烧录后屏显名称正确、15个动作，单会话MQTT+UDP ASR/LLM/TTS成功；三机批次命令`aa933b9f-1565-421a-ac94-e1d84d89777d`在6.229秒完成 |
| EVA3 | `288485478f34`，`192.168.122.59`、MQTT online、固件2.0.16；屏显`EVA3/2.0.16`，受保护发放独立凭据/Topic，15个动作；三机批次命令`1856da68-5a84-428a-b71d-50d4b252108c`在6.221秒完成 |
| 三机隔离 | batch `array-eva1-eva2-eva3-20260919-01`请求3、接受3、失败0；三条命令均独立走完requested/published/accepted/moving/completed，最终三台online/idle且无Gateway reject/publish failure |
| 本地音效 | 奶龙笑声约2.0065秒、24 kHz OpusHead/34个60 ms包；EVA1连续两轮观测`moving/laugh/busy=true → idle/false`，不再产生15秒Dispatcher悬挂 |
| 安全与恢复 | 无`current_token`改配被拒绝；相同命令ID仅重放缓存ACK；Server/Broker重启后设备重新hello、verify与WebUI恢复；EVA3发放只命中目标MAC且临时密钥文件已删除 |
| 未完成 | EVA1看山动作图切换/恢复目视确认、多设备并发语音/工具隔离、WebSocket真机Profile及实体Windows局域网 |
