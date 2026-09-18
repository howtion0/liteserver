# Phase 5 火山引擎语音接入设计

本文档冻结 Otto Master Phase 5 的 ASR、TTS、Opus 数据流、云协议、复用边界和验收标准。它记录的是已经完成的可行性验证和后续施工合同，不表示 Phase 5 业务代码已经实现。

## 1. 当前结论

| 项目 | 决定或证据 |
|---|---|
| ASR Provider | 火山引擎豆包语音，当前账号先使用流式语音识别 1.0 时长版资源 `volc.bigasr.sauc.duration` |
| TTS Provider | 火山引擎豆包语音 2.0，资源 `seed-tts-2.0` |
| ASR传输 | 双向流式 WebSocket：`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async` |
| TTS传输 | 单向流式 HTTP：`https://openspeech.bytedance.com/api/v3/tts/unidirectional` |
| 鉴权 | 新控制台 API Key，通过 `X-Api-Key` 发送；密钥只从环境变量读取 |
| 运行时依赖 | 复用现有 `websockets`、`httpx`、`opuslib-next`，不增加FFmpeg、pydub或numpy运行时依赖 |
| 实现状态 | 云API独立烟测通过；Otto Master适配器、消息链和真机播放尚未实现 |

当前账号调用 ASR 2.0 资源 `volc.seedasr.sauc.duration` 返回 403；相同 API Key 对 ASR 1.0 和 TTS 均成功。因此 Phase 5 先把它视为“ASR 2.0资源未授权或未开通”，不能误判为密钥整体无效。以后控制台开通 2.0 后，只允许通过配置切换资源ID，不改变内部消息合同。

测试使用的密钥不得写入本文档、Git、日志或SQLite。测试密钥已经在用户截图中出现，正式部署前必须轮换。

## 2. 已完成的真实API烟测

测试日期：2026-09-18。测试只验证云端协议与编解码可行性，没有连接 EVA 真机，也没有修改 Otto Master 业务代码。

### 2.1 TTS

- HTTP状态为200，服务事件码包含 `0` 和 `20000000`，最终消息为 `OK`。
- 合成文本为“你好，我是伊娃，这是火山引擎语音合成接入测试。”。
- 请求输出为单声道、24 kHz、16-bit little-endian PCM。
- 首个音频块约524 ms到达；完整请求约1243 ms结束。
- 收到267102字节PCM，对应约5.565秒音频。
- 使用 `opuslib-next` 编码为93个60 ms Opus帧，共15251字节；首帧成功解码回2880字节PCM。
- 最后一帧不足60 ms时补零，只在输出边界补齐，不把补齐后的静音写入持久化记录。

### 2.2 ASR

- 使用双向流式WebSocket和ASR 1.0时长版资源成功识别上述TTS音频。
- 连接后约986 ms收到首个部分结果。
- 4.824秒源音频发送结束后约369 ms收到最终结果。
- 最终文本精确为“你好，我是伊娃，这是火山引擎语音合成接入测试。”。

这些数字是一次开发机烟测结果，只作为可行性证据和后续回归基线，不是生产SLA。Phase 5必须在同一测试脚本中记录首包、最终结果、总耗时、资源ID和请求ID，才能比较后续变化。

## 3. 端到端数据流

### 3.1 设备语音上行到ASR

```text
EVA麦克风
  → 16 kHz / mono / 60 ms Opus帧
  → MQTT Profile: 加密UDP音频
     或 WebSocket Profile: 二进制Opus
  → Device Gateway验证设备、序号、参数和大小
  → Device Session短期有界AudioFrameStore
  → Message Bus只发布audio.input.*元数据和frame_ref
  → Opus解码为16 kHz / mono / S16LE PCM
  → 按设备、按utterance顺序聚合后实时发送火山ASR WebSocket
  → 部分转写voice.transcription.partial
  → 最终转写voice.transcription.completed
  → Phase 6 WakeGate或后续对话模块消费文本
```

