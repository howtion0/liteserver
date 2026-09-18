# Otto Master 总工程宪法

本文件定义项目不可随意漂移的目标、边界和原则。具体实现合同见 `CODEX_ARCHITECTURE.md`，施工顺序见 `docs/CONSTRUCTION_PLAN.md`。

## 1. 项目目标

构建一个跨 macOS 与 Windows 的轻量 Python 主控，用一个进程管理多台 Otto ESP32 机器人，并提供云端语音、Siri 式唤醒、动作分发、WebUI、OTA、服务发现和本地数据记录。

## 2. 产品原则

### 2.1 单进程，不等于单文件

MVP 只有一个 Python 服务进程，但必须按职责拆分模块。禁止把网络、音频、数据库和业务逻辑堆进一个大文件。

### 2.2 消息总线是内部主干

模块之间交换标准 `Message`。禁止 Gateway、WakeGate、Dispatcher 和 Storage 形成任意互调网。

### 2.3 Gateway 隔离外部协议

MQTT Topic和JSON、Xiaozhi JSON、二进制Opus、加密UDP、TCP、HTTP、mDNS和云厂商响应都停留在Gateway边界。内部模块只处理稳定的领域消息。

### 2.4 Runtime 只组装，不承载业务

Runtime 负责生命周期、依赖注入、任务启动和优雅关闭。它不进行ASR判断、不解析动作、不直接写数据库。

### 2.5 每台设备独立

设备连接、会话状态、命令队列、唤醒窗口和音频缓冲按设备隔离。一台设备失败不能拖垮其他设备。

### 2.6 唤醒失败时关闭执行通道

未验证的语音不能进入LLM、MCP或动作分发。云端不可用、超时和低置信度都按未唤醒处理。

### 2.7 持久数据和实时对象分离

SQLite保存设备身份、配置、消息摘要、命令、结果和会话记录。Socket、Task、Lock、音频缓冲和实时Provider对象只存在内存。

### 2.8 跨平台优先

不依赖macOS专用命令，不要求Docker、Java、Node.js、MySQL、Redis或另行安装MQTT Broker。MVP Broker嵌入同一个Python进程，路径、网络、信号和打包必须验证Windows行为。

### 2.9 云能力可替换

ASR、LLM和TTS必须通过窄接口适配，不允许厂商SDK类型渗透到业务状态和消息结构中。

### 2.10 可观测但不过度收集

所有重要状态变化产生结构化消息和JSONL日志。默认不长期保存原始麦克风音频；如以后增加，必须显式配置保存期限。

## 3. MVP能力边界

MVP必须具备：

1. 多台ESP32通过MQTT建立集群控制连接，并按MAC派生的稳定device_id隔离Topic。
2. MQTT动作、停止、状态、动作目录、上线和心跳流程完整可追踪。
3. 支持Xiaozhi兼容的hello、listen、stt、tts流程；MQTT模式使用加密UDP音频，WebSocket模式使用二进制Opus。
4. 云端ASR、LLM、TTS各一个可用Provider。
5. 云端两阶段唤醒：呼叫设备、设备回应、再接收命令。
6. 单设备、设备组和全体广播动作。
7. WebUI查看在线状态、实际传输、消息、动作和语音状态。
8. SQLite保存设备、事件、命令和配置。
9. OTA清单、固件下载和每设备MQTT连接配置。
10. `master.local` 跨平台mDNS发布。
11. Windows可启动、可退出、可打包。

## 4. MVP非目标

- 不构建通用多租户管理平台。
- 不复制完整小智manager-api和manager-web。
- 不实现知识库、复杂插件市场、声纹系统或商业计费。
- 不引入Kafka、RabbitMQ、Redis Streams等外部消息系统。
- 不要求外部Mosquitto、EMQX或云Broker；以后可以增加外部Broker模式，但MVP默认使用内嵌Broker。
- 不在MQTT控制Topic传连续Opus音频。
- 不要求固件2.0.5同时运行独立的WebSocket和MQTT主协议。
- 不实现本地大模型或本地ASR模型。

## 5. 模块集合

```text
Runtime
├── Message Bus
├── Gateways
│   ├── Device WebSocket
│   ├── Embedded MQTT Broker
│   ├── Device MQTT
│   ├── Legacy Device TCP
│   ├── Web / REST / OTA
│   ├── Cloud
│   └── mDNS
├── Device Manager / Sessions
├── Services
│   ├── ASR
│   ├── LLM
│   ├── TTS
│   └── WakeGate
├── Dispatcher
├── Audio / Opus
├── SQLite Storage
└── Worker Pool
```

## 6. 四条铁律

```text
Gateway只翻译协议，不做业务决策。
Message Bus只传递消息，不理解业务内容。
Dispatcher只选择目标和排队，不生成机器人意图。
Storage只持久化，不持有活连接和运行任务。
```

## 7. 数据和消息原则

- 内部消息必须包含版本、消息ID、主题、类型、来源、目标、时间和payload。
- 命令结果通过 `correlation_id` 与请求关联。
- 外部JSON不得直接作为内部消息payload长期扩散。
- 二进制Opus只在音频链路传递，不转Base64塞入普通JSON。
- 广播消息必须明确 `target_scope=cluster`，不能用空target暗示全体。
- MQTT Topic使用MAC派生device_id，不使用设备名称或动态IP。
- MQTT动作ACK只表示已接收，动作回到idle后才表示完成。
- MQTT QoS 1和重试启用前必须实现命令ID去重；动作命令禁止retain。

## 8. 版本原则

- `pyproject.toml` 的 `project.version` 是唯一版本真相源。
- Phase 0 从 `0.0.0` 开始。
- 每完成一个施工Phase，按计划提升次版本或补丁版本。
- `1.0.0` 仅在MVP验收全部通过后使用。
- 大版本升级必须由用户明确确认。
- Git施工支线使用独立的连续编号 `testN.N`。该编号只表示检查点顺序，不替代、不驱动产品版本。
- 每个阶段检查点使用一个新支线和一次验收提交；后续修复继续递增支线编号，不修改已经完成的支线历史。

## 9. 文档原则

- 当前状态以 `docs/DEV_PROGRESS.md` 为准。
- 模块实现度以 `docs/MODULE_STATUS.md` 为准。
- 每次重要施工记录到 `docs/LOG.md`。
- 架构变更必须先改宪法或架构文档，再进入实现。

## 10. 强制施工原则

- `CODEX_CONSTRUCTION_WORKFLOW.md` 是所有Phase共同的施工门禁，优先于阶段内的简写步骤。
- 每轮施工开始前必须确认上一可恢复检查点已上传GitHub，未上传不得开始新实现。
- 每轮必须先冻结Session Contract、修改范围、非目标和二元可判定的测试通过标准。
- 任一必需测试FAIL、BLOCKED或NOT RUN时，Phase不得标记完成、不得合并main。
- FAIL必须返工并重跑原测试及受影响回归，直到全部必需测试PASS。
- 测试通过后必须先更新进度、模块状态、日志和Session Contract，再创建唯一阶段提交并push。
- 只有GitHub远程支线与本地HEAD哈希一致后，才能向用户报告本轮完成。
- 施工请求持续授权创建下一 `testN.N`、验收commit和push测试支线；合并main、tag、正式发布、force-push、删除远程分支和历史改写必须单独授权。
