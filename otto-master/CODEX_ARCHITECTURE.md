# Otto Master 架构合同

## 1. 总体调用模型

```text
External Input
    ↓
Gateway converts protocol payload
    ↓
Message Bus publishes internal Message
    ↓
Subscriber handles one responsibility
    ↓
Subscriber publishes state/result/command
    ↓
Gateway or Storage consumes output
```

模块之间不以任意函数调用构成业务流程。Runtime允许在启动时注入依赖；运行中的业务协作通过消息总线完成。

## 2. Runtime

文件：`src/otto_master/runtime.py`

职责：

- 读取已验证的配置对象。
- 创建消息总线、Gateways、服务、设备管理器、Dispatcher和Storage。
- 注册消息订阅。
- 启动后台任务并执行优雅关闭。
- 管理唯一的Worker Pool。

禁止：

- 解析ESP32协议。
- 直接调用ASR、LLM、TTS。
- 直接执行机器人动作。
- 拼接SQL或保存领域数据。

## 3. Messages

文件：`messages.py`

职责：

- 定义 `Message`、`MessageKind` 和必要的不可变值对象。
- 提供消息版本字段和关联字段。
- 保持与具体Gateway、数据库和云厂商无关。

禁止：

- 持有WebSocket、Task、Lock、数据库连接或文件句柄。
- 把原始音频塞入需要持久化的普通消息。

## 4. Message Bus

文件：`message_bus.py`

职责：

- 异步发布、订阅和取消订阅。
- 支持精确主题；通配主题在有测试后再增加。
- 隔离订阅者异常。
- 提供可观测的队列深度和投递失败信息。

禁止：

- 解析payload。
- 写SQLite。
- 调用云API。
- 决定动作目标。
- 在MVP中引入外部Broker。

## 5. Gateways

### 5.1 Device WebSocket Gateway

文件：`gateways/device_ws.py`

职责：

- 处理WebSocket握手、设备头、hello和连接生命周期。
- 区分JSON文本帧与二进制Opus帧。
- 将设备消息翻译为内部消息。
- 将内部设备命令翻译回Xiaozhi兼容消息。
- 用每设备Bearer token、MAC派生device_id和client_id交叉验证身份。
- 只在有界短期内存保存raw Opus，并向Message Bus发布`utterance_id + sequence + frame_ref`。

禁止：

- 判断唤醒词。
- 调LLM。
- 选择集群目标。
- 直接写数据库。

当前实现边界：Phase 4D实现Xiaozhi WebSocket v1 hello、listen/start|stop|detect、abort、raw Opus二进制帧及Otto查询/动作/stop文本扩展。v2/v3、Opus解码、ASR/TTS和真机均未实现或验收。

### 5.2 Embedded MQTT Broker

文件：`gateways/mqtt_broker.py`

职责：

- 在Otto Master进程内启动和关闭MQTT 3.1.1 Broker。
- 监听局域网MQTT端口并执行设备认证和Topic ACL。
- 暴露Broker健康状态，不把Broker库类型泄漏给业务模块。
- 运行时凭据只保存到`.local-secrets`，设备凭据绑定稳定device_id和client_id。

禁止：

- 解析Otto动作JSON。
- 直接调用Dispatcher或写SQLite。
- 允许匿名连接或允许设备跨MAC访问Topic。

### 5.3 Device MQTT Gateway

文件：`gateways/device_mqtt.py`

职责：

- 订阅 `otto/v1/devices/+/up`，按Topic提取稳定device_id。
- 将hello、heartbeat、动作ACK、状态和动作目录翻译为内部Message。
- 将Dispatcher的单设备命令编码后发布到精确down Topic。
- 维护MQTT连接重试、命令超时和协议级指标。

当前实现边界：Phase 4A已实现安全上行、重连和协议指标；Phase 4B已实现白名单只读查询；Phase 4C已实现Dispatcher专用动作/stop命令编码、精确down发布、ACK/状态映射和非retain边界。当前仅fake设备验收，不代表固件或真机已通过。

规则：

- MQTT是设备网络总线，不替代进程内Message Bus。
- MQTT动作ACK只产生accepted结果；动作完成由后续idle状态产生。
- 广播先由Dispatcher拆成单设备命令，Gateway不发布通配动作命令。
- Phase 4D起，共享设备JSON由`gateways/device_protocol.py`校验和翻译；MQTT Gateway只发送`payload.transport=mqtt`的命令。
- 动作和停止消息禁止retain；QoS 1启用前必须完成设备命令ID去重。

### 5.4 Legacy Device TCP Gateway

文件：`gateways/device_tcp.py`

职责：