设备帧长已经固定为60 ms。ASR发送块的首选起始值为180 ms，即连续3帧聚合一次；它必须是可配置参数，并用延迟、识别准确率和内存占用实测决定，不能把官方示例中的块大小硬编码成业务合同。

### 3.2 文本到设备TTS播放

```text
tts.synthesis.requested
  → TTS Service校验文本、设备和音色
  → Cloud Gateway发起火山TTS流式HTTP
  → 直接接收24 kHz / mono / S16LE PCM
  → Opus Encoder按60 ms滚动切帧
  → tts start
  → tts sentence_start
  → MQTT Profile: 加密UDP Opus
     或 WebSocket Profile: 二进制Opus
  → tts stop
  → 设备重新进入listening，映射为tts.playback.finished
```

24 kHz、单声道、16-bit PCM的一个60 ms帧是1440个采样点、2880字节。编码器必须保留跨HTTP块的尾部数据，凑满一帧再编码；流结束时只补齐最后一帧。

TTS控制JSON沿用小智固件已经识别的顺序：

```json
{"type":"tts","state":"start"}
{"type":"tts","state":"sentence_start","text":"EVA1 在"}
{"type":"tts","state":"stop"}
```

`start`成功后，无论云API失败、设备断开还是任务取消，都必须在清理路径尝试发送 `stop`；不能让设备永久停留在speaking。当前固件没有独立的 `tts_finished` 事件，Phase 5暂以 `stop` 后设备重新进入listening作为播放完成证据，未来固件提供显式完成事件后再升级合同。

## 4. 控制面与音频数据面

Message Bus只传递控制事实、结果和音频引用，禁止在普通Message中携带PCM、Opus或Base64音频。

```text
控制面：Message Bus
  audio.input.started / frame / finished
  voice.transcription.partial / completed / failed
  tts.synthesis.requested / started / completed / failed
  audio.output.frame
  tts.playback.requested / finished

数据面：每设备短期内存
  device_id + utterance_id + sequence → Opus/PCM bytes
```

数据面必须满足：

- 按 `device_id + utterance_id` 隔离，EVA1帧不能被EVA2引用。
- 固定最大帧数、最大字节数和空闲超时，达到上限立即失败关闭，不允许无限缓存。
- `frame_ref` 是进程内短期引用，过期、跨设备或序号不连续时拒绝读取。
- 默认不落盘、不写JSONL、不进入SQLite、不进入Browser事件流。
- 调试保存原始音频只能由显式、短时、本地配置开启，并默认在会话结束时删除；MVP默认关闭。

## 5. 内部消息合同

所有消息继续使用通用信封。本节只定义语音payload，外部火山字段不能直接泄漏到业务模块。

### 5.1 输入音频帧

```json
{
  "topic": "audio.input.frame",
  "source": "device:aabbccddeeff:websocket",
  "target": "service:asr",
  "payload": {
    "device_id": "aabbccddeeff",
    "utterance_id": "utt-uuid",
    "frame_ref": "audio-ref-uuid",
    "sequence": 42,
    "codec": "opus",
    "sample_rate": 16000,
    "channels": 1,
    "frame_duration_ms": 60
  }
}
```

### 5.2 转写结果

```json
{
  "topic": "voice.transcription.completed",
  "source": "service:asr",
  "target": "device:aabbccddeeff",
  "payload": {
    "device_id": "aabbccddeeff",
    "utterance_id": "utt-uuid",
    "text": "你好 EVA1",
    "is_final": true,
    "provider": "volcengine",
    "latency_ms": 369
  }
}
```

部分结果使用 `voice.transcription.partial`；网络、协议、限流或序号错误使用 `voice.transcription.failed`。失败payload只记录稳定错误码、可否重试和请求关联ID，不记录密钥、认证头或原始音频。

### 5.3 TTS请求和输出帧

