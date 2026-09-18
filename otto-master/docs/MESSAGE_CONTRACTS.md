# Message Bus消息合同

## 1. 消息信封

内部消息统一包含：

| 字段 | 含义 |
|---|---|
| `version` | 消息合同版本 |
| `message_id` | 本消息唯一ID |
| `correlation_id` | 命令与结果的关联ID |
| `topic` | 路由主题 |
| `kind` | command、event、state或result |
| `source` | 来源模块或设备 |
| `target` | 明确目标；广播不能靠空值表达 |
| `created_at` | UTC时间 |
| `payload` | 经过校验的领域数据 |

示例仅用于合同说明：

```json
{
  "version": 1,
  "message_id": "msg-uuid",
  "correlation_id": null,
  "topic": "robot.action.execute",
  "kind": "command",
  "source": "webui",
  "target": "EVA1",
  "created_at": "2026-09-18T08:00:00Z",
  "payload": {
    "action": "walk",
    "steps": 2,
    "direction": 1
  }
}
```

## 2. Kind语义

- `command`：要求某模块或设备执行动作，预期产生result。
- `event`：已经发生的事实，不要求唯一处理者。
- `state`：可覆盖的当前状态快照。
- `result`：某个command的成功、失败或部分结果。

## 3. 主题命名

使用点分层级、小写英文：

```text
device.connected
device.disconnected
device.heartbeat.received
device.state.received
device.state.changed
device.transport.unavailable
device.transport.changed
device.actions.catalog.received
device.state.query.requested
device.actions.query.requested
device.action.execute.requested
device.stop.execute.requested
device.command.published
device.command.failed
device.verification.requested
device.verification.completed
audio.input.started
audio.input.frame
audio.input.finished
audio.output.frame
voice.transcription.partial
voice.transcription.completed
voice.transcription.failed
voice.wake.accepted
voice.wake.rejected
voice.command.window.opened
tts.synthesis.requested
tts.synthesis.started
tts.synthesis.completed
tts.synthesis.failed
tts.playback.requested
tts.playback.finished
robot.action.requested
robot.action.accepted
robot.action.completed
robot.action.failed
robot.stop.requested
robot.stop.accepted
robot.stop.completed
robot.stop.failed
cluster.broadcast.requested
storage.write.requested
system.shutdown.requested
```

## 4. 目标规则

- 单设备：`target=device:<device_id>`。
- 设备组：`target=group:<group_id>`。
- 集群广播：`target=cluster:all`，且payload必须包含 `target_scope: cluster`。
- 系统模块：`target=service:wake_gate` 等明确名称。
- 禁止使用 `target=null` 表达广播。

## 5. 请求与结果

一个command产生结果时：

- 结果拥有新的 `message_id`。
- 结果的 `correlation_id` 等于命令的 `message_id`。
- 多设备广播拆为多个子命令，每个子命令独立关联结果。
- 汇总结果由Dispatcher生成，不能覆盖单设备失败信息。

## 6. 外部与内部协议边界

ESP32可能发送：

```json
{"type":"listen","state":"detect","text":"你好 EVA1"}
```

Device Gateway应转换为内部主题，而不是让WakeGate解析所有Xiaozhi字段：

```text
voice.wake.candidate.received
```

反向发送同理：Dispatcher发布领域动作，Device Gateway负责生成ESP32理解的JSON。

MQTT外部Topic和JSON合同见 `docs/MQTT_CONTROL_CONTRACT.md`，三传输共享边界见 `docs/DEVICE_TRANSPORT_CONTRACT.md`。Gateway转换后至少补充以下稳定字段：

```json
{
  "device_id": "aabbccddeeff",
  "device_name": "EVA1",
  "transport": "mqtt",
  "external_message_id": "cmd-uuid"
}
```

其中 `device_id` 从Topic与已认证连接映射交叉验证，不能只相信payload中的名称、MAC或IP。

## 7. 音频消息

