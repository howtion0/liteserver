# Phase 5 火山引擎语音接入设计

本文档冻结 Otto Master Phase 5 的 ASR、TTS、Opus 数据流、云协议、复用边界和验收标准，并记录 `test0.9` 已完成的单机纵向实现及`test1.0`音量加固。EVA1的完整MQTT+加密UDP闭环和EVA2单会话烟测已经通过；多设备并发语音、双Profile和Windows打包矩阵仍未完成。

## 1. 当前结论

| 项目 | 决定或证据 |
|---|---|
| ASR Provider | 火山引擎豆包语音，当前账号先使用流式语音识别 1.0 时长版资源 `volc.bigasr.sauc.duration` |
| TTS Provider | 火山引擎豆包语音 2.0；当前湾区大叔音使用资源 `volc.service_type.10029`、speaker `zh_female_wanqudashu_moon_bigtts` |
| ASR传输 | 双向流式 WebSocket：`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async` |
| TTS传输 | 单向流式 HTTP：`https://openspeech.bytedance.com/api/v3/tts/unidirectional` |
| 鉴权 | 新控制台 API Key，通过 `X-Api-Key` 发送；密钥只从环境变量读取 |
| 运行时依赖 | 复用现有 `websockets`、`httpx`、`opuslib-next`，不增加FFmpeg、pydub或numpy运行时依赖 |
| 实现状态 | Cloud/ASR/LLM/TTS/WakeGate、MQTT+AES-CTR UDP Opus、可配置PCM增益和EVA1真机闭环已实现；完整Phase 5矩阵仍进行中 |

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

### 2.3 `test0.9` EVA1真实纵向闭环

- 固件：首个闭环使用EVA1 `2.0.9`，随后循环加固升级至`2.0.11`；MQTT控制/信令，AES-128-CTR UDP Opus数据面；动作目录包含无舵机`laugh`，本地音频资产时长约2.0065秒。
- 门禁：16:30:09进入`laughing`，只有观测到`sound.busy=true → false`后才在16:30:13 arm下一条utterance；笑声前置帧全部丢弃。
- ASR：设备auto listen没有主动结束包，因此以非空partial连续1.2秒不变作为服务端端点；排空已接收队列后发送火山结束包，最终文本为“卧槽，疯掉了。”。
- LLM：DeepSeek完全流式；输入上限512字符、输出上限96字符、`max_tokens=96`，增量文本经4句有界队列切分。奶龙角色输出“奶龙在呢！”和“怎么啦，遇到什么疯掉的事啦？”。
- TTS：湾区大叔音真实合成成功；句子生产、PCM合成、Opus编码、设备播放分为独立有界阶段，最终发送85个60 ms Opus帧。
- 当时的单轮清理：`tts stop`后固件会在同一session自动`listen/start`；首个闭环用server `goodbye`关闭，最终UDP sessions=0、WakeGate=waiting、EVA1=idle。
- 自动门禁：首个闭环为`120 passed`；循环加固后为`127 passed`；DeepSeek工具桥后为`144 passed`，Ruff和mypy strict通过。EVA2由用户关机，双机在线隔离、正式CI和实体Windows仍为NOT RUN。

当前DeepSeek请求已携带从目标设备动作目录收窄生成的真实tool schema，并消费流式`tool_calls`；EVA1的`laugh`和`walk`已通过Dispatcher真实完成。模型文本仍不能当作动作证据，只有持久命令`completed`才算成功；双设备语音工具隔离仍未验收。

### 2.4 `test0.9` 循环对话与笑声加固

- 状态机变为`waiting → laughing → listening → recognizing → answering → laughing...`；回答后沿用同一设备session，但每轮使用新的`round_id`和utterance。
- 每次门禁都先关闭ASR，触发本地`laugh`，观测`sound.busy=true → false`后才发送一次`tts start → stop`建立干净的新监听轮。
- 监听开放后启动8秒计时。只有当前轮非空ASR partial会取消计时；设备VAD仍参与ASR端点，但其房间噪声/扬声器尾音抖动不能延长WakeGate窗口。没有云端文字证据时Server准时关闭session。按钮在对话态发送设备`goodbye`退出，再按一次建立新session。
- 原笑声资产为48 kHz名义输入和20 ms包，对60 ms播放链更敏感；2.0.10起改为24 kHz OpusHead、单声道、34个60 ms包。`PlaySound`等待解码队列、PCM队列和I2S写入全部排空后才清除`sound.busy`。
- 2.0.11让无舵机`laugh`保持动作`moving`直到本地音频真正结束，修复Dispatcher因错过瞬时moving而占住命令15秒、进而取消下一轮笑声的问题；新的不同session也可从失败状态自恢复。
- EVA1真机完成一次真实回答后的第二轮笑声和监听；连续两个独立门禁均观测完整忙闲边沿，另一次监听在8秒静默后`idle_timeout`退出且UDP sessions=0。

