# Otto Master施工计划

本文档规定从纯脚手架到 `1.0.0` MVP 的实现顺序。后续Phase不能以“文件存在”代替“功能通过验收”。

## 总路线

```text
Recovery version 0.1.0  branch test0.1  Phase 0 + Phase 1恢复基线
Phase 2  version 0.2.0  branch test0.2  SQLite持久化与迁移
Phase 3  version 0.3.0  branch test0.3  WebUI + REST + OTA + mDNS + Embedded MQTT Broker
Phase 4  version 0.4.0  branch test0.4  MQTT集群控制 + TCP回退 + WebSocket兼容
Phase 5  version 0.5.0  branch test0.5  Opus + 云端ASR/TTS
Phase 6  version 0.6.0  branch test0.6  Siri式WakeGate
Phase 7  version 0.7.0  branch test0.7  LLM + Dispatcher + Otto动作
Phase 8  version 0.8.0  branch test0.8  多设备集群、日志和容错
Phase 9  version 0.9.0  branch test0.9  Windows打包与端到端验收
MVP      version 1.0.0  branch test1.0  EVA1/EVA2完整链路通过
```

Phase 0和Phase 1已在强制门禁建立前完成但没有GitHub检查点，因此不得伪造两段历史；`test0.1` 是一次性恢复基线。支线编号是连续施工检查点，不是产品版本。若中途增加修复检查点，使用下一个自然编号，后续阶段顺延，不复用旧编号，也不特别处理 `test0.9` 到 `test1.0` 的变化。

## 全Phase强制施工门禁

每个Phase以及Phase内的补充检查点，都必须完整执行 `CODEX_CONSTRUCTION_WORKFLOW.md`：

```text
[ ] 上一检查点已经push到GitHub并核对远程哈希
[ ] 已完整阅读当前施工依据
[ ] 已建立本轮Session Contract
[ ] 每条验收标准都有具体测试或人工观测方法
[ ] 只施工契约内范围
[ ] 失败测试已经返工并重跑
[ ] 所有必需测试最终PASS
[ ] DEV_PROGRESS、MODULE_STATUS、LOG和Session Contract已更新
[ ] 本轮唯一提交已push到新的testN.N支线
[ ] 远程支线哈希与本地HEAD一致
```

任意一项未满足，Phase状态只能是未开始、进行中、失败或阻塞，不得写成完成。历史测试、其他传输或其他设备的结果不能替代本轮验收。

## Phase 0：文档脚手架

目标：建立不含业务实现的工程骨架和施工治理体系。

交付：

- `pyproject.toml`、`config.yaml`、`.env.example`、`.gitignore`
- 空Python模块、空Web资源和空测试文件
- 宪法、架构、Git、测试、Session Contract
- 施工计划、进度、模块状态、日志、调试和消息合同
- MQTT控制合同和EVA1/EVA2真机验收基线
- 运行产物目录，但不伪造数据库、日志和固件

验收：

- [x] 工程位于 `liteserver/otto-master/`
- [x] 目录结构完整
- [x] 业务文件没有实现代码
- [x] 配置不含真实密钥
- [x] 当前状态明确标记为不可运行

## Phase 1：Runtime与Message Bus

目标：建立可启动和可关闭的异步内核。

实现：

1. 配置加载和环境变量替换。
2. `Message`、`MessageKind`和验证规则。
3. Message Bus publish/subscribe/unsubscribe。
4. Runtime依赖组装、后台任务和优雅关闭。
5. 基础结构化日志。

约束：

- 不接硬件、不接云API、不建WebUI。
- Message Bus不理解payload。
- 订阅者异常不能终止总线。

验收：

- [x] `python -m otto_master` 可以启动并干净退出
- [x] `ruff check src tests`
- [x] `mypy src`
- [x] `tests/test_message_bus.py`覆盖投递、多个订阅者和异常隔离

## Phase 2：SQLite持久化

目标：建立本地持久数据的单一入口。

实现：

1. 数据库连接生命周期。
2. 版本化迁移。
3. devices、groups、messages、commands、results、settings表。
4. 消息日志订阅者和敏感字段过滤。
5. 单写者或受控事务策略。