- 兼容当前 `master.local:8765`、`otto-master/1` 换行JSON链路。
- 在MQTT迁移期间提供上线、心跳、状态、动作、停止和OTA回退。
- 将TCP和MQTT响应转换为相同内部领域消息。
- 首帧同时验证协议、MAC、token和已发放client_id；限制连接数、hello超时和单帧大小。
- 同一device_id的新认证连接替换旧连接，并使旧连接立即失去投递资格。

TCP不是第二套业务模型。Phase 4D已用真实loopback Socket验证查询、动作、stop、替换连接、错误身份、超限帧和关闭。Dispatcher只生成一次领域命令，由Device Manager选择实际传输，并把 `transport=mqtt|tcp|websocket` 写入持久请求；命令发布后不跨传输重放。

### 5.5 Web Gateway

文件：`gateways/web.py`

职责：

- 提供WebUI静态资源、REST、状态推送和OTA下载。
- 校验输入并发布内部命令。
- 将查询结果转换为HTTP响应。
- 用有界事件历史、服务端stream ID和cursor支持浏览器断线恢复。
- 设备发放接口与Browser API分离；只有受保护的发放响应可以返回该设备自己的MQTT、TCP和WebSocket凭据。

WebUI不能直接获得或持有设备Socket或MQTT设备凭据，也不能指定任意MQTT Topic。

### 5.6 Cloud Gateway

文件：`gateways/cloud.py`

职责：

- 统一HTTP/WebSocket客户端、超时、重试和认证头。
- 屏蔽云厂商传输细节。
- 不保存业务会话状态。

Phase 5首个Provider锁定为火山引擎豆包语音：ASR使用双向流式WebSocket和当前官方二进制协议，TTS使用单向流式HTTP并直接请求PCM。Cloud Gateway负责火山认证头、请求ID、协议帧和Provider错误翻译；ASR/TTS Service只能看到规范化输入、输出和稳定错误，不能解析火山原始响应。

API Key只从 `OTTO_ASR_API_KEY`、`OTTO_TTS_API_KEY` 对应环境变量读取，不得进入配置文件、Message、SQLite、Browser事件或设备协议。完整协议和烟测证据见 `docs/VOLCENGINE_SPEECH_INTEGRATION.md`。

### 5.7 mDNS Gateway

文件：`gateways/mdns.py`

职责：

- 使用Python `zeroconf` 发布 `master.local` 和服务记录。
- 在关闭时注销服务。
- 保持macOS和Windows一致行为。

## 6. Device Manager与Session

文件：`devices/manager.py`、`devices/session.py`、`devices/states.py`

Manager职责：

- 维护 `device_id → DeviceSession` 内存索引。
- 注册、重连、断开和查询设备。
- 记录每台设备可用传输和当前首选传输，设备主键始终是MAC派生device_id。
- 不持久化Socket对象。

Session职责：

- 保存单台设备按transport隔离的在线/心跳/Profile，以及当前首选传输的协议能力和状态机。
- 串行命令队列由Dispatcher按device_id持有；短期音频当前由WebSocket Gateway有界保存，Phase 5再抽为共享AudioFrameStore。
- 与Dispatcher配合，确保同一机器人动作不发生无序并发。
- 区分 `accepted`、`moving` 和 `completed`，不能把MQTT立即ACK当作动作完成。

当前实现边界：Phase 4A的Session保存身份、连接状态、固件、动态IP、能力、动作状态和动作目录；Phase 4C的Dispatcher按device_id提供串行动作队列并关联completed；Phase 4D新增`available_transports`、固定优先级、非首选快照隔离和逐transport降级。原始Socket仍由对应Gateway持有，不进入Session或SQLite。

### 6.1 Device Verifier

文件：`devices/verifier.py`

- 只通过Message Bus发起状态和动作目录查询，不直接访问任一Gateway连接。
- 验证所选Gateway健康、唯一online Session、实际传输、心跳和设备能力。
- pending请求同时匹配correlation ID、响应topic、device target和锁定transport；超时、断线、传输切换和Runtime关闭均失败关闭。
- 输出逐步验证报告，不把网络写入成功当成设备连接验证通过。

推荐状态：

```text
offline → sleeping → wake_check → ack_playing
        → listening → thinking → speaking / executing → sleeping
```

## 7. Services

### ASR

输入按设备隔离的音频描述或短期 `frame_ref`，管理一个utterance的开始、partial、final、失败和取消，输出规范化转写结果。不得判断唤醒词或机器人动作，也不得持久化原始音频。

### LLM

输入规范化对话请求，输出文本回复或结构化意图。不得直接发送设备消息。

### TTS

输入文本、目标设备和音色配置，输出可发送的有序音频帧引用及合成状态。不得改变唤醒状态，也不得把“合成完成”误当作“设备播放完成”。

### WakeGate

- 管理每台设备的唤醒状态和超时。
- 休眠状态只允许识别配置的呼叫语句。
- 唤醒成功后请求TTS回应，并等待播放完成消息。
- 播放完成后开启有限命令窗口。
- 任意失败按休眠处理。