### 2.5 `test1.0` TTS响度加固

- TTS Service在Opus编码前对火山返回的24 kHz单声道S16LE PCM应用`audio.tts_pcm_gain`。每个样本四舍五入并饱和到`[-32768, 32767]`，不得发生整数回绕；任意HTTP块边界拆开的单个字节会保留到下一块，最终残留半个采样视为Provider协议错误。
- 首轮真实短句在1.5倍时得到113,398字节PCM、约2.362秒音频；峰值从30,365升到32,768，RMS从4,338.5升到6,412.2，182个样本发生饱和，约占0.32%。这些数字仅描述该短句，不外推为所有文本的响度或削波比例。
- 用户仍反馈1.5倍和设备音量90偏小，因此生产配置调整为2.0，EVA1固件2.0.13把持久输出音量迁移到100；当前2.0.16继续保留该档位。2.0.15 OTA后已用心跳验证`output_volume=100`，2.0.16换网修复后hello确认新版本与当前DHCP IP，Server健康状态继续报告`pcm_gain=2.0`。
- 修复后五轮真实问答均完成TTS，用户随后确认“对话感觉没问题了”；因此当前2.0倍增益、设备音量100下的清晰度、流畅度和明显削波主观门禁记为PASS。若后续其他文本暴露破音，应改用压缩/限幅策略，不继续提高硬增益。

### 2.6 `test1.0` ASR端点与动作音效时序加固

- 03:43至03:47左右的“丝滑”实测中，每个有效utterance都在开始后约1.1至3.1秒收到火山partial；最后一条partial稳定1.2秒后收句，云端final再用约0.1至0.33秒返回。DeepSeek开始生成到首段TTS播放约0.6至1.1秒，因此用户感知为连续响应。
- 05:21故障轮并非麦克风距离问题：EVA1上传491个60 ms Opus帧（约29.5秒），设备VAD持续上报且UDP只出现少量序号缺口，但火山没有返回任何partial。旧实现只靠partial稳定收句，最终等到30秒云超时。
- ASR现为三个独立的一次性端点：非空partial稳定1.2秒、已说话后的VAD连续静音1.2秒、从utterance开始的12秒硬上限。新的VAD `true`只取消`vad_silence`，不再像旧共享计时器那样反复续掉partial稳定；任一端点获胜后先禁止新帧、取消其余计时器，再把队列中已接收尾帧全部送完，避免截断末字。12秒上限与1.2秒尾部窗口还必须严格小于火山30秒请求超时。
- 首次真机复测在最后一次`false`后1.202秒触发`vad_silence`，148 ms后火山返回空final，证明原30秒悬挂已可被兜底，也暴露旧WakeGate会停在`recognizing`。最终策略是首次空final执行一次本地大笑并重开监听，连续第二次空final以`empty_transcription_limit`正常退出；不调用LLM、不增加turn、不因重复final重复动作。EVA1已真实走完“两次空final→一次笑声→waiting”，无第三次笑声、无failed、ASR和UDP活动数均归零。
- 修复后EVA1另完成五轮有效问答，五轮都由`partial_stability`收句：首个partial约1.102至4.538秒，端点约3.309至8.304秒，final约0.078至0.196秒；火山ASR、DeepSeek和TTS均无失败。另一次纯等待期间即使收到9次VAD-only事件，也在开放监听后精确约8秒以`idle_timeout`退出，证明环境噪声不能续命。
- Dispatcher把设备`sound_busy=true`同时纳入新动作准入和完成判据。动作舵机已idle但本地OGG仍播放时，持久命令继续保持moving；只有`action_state=idle`且`sound_busy!=true`才completed，旧固件未上报该字段时保持兼容。WakeGate开场笑声遇到`device_state_unsafe`时也反复查询这两个条件，不把一次idle快照误当作共享音频解码器已经空闲。

### 2.7 `test1.0` EVA2单会话烟测

