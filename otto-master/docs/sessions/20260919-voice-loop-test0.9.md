# 循环语音与笑声播放加固施工契约

## 基本信息

- 日期：2026-09-19（设备与Server日志使用UTC，显示为2026-09-18 17时段）
- Otto Master版本：`0.4.3`
- 工作支线：`test0.9`
- 固件工作树：`/Users/howtion/otto`，分支`codex/otto-portable`
- 目标设备：EVA1 `e072a1f71184` / `e0:72:a1:f7:11:84`
- EVA2：由用户关机，本轮不计双设备验收
- 前序合同：`docs/sessions/20260918-voice-mvp-test0.9.md`

本文件只记录本轮增量。前序文件保留首个真实ASR→DeepSeek→TTS闭环证据；其中“回答后立即goodbye”的单轮策略已被本文件的循环策略取代。

## 目标

1. 修复本地奶龙笑声一卡一卡的问题，不让`tts start`清空正在播放的本地OGG。
2. 回答完成后继续下一轮：每次提问前都先完整大笑，再开始收音。
3. 新一轮8秒内没有真实讲话则退出；讲话后不受该静默计时误杀。
4. Otto按钮在idle进入循环对话，在connecting/listening/speaking退出；退出后再按可建立新session。
5. 失败不能永久卡死设备；新的不同session可以重新进入门禁。

## 冻结状态机

```text
button / wake
  → laughing
  → local laugh sound.busy false → true → false
  → tts start → arm exact next utterance → tts stop
  → listening / recognizing
      ├─ 8秒内无VAD true或ASR partial → server goodbye → waiting
      ├─ button → device goodbye → waiting
      └─ ASR final → answering → streaming LLM/TTS → laughing（下一轮）
```

规则：

- 每一轮都有新的`round_id`和utterance；旧轮VAD、partial、final不得取消当前计时或驱动回答。
- 本地笑声开始前关闭ASR；只有观测到`sound.busy=true → false`才可arm下一轮。
- `audio.input.activity(speaking=true)`和非空ASR partial是“用户开始讲话”的证据；纯音频帧到达不能取消8秒计时。
- 正常回答后保留同一设备session并重新执行笑声门禁；只有静默、按钮、异常或显式关闭才释放session。
- 设备`goodbye`是正常按钮退出；未知断开仍失败关闭。

## 根因与修复

### 笑声卡顿

旧顺序会在本地笑声之前发送`tts start`。固件进入speaking时调用`ResetDecoder()`，本地OGG与云端TTS又共用解码和播放队列，因此笑声包可能被清空或产生下溢。旧资产还是20 ms Opus包，与当前60 ms链路不一致。

修复：

- 先完成本地`laugh`，再发`tts start`建立下一轮监听。
- `nailong_laugh.ogg`转为24 kHz OpusHead、单声道、34个60 ms包，总时长约2.0065秒。
- `PlaySound`按Opus包实际采样数推导帧长，并等待解码队列、PCM播放队列、活动解码和I2S写入全部结束才返回。
- `sound.busy=false`现在表示本地音频真实排空，不再只表示压缩包已入队。

### 连续第二轮笑声丢失

`laugh`没有舵机动作，旧实现把音频入队后立即把动作改回idle。Dispatcher轮询可能完全错过瞬时moving，于是命令15秒后超时并安全stop，排队的下一轮笑声被取消。

固件2.0.11让`laugh`从入队前开始保持moving，直到`sound.busy=false`才回idle；忙时最多有界等待5秒再入队。Server同时允许新的不同session从`failed`重新开始。

## 协议增量

设备VAD：

```json
{"session_id":"...","type":"listen","state":"vad","speaking":true}
```

内部映射：

- `audio.input.activity`
- `voice.session.closed(reason=device_goodbye|server_goodbye|...)`

所有映射必须校验当前`device_id + transport + session_id + utterance_id`。连续Opus仍只走WebSocket二进制或MQTT AES-128-CTR UDP，不进入普通Message或SQLite payload。

## 验收结果

| 门禁 | 结果 | 证据 |
|---|---|---|
| 本地静态检查 | PASS | `uv lock --check`、Ruff、mypy strict、Node语法、两仓`git diff --check` |
| Python测试 | PASS | `127 passed`；WakeGate定向12项通过 |
| 固件构建 | PASS | ESP-IDF 5.5.5；2.0.11；3,788,720字节 |
| 固件哈希 | PASS | `4c4298363b621599ea11c8daba2dc3efbc644538666a9f10f7115ece49e200bf` |
| OTA与回连 | PASS | EVA1从2.0.10升级后以2.0.11、MQTT online回连 |
| 回答后循环 | PASS | 真机识别“没事。”并完成回答；随后再次进入laughing和新一轮listening |
| 按钮退出/重入 | PASS | 设备产生`device_goodbye`并释放session；约2秒后新session重新进入laughing |
| 连续笑声生命周期 | PASS | 两轮均见`moving/laugh/busy=true → idle/false`；第二轮约2.1秒完成且Dispatcher归零 |
| 8秒静默退出 | PASS | `exit_reason=idle_timeout`、UDP sessions=0、EVA1 idle |
| 失败后新session恢复 | PASS | 自动测试覆盖；新session清除旧failure并重新进入laughing |
| 主观听感 | 待用户确认 | 队列竞态、帧长和完成语义已修复；是否仍可听出卡顿不能仅靠遥测替代人耳结论 |

## LLM工具边界

> 后续状态：本节记录的是循环加固结束时的暂停点，已由`docs/sessions/20260919-llm-tools-test0.9.md`完成并取代；保留以下文字作为历史验收记录。

当前DeepSeek请求只使用流式文本SSE：没有发送`tools`或`tool_choice`，也没有消费`tool_calls`。因此用户说“大笑、前进、后退”时，模型只会生成文字并进入TTS；配置提示词中的`self.otto.laugh`不会自动变成设备调用。

下一步必须显式实现：

```text
设备动作目录
  → 受限tool schema
  → DeepSeek tool_calls增量组装
  → 动作名/参数/目标/confirmation校验
  → 现有Dispatcher
  → 成功动作本轮不追加TTS复述
```

未经该桥接，不得宣称奶龙能通过自然语言调用动作工具。

## 当前暂停点

- 正式Runtime：HTTP `8081`、MQTT `1883`、UDP `8884`，健康。
- 诊断/OTA服务：HTTP `8080`、TCP `8765`，保留运行。
- EVA1：固件2.0.11、MQTT online、idle、无活动音频session。
- EVA2：关机，未触碰。
- Git：两仓均有本轮及前序未提交改动；未commit、未push、未运行正式CI。根目录用户`/Users/howtion/liteserver/README.md`未编辑。
- 密钥：仍只位于Git忽略的本地环境和凭据目录，未写入文档或差异。

## 恢复施工顺序

1. 只读确认8081 Runtime健康、EVA1 `2.0.11 / mqtt / idle`、UDP sessions=0；不要重复OTA。
2. 实现DeepSeek真实工具桥，先用fake tool-call流和Dispatcher测试覆盖动作名、参数、取消、拒绝、重复调用与“成功后不朗读”。
3. 只在自动门通过后用EVA1分别验收`laugh`、前进和后退；动作后显式确认idle。
4. EVA2开机后补双设备语音/动作隔离；再补WebSocket真机Profile、正式CI与实体Windows。
