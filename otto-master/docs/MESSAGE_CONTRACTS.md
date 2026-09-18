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
audio.input.activity
audio.input.finished
audio.output.frame
voice.endpoint.detected
voice.session.state.changed
voice.session.closed
voice.transcription.started
voice.transcription.partial
voice.transcription.completed
voice.transcription.displayed
voice.transcription.failed
voice.tool.requested
voice.tool.completed
voice.tool.failed
voice.wake.accepted
voice.wake.rejected
voice.command.window.opened
tts.synthesis.requested
tts.synthesis.started
tts.synthesis.completed
tts.synthesis.failed
tts.playback.requested
tts.playback.started
tts.playback.finished
tts.playback.failed
robot.action.requested
robot.action.accepted
robot.action.completed
robot.action.failed
robot.stop.requested
robot.stop.accepted
robot.stop.completed
robot.stop.failed
device.conversation.start.requested
device.conversation.stop.requested
device.conversation.control.accepted
device.conversation.control.failed
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
- `tts.synthesis.completed`只表示单句云合成和Opus编码完成。当前`tts.playback.finished`表示服务端已按60 ms节奏发送全部帧并成功发出`tts stop`，不是扬声器物理播放ACK；未来有设备完成事件后再提升其语义。

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

`test0.9`新增并冻结以下运行时语义：

- `voice.endpoint.detected`表示auto模式下某个一次性服务端端点获胜，payload的`method`为`partial_stability`或`vad_silence`。后者必须先在同一`device_id + session_id + utterance_id`观测`speaking=true`，再连续静音达到配置窗口；ASR随后停止接收新帧、排空已入队音频并请求Provider final。它不等同设备发送了`listen/stop`。
- ASR、LLM和TTS之间使用有界生产者/消费者队列；取消、队列满和下游失败必须向上游传播，不能遗留后台生产任务。
- `audio.input.activity`至少携带`device_id + transport + session_id + utterance_id + speaking`；只有精确匹配当前轮且`speaking=true`才能取消8秒静默计时，旧轮、跨设备和`false`事件不能延长窗口。
- ASR final的`text`允许为空以保留Provider事实，但空白final不得上屏、调用LLM或永久停在`recognizing`；WakeGate只触发一次本地笑声并重新开放下一条utterance，重复或过期final无效。
- 正常回答完成后不关闭设备session，而是重新进入`laughing`；本地笑声完成后用一次`tts start → stop`让固件建立新的`listen/start`和utterance，再进入下一轮识别。
- 8秒内没有真实VAD/partial时，Server发送`goodbye`并发布`voice.session.closed(reason=server_goodbye)`；按钮退出由设备发送`goodbye`并发布`voice.session.closed(reason=device_goodbye)`。两者都必须回到`waiting`并释放音频引用。
- 异常关闭仍失败关闭；失败状态只允许由新的不同session重新开始，迟到的旧session消息不能复活对话。

### 7.1 设备文字上屏映射

固件外部协议已有两种文字显示入口：

```json
{"type":"stt","text":"用户识别文字"}
{"type":"tts","state":"sentence_start","text":"助手回答文字"}
```

第一条显示为`user`消息，第二条显示为`assistant`消息。Otto Master在加固轮中已实现两者：匹配当前`device_id + session_id + utterance_id`的ASR final先转换成`stt`，成功后发布`voice.transcription.displayed`并启动LLM；回答句继续由`sentence_start`显示。重复、过期或其他设备的final被忽略，上屏失败则不调用LLM并安全关闭会话。

### 7.2 LLM工具调用与结果

DeepSeek工具名使用API兼容的`self_otto_*`，Server再映射到设备动作目录。模型只收到当前语音设备的白名单Schema，不能提供或覆盖`device_id`、transport、Topic、confirmation或command ID。每轮最多一个工具调用：工具出现前仍未形成完整句子的内部文本前缀会被丢弃；若完整句子已经提交给TTS，则必须先完成这些句子的播放并发送`tts stop`，再串行执行工具。工具后继续输出文字、多个调用、未知工具、非法JSON或越界参数全部失败关闭。

工具生命周期发布以下内部事件：

```text
voice.tool.requested
voice.tool.completed
voice.tool.failed
```

三者至少携带`device_id + session_id + utterance_id + tool_call_id + tool_name`。`completed`额外携带持久`command_id + command_type + status=completed`以及可选`action`；`failed`只携带稳定、截断后的`error_code`。原始Provider响应、认证信息和任意模型错误文本不得进入事件。参数的权威审计记录位于Dispatcher持久命令payload，不在事件中复制第二份。

`voice.tool.requested`不代表设备已接收或动作完成；只有Dispatcher持久状态到达`completed`才允许发布`voice.tool.completed`。纯工具成功轮不生成TTS；若上段规则已经提交完整前置句，只播放该前置句，不再朗读动作结果。失败轮只使用固定用户提示。TTS与动作必须串行，不能一边说话一边驱动舵机。显式`laugh`若已真实完成，可直接充当下一轮笑声门禁，不得再提交第二次笑声。

普通`ChatMessage`当前只表示可朗读文本，不支持OpenAI结构化tool/tool-result历史。因此工具轮不写成虚构的assistant“已执行”文本；工具审计由上述事件和Command Repository负责。

### 7.3 Web对话投影与批量命令

Web Gateway维护按稳定`device_id`隔离的有界对话投影，只消费已经脱敏的`voice.session.*`、转写、TTS、工具和失败事件。投影包含当前或最近的session/utterance、用户文字、助手文字、工具状态和更新时间；新session建立后，旧session的迟到事件不得覆盖它。投影不是新的领域事实来源，刷新或事件流重连时仅用于恢复控制台视图。

批量动作与批量stop不是广播消息。Web Gateway必须把1至16个显式、唯一的`device_id`拆成独立Dispatcher调用，并逐设备返回成功或稳定错误；空目标、通配符、名称、IP和未确认请求在进入Message Bus前拒绝。一台设备失败不能撤销或掩盖其他设备的结果。

同一轮增加正式的批量会话控制：Web Gateway把显式目标拆成`device.conversation.start.requested|stop.requested`单设备命令，Conversation Control Service为每台设备生成唯一命令ID并等待同目标、同传输、同correlation的`device.conversation.control.accepted|failed`。Gateway仅编码为`{"type":"otto_conversation","id":"...","command":"start|stop"}`；固件ACK为`otto_conversation_ack`。ACK表示设备接受切换，不等于WakeGate已经建立session；控制台继续以`audio.input.started`和`voice.session.state.changed`显示真实状态。重复ID必须只重放ACK，不能再次切换；该链不经过旧8080诊断服务。

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