- EVA2升级到2.0.16并以独立device_id `aca704ed89a8`上线后，按钮会话`faa4eea4-b809-4e34-b835-0b221fcbbd5c`经MQTT信令和加密UDP音频进入listening。
- 串口和Server对话投影记录了本地笑声、ASR最终文本“你好，你是谁？”、奶龙短回复、火山TTS和再次监听，最终无活动ASR/UDP遗留。该证据确认EVA2单设备路径可用，但没有用户听感确认，也没有与EVA1/EVA3同时开语音会话，因此多设备并发隔离仍为NOT RUN。

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
  → 按audio.tts_pcm_gain逐样本放大并饱和钳位
  → Opus Encoder按60 ms滚动切帧
  → tts start
  → tts sentence_start
  → MQTT Profile: 加密UDP Opus
     或 WebSocket Profile: 二进制Opus
  → tts stop
  → paced发送完成，产生tts.playback.finished
  → 循环模式重新进入本地laugh门禁并创建下一条utterance
  → 8秒静默 / 按钮 / 失败时goodbye
  → 设备回到idle
```

24 kHz、单声道、16-bit PCM的一个60 ms帧是1440个采样点、2880字节。增益器必须先保留跨HTTP块拆开的半个S16LE采样；编码器再保留跨块的PCM尾部，凑满一帧后编码，流结束时只补齐最后一帧。

TTS控制JSON沿用小智固件已经识别的顺序：

```json
{"type":"tts","state":"start"}
{"type":"tts","state":"sentence_start","text":"EVA1 在"}
{"type":"tts","state":"stop"}
```

`start`成功后，无论云API失败、设备断开还是任务取消，都必须在清理路径尝试发送 `stop`；不能让设备永久停留在speaking。当前固件没有独立的 `tts_finished` 事件，因此`tts.playback.finished`只证明服务端按60 ms节奏发完帧并成功发出`stop`，不冒充扬声器物理播放ACK。正常回答随后进入下一次本地笑声门禁；8秒静默、按钮或失败再以server/device `goodbye`和设备idle作为资源清理证据。未来固件提供显式播放完成事件后再升级合同。

固件还支持识别文字上屏：`{"type":"stt","text":"..."}`显示为用户消息；`tts/sentence_start`中的`text`显示为助手消息。`test0.9`加固轮已实现两者：ASR final先按同一`device_id + session_id + utterance_id`发送一次`stt`，成功后才启动DeepSeek；重复/过期final不显示，上屏失败不继续回答并关闭session。EVA1真实回合已在诊断心跳中确认`last_user_text`和`last_assistant_text`均更新。

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

TTS Service发布 `tts.synthesis.started`，经内部有界队列对PCM应用配置增益，再转换为有序Opus帧并交给当前Profile的播放对象，单句结束发布 `tts.synthesis.completed`。`tts.synthesis.completed`只表示该句合成与编码结束；当前`tts.playback.finished`表示服务端已按60 ms节奏发完全部帧并成功发出`stop`，仍不等于扬声器物理播放ACK。

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
X-Api-Resource-Id: volc.service_type.10029
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
    resource_id: volc.service_type.10029
    speaker: zh_female_wanqudashu_moon_bigtts
    sample_rate: 24000
    format: pcm
audio:
  tts_pcm_gain: 2.0
```

`tts_pcm_gain`允许范围为`0.25..4.0`且必须是有限数值。它只影响云TTS PCM，不二次放大固件本地笑声或动作音效；运行状态公开当前数值，方便确认实际生效配置。

ASR和TTS当前可以在本机 `.env` 中使用同一个火山项目API Key值，但仍保留两个环境变量名，便于以后分项目、分权限和轮换。禁止把密钥放入 `config.yaml`、WebUI、OTA响应、ESP32固件或测试夹具。

## 9. 并发、背压和重试

- 每台设备同一时刻只允许一个活动ASR utterance和一个TTS播放会话；不同设备可并发。
- 当前45秒对话窗口下有界队列为ASR 750帧、LLM 4项、TTS句子4/PCM 16/Opus 48；各阶段是独立生产者/消费者，队列满或消费者失败必须取消上游并清理。
- ASR端点同时接受云partial稳定、设备VAD静音与12秒硬上限，三个独立计时器竞争同一个一次性输入关闭权；任何迟到帧、重复端点或过期utterance只能按unmatched/stale/post-endpoint分类丢弃，不能建立第二个云请求。取消路径必须先原子摘除活动utterance，保证下一轮可以立即arm。
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