## 8. Dispatcher

文件：`dispatch/dispatcher.py`

职责：

- 解析已经验证的领域动作命令。
- 将目标解析为单设备、设备组或明确集群广播。
- 为每台设备分别排队。
- 记录投递结果和关联ID。

禁止：

- 解析自然语言。
- 绕过Device Manager直接访问Socket。
- 将空目标解释成全体机器人。

当前实现边界：Phase 4C已实现动作目录/参数/能力/在线预检、每设备有界串行worker、跨设备并行、stop抢占、重复command ID幂等、超时安全stop和集群stop拆分。`CommandRepository`持久化命令及每次转移；重启将未完成命令失败关闭，不重放动作。Phase 4D在接受命令时锁定当前transport；只有对应Gateway发送，错误transport事实被拒绝，传输切换使在途命令失败关闭。分组和普通动作广播仍未实现。

## 9. Audio / Opus

文件：`audio/opus.py`

职责：

- Opus与PCM的必要转换。
- 采样率、声道和帧长验证。
- 维护跨云响应块的PCM滚动缓冲，只对最终不足一帧的尾部补零。
- 不理解语义、不调用云API、不持久化用户语音。

阻塞或CPU较重的转换通过Runtime提供的Worker Pool执行。

Phase 5音频参数：设备上行以16 kHz、单声道、60 ms Opus为基线；火山TTS直接返回24 kHz、单声道、S16LE PCM，编码为60 ms Opus后下发。24 kHz下一帧为1440个采样点、2880字节PCM。采样率由设备hello与服务端hello协商，Gateway不得只凭默认值猜测。

连续音频字节属于数据面，按 `device_id + utterance_id` 存放于Device Session的有界短期内存；Message Bus只传帧序号、格式和 `frame_ref`。跨设备引用、过期引用、序号缺失/重复/倒序都失败关闭。详细合同见 `docs/VOLCENGINE_SPEECH_INTEGRATION.md`。

## 10. Storage

文件：`storage/database.py`、`storage/migrations.py`

SQLite建议实体：

- `devices`
- `device_groups`
- `messages`
- `commands`
- `command_results`
- `conversations`
- `settings`
- `schema_migrations`

规则：

- 使用迁移版本，不在业务代码里临时建表。
- 开启WAL前必须通过配置控制和测试。
- SQLite写操作集中处理，避免多任务争抢写锁。
- 消息payload持久化前做大小限制和敏感字段过滤。

## 11. Web assets

文件：`web/index.html`、`web/app.js`、`web/style.css`

第一版使用无构建步骤的原生HTML/CSS/JavaScript，由Python直接提供。不得引入独立Node.js构建链。

## 12. Import与组装规则

1. 模块不在内部创建其他高层模块实例。
2. 实例统一由Runtime创建并注入。
3. Gateway不import具体业务服务实现。
4. Service不import Web或Device Gateway。
5. Dispatcher通过Device Manager的公开接口投递，不触碰内部连接字典。
6. Storage不import Gateway、Service或Dispatcher。
7. 公共数据合同放在 `messages.py` 或明确的领域类型模块。
8. 出现循环import时先修正边界，不用延迟import掩盖设计问题。

## 13. 并发规则

- 网络I/O使用asyncio。
- 每个连接有独立接收任务。
- 每台设备的动作发送保持顺序。
- 阻塞SDK用统一线程池，不允许模块私建线程池。
- 本地模型如果以后加入，使用独立进程池并作为扩展方案。
- MQTT控制消息使用有界队列；连续音频不经过该控制队列。
- 关闭顺序：注销mDNS/停止新发现 → 停止Web接入 → 断开MQTT Gateway并排空故障事件 → 停止Device Manager → 排空Message Bus → 关闭嵌入式Broker → 刷新数据库 → 关闭进程池。

## 14. 设备传输策略

固件2.0.5的主语音协议由OTA二选一：

```text
MQTT profile: MQTT JSON控制与语音信令 + 加密UDP Opus
WebSocket profile: WebSocket JSON控制与二进制Opus
```

`master.local:8765` TCP独立于上述主协议，可在迁移期同时存在。MVP的集群控制首选MQTT，TCP作为诊断回退，WebSocket保留小智兼容。任何时刻WebUI必须展示每台设备实际使用的传输，不能只根据配置推断在线。

同一设备多条认证连接并存时，选择优先级固定为`mqtt → websocket → tcp`。非首选传输的状态和动作目录不得覆盖当前快照；单一连接或Gateway关闭只移除对应transport。已发布命令不因降级而自动重发。

MQTT、TCP、WebSocket认证和音频引用合同见 `docs/DEVICE_TRANSPORT_CONTRACT.md`；MQTT Topic、JSON、QoS和EVA真机验收见 `docs/MQTT_CONTROL_CONTRACT.md`。