验收：

- [x] 首次运行生成有效 `data/otto.db`
- [x] 重复迁移幂等
- [x] 并发写入测试无未处理锁错误
- [x] Runtime关闭前刷新在途数据

## Phase 3：Web、REST、OTA、mDNS与Embedded MQTT Broker

目标：把当前控制面放进同一个Python进程。

实现：

1. FastAPI应用和静态Web资源。
2. 设备、事件、命令和配置API骨架。
3. Web状态推送。
4. OTA manifest与固件下载。
5. `zeroconf`发布 `master.local`。
6. 在同一Python进程启动和关闭MQTT 3.1.1 Broker。
7. OTA按MAC下发 `master.local:1883`、独立client_id、凭据和每设备Topic。
8. Broker健康状态、匿名访问关闭和最小Topic ACL。
9. 对 `amqtt>=0.12,<0.13` 做macOS与Windows可行性验证，通过后才锁定依赖。
10. 按 `docs/SERVER_CONSOLE_REQUIREMENTS.md` 实现Server状态、Broker状态、设备列表、事件流和设置页的P0骨架。
11. Browser API只通过Web Gateway、Message Bus和Dispatcher进入控制链，浏览器不得直连MQTT Broker。

验收：

- [x] 浏览器可打开WebUI
- [x] macOS和Windows地址行为一致
- [x] 固件不存在时明确返回404
- [x] mDNS注册和注销均可观测
- [x] 无需另装Broker即可在macOS和Windows启动MQTT服务
- [x] 匿名MQTT连接被拒绝，设备不能订阅其他MAC的down Topic
- [x] OTA返回 `otto/v1/devices/{device_id}/up|down`，不返回固定IP
- [x] Broker候选在macOS和Windows完成启动、鉴权、消息往返、关闭及PyInstaller冒烟测试
- [x] WebUI显示Server、Broker、SQLite、mDNS和OTA组件的真实健康状态
- [x] 页面刷新或事件流重连后，快照与增量状态一致
- [x] Browser API和WebUI不返回MQTT密码、云API Key或任意Topic发布入口；受保护的设备发放接口除外

## Phase 4：MQTT集群控制、TCP回退与Device Session

目标：以当前TCP真机行为为基线，让EVA1/EVA2通过MQTT完成等价集群控制，同时保留TCP诊断回退和Xiaozhi WebSocket兼容。

实现：

1. MQTT Gateway订阅每设备up Topic并向精确down Topic发送命令。
2. hello、heartbeat、动作目录、状态、ACK和错误的协议翻译。
3. 补齐固件MQTT `stop`、上线心跳和命令ID去重。
4. Device Manager按MAC派生device_id注册、重连和断开，名称只作显示。
5. 每设备Session状态机、串行动作队列和accepted/moving/completed状态。
6. TCP `otto-master/1` Gateway作为迁移回退，输出相同内部Message。
7. Xiaozhi WebSocket握手、JSON和二进制Opus继续作为兼容Profile。
8. WebUI显示在线、动态IP、名称、MAC、固件、能力、实际传输和心跳。
9. 实现只读连接验证和需要安全确认的动作验证，逐步展示查询、ACK、moving和idle结果。
10. 动作控制使用设备动作目录和参数Schema；stop独立高优先级，广播拆成单设备结果。

Phase 4A检查点（`test0.4`）只完成不移动设备的上行联调端：

- [x] Master凭据连接内嵌Broker并只订阅设备up Topic
- [x] hello、heartbeat、state、actions、ACK和error严格翻译为内部Message
- [x] MAC派生Session去重、重连代次和`connecting → online → stale → offline`状态
- [x] SQLite schema v2保存设备快照与动作目录，重启不伪造online
- [x] Web列表、详情、动作目录和事件流读取实时Manager状态
- [x] 两个fake设备通过受保护OTA和真实Broker同时联调且无动作下行
- [x] Phase 4B精确down状态/动作目录查询、correlation、超时与只读验证报告
- [x] Phase 4C fake设备MQTT动作、stop、持久命令历史与完整生命周期
- [x] Phase 4D认证TCP回退、Xiaozhi WebSocket v1、fake多传输隔离与动作/stop闭环
- [ ] 固件MQTT改造、Broker恢复与EVA1/EVA2真机验收

