# 设备多传输合同

本文档冻结 Otto Master `0.4.3` 的 MQTT、TCP `otto-master/1` 与 Xiaozhi WebSocket v1 设备边界。三种外部协议必须翻译成同一套内部 Message，业务模块不得直接解析网络帧。

## 1. 传输选择

同一 `device_id` 可以同时存在多个已认证连接，Device Session 按固定优先级选择当前传输：

```text
mqtt → websocket → tcp
```

- `available_transports` 公开当前仍活跃的传输集合，`transport` 是当前首选。
- hello、heartbeat、能力、状态和动作目录都必须带已认证连接推导出的 `device_id` 与 `transport`。
- 非首选传输的状态或动作目录不得覆盖首选传输快照。
- 首选传输变化时清空旧传输的动作状态和动作目录，再等待新传输报告。
- Dispatcher在接受命令时锁定当前 `transport`，并将其写入持久命令payload。
- 每个Gateway只处理 `payload.transport` 与自身一致的命令；不匹配时静默忽略。
- 已发布的动作不得跨传输自动重试，避免机器人重复移动。传输切换使在途命令进入明确的 `disconnected` 终态。

## 2. 共享设备消息

三种Gateway把以下设备消息映射到相同内部主题：

| 设备类型 | 内部主题 |
|---|---|
| `hello` | `device.connected` |
| `heartbeat` | `device.heartbeat.received` |
| `otto_state` | `device.state.received` |
| `otto_actions` | `device.actions.catalog.received` |
| `otto_action_ack` | `robot.action.accepted`或`robot.action.failed` |
| `otto_stop_ack` | `robot.stop.accepted`或`robot.stop.failed` |
| `error` | `robot.action.failed` |

共享下行白名单：

| 内部主题 | 设备类型 |
|---|---|
| `device.state.query.requested` | `otto_query` |
| `device.actions.query.requested` | `otto_actions` |
| `device.action.execute.requested` | `otto_action` |
| `device.stop.execute.requested` | `stop` |

查询ID使用内部Message ID；动作和stop的外部 `id` 必须等于持久command ID。未知类型、错误目标、错误transport、重复JSON字段、非UTF-8、非有限数字、超限深度或超限帧全部拒绝，不能默认映射为动作。

## 3. 每设备凭据

受保护的 `POST /api/v1/ota/provision` 为设备返回同一组每设备身份材料：

- MQTT：username、password、client_id与精确up/down Topic。
- TCP：client_id与device token。
- WebSocket：client_id与Bearer device token。

token与MQTT password当前是同一随机secret，但普通设备读取API、设置、manifest、事件、日志和SQLite不得返回它。Gateway必须同时验证MAC派生的 `device_id`、token与已发放client_id；只提供一个正确字段不能通过认证。

当前TCP和WebSocket Profile是局域网兼容入口，默认没有TLS。不得直接暴露到互联网或不可信网络；跨不可信网络部署必须在进入生产前增加TLS终止、证书验证和相应威胁模型。

## 4. TCP `otto-master/1`

配置默认端口为 `8765`，默认关闭。每帧是一个UTF-8 JSON对象，以LF结束；允许CRLF输入，单帧默认上限64 KiB。

首帧必须是：

```json
{
  "type": "hello",
  "protocol": "otto-master/1",
  "mac": "aa:bb:cc:dd:ee:01",
  "client_id": "otto-aabbccddee01",
  "token": "<device-secret>",
  "name": "EVA1",
  "firmware_version": "2.0.5",
  "capabilities": {"actions": true, "state": true, "stop": true}
}
```

认证成功后服务端返回：

```json
{"type":"hello","protocol":"otto-master/1","session_id":"<uuid>"}
```

规则：

- hello必须在配置超时内到达，且只能出现一次。
- 同一device_id的新认证连接替换旧连接，即使已达到不同设备连接上限也允许替换。
- 被替换连接不得继续上报消息或接收命令。
- 关闭、畸形帧或写失败生成transport限定的断线或命令失败事实。
- JSON行不得包含token以外的隐式身份来源；客户端IP只作为观测字段。

## 5. Xiaozhi WebSocket v1