- WebSocket二进制Opus帧不进行JSON Base64编码。
- MQTT Profile的连续Opus使用固件现有加密UDP数据面，不进入MQTT控制Topic。
- 普通消息只携带帧元数据或短期内存 `frame_ref`，不携带PCM、Opus或Base64音频。
- 默认不将原始音频写入消息日志或SQLite。
- 音频缓冲必须有设备归属、大小上限和生命周期。
- 音频引用必须同时核对 `device_id`、`utterance_id` 和帧序号；过期、跨设备、重复、缺失或倒序引用失败关闭。
- `tts.synthesis.completed`只表示云合成和Opus编码完成；只有设备完成证据才能产生`tts.playback.finished`。

`audio.input.frame` 的最小payload：

```json
{
  "device_id": "aabbccddeeff",
  "utterance_id": "utt-uuid",
  "frame_ref": "audio-ref-uuid",
  "sequence": 42,
  "codec": "opus",
  "sample_rate": 16000,
  "channels": 1,
  "frame_duration_ms": 60
}
```

转写结果必须包含 `device_id`、`utterance_id`、规范化 `text` 和 `is_final`。Provider名称、请求ID和延迟可作为观测字段；Provider原始响应、认证头和音频不得进入普通消息。

TTS使用两层状态：`tts.synthesis.*`描述云合成，`tts.playback.*`描述设备播放。`audio.output.frame`只携带目标设备、会话、序号、音频参数和 `frame_ref`。完整Phase 5合同见 `docs/VOLCENGINE_SPEECH_INTEGRATION.md`。

## 8. 兼容性

- 新增可选payload字段允许向后兼容。
- 删除字段、改变语义或改变类型必须提升消息版本。
- 未知topic应记录并安全忽略，不能导致Runtime崩溃。
- 未知command不能默认映射为机器人动作。

## 9. MQTT映射规则

- `hello` → `device.connected`或`device.transport.changed`。
- `heartbeat` → `device.heartbeat.received`。
- `otto_actions` → `device.actions.catalog.received`。
- `otto_action_ack(ok=true)` → `robot.action.accepted`，只表示accepted。
- 后续状态为moving → 设备Session更新执行中状态。
- 对应命令状态回到idle → `robot.action.completed`。
- `otto_action_ack(ok=false)`、超时或断线 → `robot.action.failed`。
- `otto_stop_ack(ok=true)`且状态为idle → `robot.stop.completed`。
- MQTT响应的外部 `id` 映射到内部correlation链；重复外部ID不能产生第二次执行。

Phase 4A实现说明：Gateway已生成`device.connected`、`device.heartbeat.received`、`device.state.received`、`device.actions.catalog.received`及ACK/error结果；Manager生成`device.state.changed`快照。该检查点当时尚未实现外部ID到内部命令仓库的完整关联和重复执行保护，已由Phase 4C补齐。

Phase 4B实现说明：Verifier发布`device.state.query.requested`和`device.actions.query.requested`；Gateway只将这两个白名单命令编码为精确设备down消息，并生成`device.command.published|failed`。设备响应的外部`id`进入内部`correlation_id`，Verifier再同时核对响应topic和target。该检查点当时尚未实现动作命令的完整持久关联，已由Phase 4C补齐。

Phase 4C实现说明：Dispatcher消费`robot.action.requested|robot.stop.requested`，持久命令后发布`device.action.execute.requested|device.stop.execute.requested`。Gateway只对这两个专用topic编码外部动作/stop，且外部`id`必须等于持久command ID。ACK映射为`robot.action.accepted|robot.stop.accepted`，只有同关联链的moving与idle事实才推进终态。重复ID同载荷幂等，冲突载荷拒绝。

Phase 4D实现说明：MQTT、TCP和WebSocket使用同一严格协议翻译器，所有设备事实和下行请求都携带`transport`。Session按`mqtt → websocket → tcp`选择首选传输并隔离各transport的Profile；Verifier同时核对响应transport，Dispatcher把接受命令时的transport写入持久payload。每个Gateway只处理与自身transport一致的请求，传输切换或对应Gateway不可用使在途命令进入`disconnected`，不得从另一传输重放。

Xiaozhi WebSocket v1的`listen/start`、二进制帧、`listen/stop|abort`依次映射为`audio.input.started`、`audio.input.frame`、`audio.input.finished`。帧消息只携带`device_id + utterance_id + sequence + frame_ref`及格式元数据；取帧时四项必须全部匹配，原始Opus不进入普通Message或SQLite。