验收：

- [ ] EVA1和EVA2通过MQTT同时连接，当前IP变化不影响身份
- [ ] 两台设备均返回完整14动作目录
- [ ] EVA1执行 `swing → stop → idle` 时EVA2状态不变
- [ ] EVA2执行 `swing → stop → idle` 时EVA1状态不变
- [ ] 相同命令ID重复投递不会执行两次
- [ ] MQTT动作和stop消息均不retain
- [ ] 重连不产生重复在线记录
- [ ] 一台设备断开不影响另一台
- [ ] Broker重启后设备可重连；失败时WebUI不伪报在线
- [ ] TCP回退启用时WebUI明确显示 `transport=tcp`
- [ ] 只读连接验证不移动机器人，并逐项验证心跳、状态查询和动作目录
- [ ] 安全动作验证必须完成 `accepted → moving → idle`，超时路径自动stop并确认idle
- [ ] 页面只显示publish或ACK时不得标记动作完成
- [ ] WebUI刷新后能恢复正在执行命令及最终结果

## Phase 5：Opus与云端ASR/TTS

目标：打通语音上行和语音回传，不接LLM动作。

Provider和协议已经通过独立烟测冻结，完整设计、数据流、实测证据和复用边界见 `docs/VOLCENGINE_SPEECH_INTEGRATION.md`。烟测通过只证明云协议可行，不表示Phase 5已经开始或完成。

实现：

1. 在 `audio/opus.py` 实现16/24 kHz、单声道、60 ms帧的Opus/PCM转换、跨块滚动缓冲、尾帧补齐和参数校验。
2. 按 `device_id + utterance_id` 建立有界、短生命周期音频数据面；Message Bus只传元数据和 `frame_ref`，不传或持久化原始音频。
3. 在 `gateways/cloud.py` 接入火山当前API Key鉴权、ASR二进制WebSocket协议和TTS流式HTTP，并把Provider错误转换为稳定内部错误。
4. 在 `services/asr.py` 实现火山ASR 1.0时长版适配器和fake provider；输入为16 kHz PCM，输出partial/final/failed规范消息。
5. 在 `services/tts.py` 实现火山TTS 2.0适配器和fake provider；直接请求24 kHz PCM，再编码为60 ms Opus，避免MP3和FFmpeg转码链。
6. 严格实现 `tts start → sentence_start → Opus frames → stop`；start后任何失败、断开或取消都在清理路径尝试stop。
7. MQTT Profile使用现有加密UDP Opus，WebSocket Profile使用二进制Opus；两者共享相同内部音频和TTS状态合同。
8. 以 `tts stop` 后设备重新进入listening作为当前播放完成信号；显式 `tts_finished` 仅在固件以后增加时使用。
9. 复用小智Server的连接生命周期、PCM滚动缓冲、60 ms节奏和TTS状态顺序；火山鉴权和二进制帧格式以当前官方文档为准，不复制旧协议。
10. 网络层仅使用现有 `httpx`、`websockets`、`opuslib-next`；不增加仅macOS可用的依赖，Windows `libopus` 和PyInstaller收集必须进入验收。

验收：

- [ ] 编解码单元测试覆盖16/24 kHz、60 ms帧、跨块缓冲、尾帧补齐和非法参数
- [ ] fake ASR/TTS覆盖partial、final、取消、超时、限流、乱序和所有stop清理路径
- [ ] 真实火山外部测试返回目标文本；无Key必须记为NOT RUN，不能算PASS
- [ ] EVA1和EVA2分别完成语音转写且不串设备、utterance或帧序号
- [ ] 指定文本分别能在EVA1和EVA2播放，另一台不播放，目标设备最终回到listening
- [ ] MQTT加密UDP与WebSocket二进制两种Profile分别完成至少一个闭环
- [ ] API Key无效、403、429、5xx、超时和设备断开不使Runtime崩溃或遗留会话
- [ ] 默认日志、SQLite和Browser事件流不保存原始语音、Base64音频、API Key或认证头
- [ ] macOS与Windows通过Opus编解码、fake Provider集成和PyInstaller导入冒烟