入口路径来自 `server.websocket_path`，默认 `/xiaozhi/v1/`。握手必须包含：

```text
Authorization: Bearer <device-secret>
Protocol-Version: 1
Device-Id: aa:bb:cc:dd:ee:01
Client-Id: otto-aabbccddee01
```

首个设备文本帧必须是官方v1 hello：

```json
{
  "type": "hello",
  "version": 1,
  "transport": "websocket",
  "name": "EVA1",
  "firmware_version": "2.0.5",
  "features": {
    "mcp": true,
    "otto_state": true,
    "otto_actions": true,
    "otto_stop": true
  },
  "audio_params": {
    "format": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration": 60
  }
}
```

服务端返回带唯一 `session_id` 和播放音频参数的hello。后续文本控制帧若提供 `session_id`，必须与当前连接一致。

支持的官方控制类型：

- `listen/start`：建立新的 `utterance_id`，序号从0开始。
- `listen/stop`：发布该utterance的完成事实和帧数。
- `listen/detect`：映射为 `voice.wake.candidate.received`。
- `abort`：以abort原因关闭当前utterance。
- `mcp`与`goodbye`：本检查点安全忽略，不解释为动作。

Otto状态、动作目录、查询、动作、stop及ACK作为同一文本通道上的显式扩展，字段与MQTT/TCP一致。只支持二进制协议v1；`Protocol-Version: 2|3`必须拒绝。

## 6. WebSocket Opus数据面

二进制v1帧是原始Opus。只有活动的listen utterance可以接收音频；连接外、start前、stop后或空帧均失败关闭。

原始bytes不进入普通Message、JSONL、SQLite或Browser事件。Gateway只在每设备有界内存中保存短期帧，并发布：

```json
{
  "device_id": "aabbccddee01",
  "transport": "websocket",
  "session_id": "session-uuid",
  "utterance_id": "utterance-uuid",
  "frame_ref": "frame-uuid",
  "sequence": 0,
  "byte_length": 120,
  "codec": "opus",
  "sample_rate": 16000,
  "channels": 1,
  "frame_duration_ms": 60,
  "protocol_version": 1
}
```

读取帧必须同时匹配 `device_id + utterance_id + sequence + frame_ref`，任一字段错误都返回失败且不能消费正确帧。缓冲达到每设备上限时淘汰最旧帧；连接结束清理该session全部帧。Phase 5将把这条临时Gateway缓冲抽成共享的短期AudioFrameStore，并保持相同引用合同。

## 7. 生命周期与健康

- Runtime启动顺序是凭据/Broker、Manager、Verifier、Dispatcher、MQTT、TCP、设备WebSocket、Web/mDNS；关闭时反向清理网络入口和在途任务。
- 单连接关闭只删除对应设备的对应transport；其他transport仍在线时立即按优先级降级。
- 整个Gateway停止发布 `device.transport.unavailable`，只影响该transport。
- `/api/v1/health` 分别公开MQTT、TCP与设备WebSocket健康，不用一个Gateway状态代替另一个。
- Runtime关闭必须释放HTTP、MQTT与TCP端口，清空WebSocket和音频缓冲，不遗留后台任务。

## 8. 当前验收边界

`0.4.3` 使用真实loopback TCP、HTTP/WebSocket和内嵌MQTT Broker配合fake设备验证协议、鉴权、隔离、动作/stop生命周期和资源释放。Phase 4E又在EVA1/EVA2固件`2.0.6`上验证了真实MQTT hello/heartbeat、14动作查询、action/stop、6步walk、双机隔离、重复ID和Server/Broker重启恢复。`test0.9`进一步在EVA1固件`2.0.11`上完成MQTT信令、AES-CTR UDP Opus、ASR/TTS、循环WakeGate和单设备LLM工具链。尚未完成：

- EVA2语音/工具链和两台设备并发会话隔离。
- EVA1/EVA2的WebSocket语音Profile真机验收。
- TLS、Xiaozhi二进制v2/v3、跨传输动作重放和实体Windows部署。

fake测试通过不能替代固件、真机、安全动作、实体Windows或生产网络验收。