```json
{
  "topic": "tts.synthesis.requested",
  "source": "service:wake_gate",
  "target": "service:tts",
  "payload": {
    "device_id": "aabbccddeeff",
    "text": "EVA1 在",
    "voice": "default",
    "sample_rate": 24000,
    "frame_duration_ms": 60
  }
}
```

TTS Service发布 `tts.synthesis.started`，并按序发布只含 `frame_ref` 和元数据的 `audio.output.frame`；全部PCM转换完成后发布 `tts.synthesis.completed`。Device Gateway负责把帧引用读取为字节并按当前Profile发送。`tts.synthesis.completed`只表示合成与编码结束，不等于设备播放结束；只有设备完成证据才能产生 `tts.playback.finished`。

## 6. 火山协议边界

### 6.1 ASR

请求头至少包括：

```text
X-Api-Key: <environment secret>
X-Api-Resource-Id: volc.bigasr.sauc.duration
X-Api-Request-Id: <new uuid per request>
```

规则：

- 二进制帧头、消息类型、序列化、压缩、序号和错误解析以火山当前官方协议为唯一真相来源。
- 首包发送完整请求，后续发送PCM音频包，结束时发送协议定义的final包。
- 每个utterance建立独立云会话；设备断开、取消或超时必须关闭WebSocket并释放缓冲。
- 只把规范化partial/final文本发布到Message Bus，Provider响应原文不进入持久化消息。

### 6.2 TTS

请求头至少包括：

```text
X-Api-Key: <environment secret>
X-Api-Resource-Id: seed-tts-2.0
X-Api-Request-Id: <new uuid per request>
```

规则：

- 请求火山直接返回PCM，不先请求MP3，也不在运行时依赖FFmpeg转码。
- `ogg_opus`只支持48 kHz，不直接作为当前固件24 kHz播放链路的输出格式。
- HTTP响应必须同时验证状态码、服务事件码和请求ID；200但服务码失败仍按失败处理。
- 第一块音频发送给设备后，不自动重试整次TTS，避免重复播报；第一块之前的瞬时网络失败才允许按策略重试。

官方协议文档：