## Phase 6：Siri式WakeGate

目标：实现“你好设备 → 设备回应 → 再说命令”。

实现：

1. sleeping、wake_check、ack_playing、listening状态。
2. 休眠时ASR结果只用于唤醒匹配。
3. 命中后TTS回应“{device_name} 在”。
4. 收到播放完成才开启6秒命令窗口。
5. 15秒连续会话窗口和显式结束。
6. 超时、低置信度和API错误全部fail-closed。

验收：

- [ ] 未说唤醒词不会触发动作
- [ ] EVA1和EVA2不会互相误唤醒
- [ ] 机器人自己的回应不会被识别成用户命令
- [ ] 命令窗口超时后恢复休眠

## Phase 7：LLM、Dispatcher与Otto动作

目标：将已唤醒命令转为受控动作并投递设备。

实现：

1. LLM适配器和结构化意图输出。
2. 动作Schema与参数范围验证。
3. Dispatcher单设备、设备组和集群路由。
4. 每台设备有序动作队列。
5. 命令结果和错误回传WebUI。

验收：

- [ ] “EVA1前进”只控制EVA1
- [ ] “EVA2后退”只控制EVA2
- [ ] 广播必须显式确认目标范围
- [ ] 非法动作或参数被拒绝

## Phase 8：集群、可观测性与容错

目标：让多设备长时间运行可检查、可恢复。

实现：

1. 分组和广播汇总结果。
2. 心跳、延迟和队列深度指标。
3. JSONL结构化日志与WebUI事件流。
4. 云API并发限制、退避和熔断策略。
5. 消息队列背压和慢消费者策略。
6. MQTT连接数、消息延迟、命令ACK、完成超时和重连指标。

验收：

- [ ] 多设备并发互不串话
- [ ] 单设备或单Provider故障被隔离
- [ ] 日志可按device_id和correlation_id追踪
- [ ] 队列达到上限时行为明确
- [ ] QoS 1重复投递测试不产生重复动作

## Phase 9：Windows打包与端到端验收

目标：从macOS开发结果生成可在Windows运行的交付物。

实现：

1. PyInstaller打包配置。
2. 静态Web资源、证书和Opus动态库收集。
3. Windows启动、退出和防火墙说明。
4. 配置、数据、日志和固件目录布局。
5. 升级与回滚文档。
6. 完成 `docs/SERVER_CONSOLE_REQUIREMENTS.md` 全部P0控制台与打包冒烟项目。

验收：

- [ ] Windows干净环境可以启动
- [ ] WebUI、mDNS、SQLite、OTA和内嵌MQTT Broker可用
- [ ] EVA1和EVA2完整语音及动作流程通过
- [ ] 退出后无残留进程和锁定数据库
- [ ] 静态资源离线可用，不依赖Node.js、CDN或外部MQTT Broker
- [ ] 浏览器长时间打开、刷新和断网恢复后，设备和命令状态保持一致
- [ ] 端口冲突、防火墙、数据目录和组件启动失败都有明确诊断

## 1.0.0 MVP验收场景

```text
1. Windows启动otto-master.exe
2. EVA1与EVA2通过master.local取得MQTT配置并连接
3. WebUI按名称和MAC显示两台设备在线及 `transport=mqtt`
4. 用户说“你好 EVA1”
5. EVA1回应“EVA1 在”，EVA2保持安静
6. 用户说“向前走两步”
7. EVA1执行，WebUI显示命令和结果
8. WebUI显式广播EVA1前进、EVA2后退
9. 两台设备独立执行并返回结果
10. 重启Master后SQLite历史仍可查看
11. 重启MQTT Broker后两台设备恢复连接和状态查询
```

Phase 4真机步骤、Topic和JSON以 `docs/MQTT_CONTROL_CONTRACT.md` 为准。当前固件 `2.0.5` 的TCP测试是行为基线，不等于MQTT验收已经通过。