- [双向流式语音识别WebSocket](https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-automatic-speech-recognition-websocket)
- [单向流式语音合成HTTP](https://docs.volcengine.com/docs/DoubaoVoice/unidirectional-streaming-text-to-speech-http)

## 7. 小智Server复用边界

参考源码位于 `/Users/howtion/otto/server/xiaozhi-esp32-server/main/xiaozhi-server/`。该仓库采用MIT许可；若复制实质代码，必须保留许可和来源记录。优先复用经过验证的算法与状态语义，不直接移植它的全套服务结构。

| 参考文件 | 可以复用 | 不直接复用 |
|---|---|---|
| `core/providers/asr/doubao_stream.py` | WebSocket生命周期、结束包、取消和结果归一化思路 | 旧鉴权、旧帧格式和配置结构；当前官方协议优先 |
| `core/utils/opus_encoder_utils.py` | PCM滚动缓冲、整帧编码、尾帧补零 | 具体全局对象和项目耦合 |
| `core/handle/sendAudioHandle.py` | `start → sentence_start → audio → stop` 顺序和约60 ms发送节奏 | 直接访问连接对象和混合业务状态 |
| `core/handle/helloHandle.py` | 服务端hello协商播放采样率和帧长 | 小智Server的完整连接组装 |

Otto Master自己的职责分配保持不变：

- `gateways/cloud.py`：HTTP/WebSocket、鉴权头、火山二进制协议、超时和Provider错误翻译。
- `services/asr.py`：消费音频引用，管理utterance，输出规范化partial/final/failed。
- `services/tts.py`：消费文本请求，输出合成状态和有序音频帧引用。
- `audio/opus.py`：纯编解码、滚动缓冲、参数验证和尾帧处理。
- Device Gateway：不同Profile的外部音频封装、TTS JSON顺序和设备完成证据。
- Runtime：只组装依赖、统一Worker Pool和启停顺序，不直接调用ASR/TTS。

## 8. 配置与密钥

Phase 5施工时扩展现有 `cloud.asr`、`cloud.tts` 和 `audio` 配置，不另起第二套配置系统。建议目标形态：

```yaml
cloud:
  asr:
    provider: volcengine
    base_url: wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
    api_key_env: OTTO_ASR_API_KEY
    resource_id: volc.bigasr.sauc.duration
    chunk_ms: 180
  tts:
    provider: volcengine
    base_url: https://openspeech.bytedance.com/api/v3/tts/unidirectional
    api_key_env: OTTO_TTS_API_KEY
    resource_id: seed-tts-2.0
    sample_rate: 24000
    format: pcm
```

ASR和TTS当前可以在本机 `.env` 中使用同一个火山项目API Key值，但仍保留两个环境变量名，便于以后分项目、分权限和轮换。禁止把密钥放入 `config.yaml`、WebUI、OTA响应、ESP32固件或测试夹具。

## 9. 并发、背压和重试

- 每台设备同一时刻只允许一个活动ASR utterance和一个TTS播放会话；不同设备可并发。
- 云请求使用全局并发信号量，具体上限从配置读取；达到上限进入短有界队列，队列满则明确失败。
- ASR连接建立前可以重试；音频开始上传后默认不重放整段，避免重复结果和内存膨胀。
- TTS只有在首个音频块发送前可以重试；发送后失败必须stop并报告部分播放失败。
- 429、5xx、超时和协议错误转换为稳定内部错误；单Provider或单设备失败不得终止Runtime。
- 音频序号缺失、重复、倒序或跨设备引用全部失败关闭，不猜测补包。
- 关闭顺序先停止接收新utterance，再取消云会话、发送必要的TTS stop、释放音频引用，最后关闭Cloud Gateway和Worker Pool。

## 10. macOS与Windows约束

- 网络层只使用项目已有的纯Python接口 `httpx` 和 `websockets`。
- Opus只通过 `opuslib-next` 封装；Phase 5先完成macOS与Windows CI导入、编码和解码测试。
- Phase 9必须在实体Windows和PyInstaller产物中验证 `libopus` 动态库收集，不能用macOS成功替代。
- 运行时不依赖Homebrew、bash、Unix信号、固定临时目录、FFmpeg命令行或macOS专用音频API。
- 云烟测脚本必须通过显式 `external` 标记启用；默认测试使用fake provider，不消耗额度。

## 11. Phase 5二元验收

只有以下必需项全部PASS，才允许把Phase 5标记为完成：

1. 单元测试验证16 kHz与24 kHz、单声道、60 ms帧、跨块滚动缓冲、尾帧补齐和非法参数拒绝。
2. fake ASR验证partial、final、错误、取消、超时、乱序帧和每设备隔离。
3. fake TTS验证 `start → sentence_start → audio → stop`，并验证每个失败/取消分支都能stop。
4. 真实火山外部测试返回目标文本；无Key时明确跳过并标记NOT RUN，不能算PASS。
5. EVA1说话只生成EVA1的转写；EVA2说话只生成EVA2的转写，不串utterance或device_id。
6. 指定文本分别能在EVA1和EVA2播放，另一台不播放；设备最终回到listening。
7. MQTT加密UDP和WebSocket二进制两种Profile分别完成至少一个音频闭环，不能用一个Profile替代另一个。
8. API Key无效、403、429、5xx、网络超时和设备断开均不使Runtime崩溃，不遗留云会话或音频引用。
9. 默认日志、SQLite和Browser事件流中不存在原始PCM、Opus、Base64音频、API Key或认证头。
10. macOS和Windows通过锁定安装、Opus编解码、fake Provider集成和PyInstaller导入冒烟。

真实云烟测成功不能替代EVA1/EVA2真机播放，macOS成功不能替代Windows打包验证，小智Server历史测试也不能替代Otto Master本轮测试。
