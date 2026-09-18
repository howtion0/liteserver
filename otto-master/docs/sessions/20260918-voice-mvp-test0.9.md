# 流式语音问答 MVP 施工契约

> 后续说明：本文件记录2026-09-18首个单轮闭环。回答后立即`goodbye`的策略已在2026-09-19改为有界循环；当前恢复点和新合同见`docs/sessions/20260919-voice-loop-test0.9.md`。

## 基本信息

- 日期：2026-09-18
- 当前版本：`0.4.3`（完成验收前不提前升版）
- 当前 Phase：Phase 5 纵向 MVP，已串通最小 WakeGate、DeepSeek 流式问答与 MQTT 加密 UDP 音频
- Git 迭代支线：`test0.9`（支线编号是连续检查点，不代表 Phase 9）
- EVA 固件基线：EVA1 已确认升级并运行 `2.0.9`；EVA2 同为 `2.0.9`，但本轮按用户要求关机且未参与语音测试
- 固件工作树：`/Users/howtion/otto`，分支 `codex/otto-portable`

## Gate 0：GitHub 与工作树基线

- 基线分支：`test0.8`
- 本地与远程基线 commit：`53b128c2c280bdc87c47309171cc68e795091886`
- 正式基线 CI：run `35314857361`；macOS job `105504221646`、Windows job `105504221763` 均为 `success`
- 本地与远程一致：是
- 当前工作区已有改动及归属：仓库根目录 `/Users/howtion/liteserver/README.md` 是用户已有修改，本轮不得编辑、覆盖或暂存
- Otto Master 的 `.env`、`.local-secrets/`、数据库、日志、音频、固件和构建产物不得提交
- 固件仓库已有 Phase 4 施工资产，必须保留并精确审查；禁止清理工作树或整树暂存

## Gate 1：施工依据已阅读

- [x] `AGENTS.md`
- [x] `ONBOARD.md`
- [x] `CODEX_CONSTRUCTION_WORKFLOW.md`
- [x] `CODEX_MASTER_REQUIREMENTS.md`
- [x] `CODEX_ARCHITECTURE.md`
- [x] `docs/DEV_PROGRESS.md`
- [x] `docs/CONSTRUCTION_PLAN.md` Phase 5、6、7
- [x] `docs/VOLCENGINE_SPEECH_INTEGRATION.md`
- [x] `docs/MESSAGE_CONTRACTS.md`
- [x] `docs/DEVICE_TRANSPORT_CONTRACT.md`
- [x] `docs/MQTT_CONTROL_CONTRACT.md`
- [x] `CODEX_RULES_TESTING.md`
- [x] `CODEX_RULES_GIT.md`
- [x] 小智 Server 中 ASR、TTS、流式 LLM、句子切分、Opus 缓冲与发送节奏的相关实现
- [x] 火山 ASR/TTS 当前官方协议与官方 Python 示例

## 本轮目标

以最快纵向路线在 EVA1 跑通一次可重复的真实流式问答：用户触发提问后，EVA1 必须先完整播放内置大笑；只有观测到真实播放开始并结束后才开放麦克风音频给 ASR。用户随后说话，火山 ASR 产生最终文本，DeepSeek 流式返回答案，服务端按句切分并依次调用火山 TTS，最终以 24 kHz、60 ms Opus 帧在 EVA1 顺序播放。

本轮不是把 Phase 5、6、7 的完整双设备矩阵提前宣告完成，而是先交付单台 EVA1 的最小可用纵向链；EVA2 已由用户关机，不计入本轮在线隔离验收。

## 不可绕过的交互状态机

```text
等待提问意图
  → laughing_gate（丢弃/拒绝全部麦克风帧）
  → 下发EVA1内置大笑
  → 必须观测 sound.busy=true
  → 必须随后观测 sound.busy=false
  → listening（此刻之后的新帧才能进入ASR）
  → ASR final
  → DeepSeek流式文本
  → 按完整句切分并顺序TTS
  → tts start → sentence_start → 60ms Opus帧 → stop
  → server goodbye → 设备确认goodbye → 安全idle
```

- 固定 `sleep`、动作 ACK、动作队列 idle 或预计音频时长都不能单独充当笑声完成证据。
- `sound.busy=true → false` 未完整出现、超时、设备断开或 stop 时，门禁失败关闭；本轮不得启动 ASR 或 LLM。
- laughing、speaking 和清理阶段收到的麦克风帧必须丢弃，不得延迟后再送入 ASR，避免识别机器人自己的笑声或回答。
- 每次新的提问轮次都重新执行完整大笑门禁，上一轮成功不能复用。
- 大笑后只接受门禁开放时刻之后创建的新 utterance；`device_id + session_id + utterance_id` 必须一致。

## 涉及模块

- `audio/opus.py`：无 numpy 的 Opus 解码、PCM 滚动缓冲、60 ms 编码与尾帧补齐
- `gateways/cloud.py`：火山 ASR 二进制 WebSocket、火山 TTS 流式 HTTP、DeepSeek 兼容接口与稳定错误映射
- `gateways/device_udp.py`、`gateways/device_audio.py`：MQTT信令、AES-CTR UDP Opus、传输路由与回合结束清理
- `gateways/device_ws.py`：认证设备音频输入、严格有序的 TTS JSON/二进制输出和兼容回合清理
- `services/asr.py`、`services/tts.py`、`services/llm.py`：单一业务职责和 fake provider
- 新增的对话编排/WakeGate 模块：按设备隔离的状态机、大笑完成门禁、取消和超时
- `runtime.py`、`config.py` 与配置文件：只负责依赖组装、密钥解析和生命周期
- EVA 固件：可认证、可逆的 MQTT/WebSocket Profile 选择；WebSocket hello 的 Otto 能力；大笑音效运行态与 TTS/listen 状态配合

## 允许修改的路径

### Otto Master

- `pyproject.toml`、`uv.lock`、`config.yaml`、`.env.example`
- `src/otto_master/audio/`、`gateways/`、`services/`、`devices/`、`runtime.py`、`config.py`、`messages.py`
- 与本轮行为直接相关的 Web API/UI 精确文件
- `tests/` 内对应单元、集成、外部 API 与真机门禁
- 施工、架构、协议、进度、模块状态和日志文档

### EVA 固件

- `main/application.*`
- `main/protocols/protocol.*`、`main/protocols/websocket_protocol.*`、`main/protocols/mqtt_protocol.*`
- `main/otto_master_link.*`、Otto 控制器状态及对应构建/配置文件
- 与可逆 Profile 切换和 WebSocket hello 能力直接相关的最小文件

所有修改按精确路径审查和暂存；禁止 `git add .`、`git add -A`、force push、清理用户工作树或删除既有配置。

## 输入与外部依赖

- ESP32：是，最终 MVP 只要求 EVA1；EVA2 本轮由用户关机
- 真实云 API：是，火山 ASR、火山 TTS、DeepSeek
- 局域网：是，Mac、EVA1、EVA2 位于同一 Wi-Fi
- Windows：是，fake provider、Opus、导入和打包冒烟必须通过 GitHub Actions
- MQTT Broker：内嵌真 Broker，承载 EVA1 控制、语音信令和 UDP 音频协商
- 预期音频传输：按最新施工路线，EVA1使用 `mqtt` JSON信令 + AES-128-CTR UDP Opus；WebSocket保留兼容Profile
- API Key：只从 `.env` 指定的环境变量读取，不记录值；当前 DeepSeek `/models` 连通性已验证
- 安全清理：任一失败、取消、超时或正常结束都尝试发送 TTS stop、停止动作并确认 EVA1 安全；EVA2 不得移动

## 验收矩阵

| 编号 | 二元验收标准 | 验证命令或人工步骤 | 必需 | 最终状态 | 证据 |
|---|---|---|---|---|---|
| V1 | Opus 16/24 kHz、单声道、60 ms、跨块缓冲、尾帧和非法参数测试通过 | `uv run pytest` 对应单测 | 是 | PASS | 最终全量 `120 passed`，包含Opus编解码与滚动缓冲覆盖 |
| V2 | 火山 ASR/TTS 与 DeepSeek adapter 的 fake 流式、超时、取消、限流、协议错误和清理测试通过 | 定向 pytest | 是 | PASS | Cloud、ASR、TTS、LLM及生产者/消费者队列测试均通过 |
| V3 | 每轮先大笑，且必须观测 `sound.busy true→false` 后才接收新语音 | fake 时钟状态机测试 + EVA1 实测 | 是 | PASS | 16:30:09进入laughing，16:30:13门禁完成后才arm ASR；固件本地音频约2.0065秒 |
| V4 | 大笑期间音频不进入 ASR；门禁超时或缺失完成证据时 ASR/LLM 调用数为零 | 集成测试 | 是 | PASS | WakeGate/ASR fail-closed测试通过 |
| V5 | 真实火山 ASR 能把测试音频转成目标文本，真实火山 TTS 返回可解码 PCM/Opus | 显式 external test | 是 | PASS | TTS 210318 PCM字节/74个60ms Opus帧；ASR返回最终文本，协议闭环成功 |
| V6 | 真实 DeepSeek 逐块返回文本，句子切分不丢字、不乱序并保留尾句 | 显式 external test | 是 | PASS | 首次流式返回“语音链路正常。”；后续两次出现可恢复连接失败，已稳定映射错误 |
| V7 | EVA1 完成“大笑→用户提问→ASR→DeepSeek→句级TTS播放”闭环 | 真机端到端人工问答 | 是 | PASS | ASR final“卧槽，疯掉了。”；奶龙回答两句并成功播放85个Opus帧 |
| V8 | TTS 严格为一个 start、每句 sentence_start/音频、一个 stop；失败也 stop | 协议集成测试 + 真机事件 | 是 | PASS | 一次playback、两次sentence_start、85帧、一次stop；随后goodbye清理 |
| V9 | EVA1 会话不控制或播放到 EVA2，EVA2 全程 MQTT online/idle | 双机状态与事件核对 | 是 | NOT RUN | EVA2由用户关机；所有本轮命令和音频均精确指向EVA1，但不能冒充在线双机验收 |
| V10 | 默认日志、SQLite、Browser 与 Git 不含音频字节、Base64、Key 或认证头 | 扫描 + 数据库/API检查 | 是 | PASS | 原始Opus仅存有界内存；消息库只含frame_ref/长度元数据；密钥扫描与Git忽略检查通过 |
| V11 | Ruff、mypy、全量 pytest、JS、diff、macOS/Windows CI 全通过 | 本地门禁 + GitHub Actions | 是 | NOT RUN | 本地 `120 passed`、Ruff、mypy、JS与diff检查通过；本轮尚未commit/push，正式CI未运行 |
| V12 | 正常和所有错误分支无遗留会话，EVA1 stop/idle 或明确 listening，EVA2 idle | 结束清理核对 | 是 | PASS | 真机回答后server/device goodbye闭环，UDP sessions=0，WakeGate=waiting，EVA1=idle；错误清理有自动测试 |

## 非目标

- 本轮不宣称 Phase 5 双设备、MQTT 加密 UDP 音频和全部异常矩阵完成。
- 本轮不完成正式 Siri 式唤醒词、长期多轮记忆、LLM 动作意图或双 EVA 同时语音。
- 本轮不让 LLM 直接控制机器人动作；动作只用于服务端大笑门禁与安全 stop。
- 不合并 `main`、不打 tag、不正式发布、不轮换或上传用户密钥。
- 不修改或提交仓库根目录用户 `README.md`。

## 风险与失败关闭

- 固件当前存在本地 MQTT 配置时优先 MQTT；EVA1 的 Profile 切换必须认证、可逆且不得擦除 Wi-Fi、身份或 MQTT 回滚凭据。EVA2 不切换。
- 当前固件没有独立音频播放完成 ACK。大笑使用运行态 `sound.busy` 的开始/结束边沿；TTS MVP 以有节奏地发完音频并成功发送 `tts stop` 作为协议完成证据，不把云合成完成冒充实体播放完成。
- 自动/实时 listen 模式在 `tts stop` 后可恢复 listening；若设备处于 manual 模式且无法可靠重开收音，本轮必须失败关闭或补最小显式 listen 控制，不得假装已开放麦克风。
- DeepSeek 流比 TTS 快时使用有界句队列和背压；TTS 失败会取消 LLM 生产任务，不能无限积压。
- 云端 401/403/429/5xx、协议错误、设备断开和用户取消都归一化并清理；认证头、响应原文和音频不得进入持久日志。

## 回滚点

- Otto Master：`origin/test0.8` / `53b128c2c280bdc87c47309171cc68e795091886`
- EVA 固件：分支 `codex/otto-portable` 当前 HEAD `d531adad20f97b0e9dca5e900255cb79c794d1a1` 加本轮开始时已有未提交施工资产
- Profile 回滚：EVA1 恢复已保存的独立 MQTT 配置，不擦除 Wi-Fi/NVS 身份；EVA2 始终保持 MQTT

## 计划验证

```text
uv lock --check
uv run ruff check src tests
uv run mypy src
uv run pytest -q
node --check src/otto_master/web/app.js
git diff --check
密钥值与认证头泄漏扫描
显式真实火山ASR/TTS与DeepSeek外部测试
EVA1 WebSocket hello/Opus/大笑门禁/流式问答真机测试
EVA2 MQTT online/idle隔离检查
GitHub Actions macOS + Windows + PyInstaller Broker/Opus smoke
```

## 测试—返工记录

| 轮次 | 测试 | 初始状态 | 失败摘要或阻塞证据 | 修复 | 重测结果 |
|---|---|---|---|---|---|
| 1 | 基线 CI 与远程 SHA | PASS | 无 | 无 | run `35314857361` 双平台 success，远端 SHA 与本地一致 |
| 2 | TTS持久化断言 | FAIL | 测试误把元数据字段`pcm_bytes`当成原始PCM | 只禁止真实`data/audio`载荷 | 定向测试通过 |
| 3 | 真实云纵向烟测 | PASS/RETRY | 首次三家Provider通过；随后DeepSeek两次瞬时连接失败 | 稳定映射为`deepseek:connection_failed`，不记录响应或密钥 | 首次闭环证据保留，未把重试失败伪装成成功 |
| 4 | EVA1传输切换 | PASS | 固件原先总是优先锁定MQTT | 增加认证、可逆的首选传输和WebSocket hello能力 | EVA1 2.0.7回连并确认`preferred_transport=websocket`；EVA2未改 |
| 5 | Mac语音唤醒尝试 | FAIL | Mac播放“你好奶龙”未触发硬件唤醒器 | 发现并修复listen/start前唤醒词预录帧兼容；增加带设备令牌的验收唤醒命令 | 修复后定向测试通过；真机因用户叫停未运行 |
| 6 | 最快路线改为MQTT+UDP | PASS | WebSocket不是固件默认路径，且用户要求先走MQTT | 实现MQTT hello/listen/TTS信令与AES-128-CTR UDP Opus数据面，统一DeviceAudioRouter | EVA1真实上行185帧、下行85帧，sessions opened/closed均为1 |
| 7 | 没有设备listen/stop | FAIL | 小智auto模式会持续送音，火山已有partial但不会自然final | partial文本连续1.2秒稳定后关闭生产端、排空已入队帧并发送ASR结束包 | 真机产生`voice.endpoint.detected`并得到Provider final |
| 8 | 奶龙角色、笑声和音色 | FAIL/PASS | 旧笑声14.79秒过长；`seed-tts-2.0`与湾区大叔speaker组合被Provider拒绝 | 固件加入2.0065秒无舵机`laugh`；角色改为奶龙；音色配对改为`volc.service_type.10029` + `zh_female_wanqudashu_moon_bigtts` | 固件2.0.9、动作目录15项；真实云TTS和真机回答通过 |
| 9 | 回答结束自动再笑 | FAIL | `tts stop`使固件同一UDP session自动恢复listen，WakeGate误当新回合 | 回答后发送server goodbye，等待设备goodbye确认；ANSWERING期间续听事件不再排队为下一轮 | 回归测试通过；真机只笑一次，UDP session归零，EVA1回idle |

## 实际结果

- 类型检查：Ruff PASS；mypy strict PASS（36个source files）。
- 单元/集成测试：最终全量 `120 passed in 3.99s`；包含MQTT UDP、ASR endpoint、LLM/TTS有界生产者/消费者队列以及回答后自动续听回归。
- 完全流式链：ASR以3帧/180 ms块消费；DeepSeek增量文本进入4句有界队列；TTS按句生产PCM（队列16）、编码Opus（队列48），播放消费者按60 ms节奏发送。
- 输入输出边界：DeepSeek输入最多512字符，输出最多96字符，Provider `max_tokens=96`；超限截断并计数，回答按完整句或安全长度边界切分。
- 人设与音色：唯一名称为“奶龙”，用户呼唤时优先回答“奶龙在呢！”；语言保持活泼、简短，TTS固定湾区大叔音。当前DeepSeek请求尚未接入真实动作/tool schema，人设中的`self.otto.laugh`和动作工具规则只是下一步合同，不得误报已可由模型调用。
- 真机闭环：EVA1 `2.0.9` 经MQTT+加密UDP完成一次真实回合。16:30:09进入笑声门禁，16:30:13开放ASR；火山final为“卧槽，疯掉了。”；奶龙回答“奶龙在呢！”与“怎么啦，遇到什么疯掉的事啦？”。
- TTS证据：一个playback、2句、85个60 ms Opus帧；首句28帧/80704 PCM字节，第二句56帧/162298 PCM字节，尾帧补1798字节。
- 会话清理：回答后先出现固件自动`listen/start`，随后设备在15 ms内确认goodbye；服务端UDP sessions由1回到0，WakeGate回waiting，EVA1诊断状态为idle、sound busy=false，且没有第二个`robot.action.requested`。
- 设备状态：EVA1为MQTT online、固件2.0.9、15动作；EVA2由用户关机，因此V9双机在线隔离保留NOT RUN。
- 服务暂停点：本机8081 Runtime保持健康且`voice_mvp=running`，8080诊断服务也在运行；没有待处理语音会话。
- 明确结论：单台EVA1纵向MVP通过；完整Phase 5仍欠EVA2、双Profile矩阵、正式CI和实体Windows验收。

## Gate 5：文档收尾

- [x] `docs/DEV_PROGRESS.md` 已更新
- [x] `docs/MODULE_STATUS.md` 已更新
- [x] `docs/LOG.md` 已更新
- [x] 架构和协议合同与实现一致
- [x] 本轮未运行项及原因已如实记录

## Gate 6：测试支线上传

- 测试支线：`test0.9`
- 验收提交：未创建
- push结果：未执行
- 远程commit：未创建
- 本地HEAD与远程一致：否
- 本轮是否获单独授权合并main：否
- 本轮是否获单独授权tag或正式发布：否

## 恢复施工的唯一安全顺序

1. 只读确认8081 Runtime、EVA1 `2.0.9 / mqtt / idle`及UDP sessions=0；不得重复OTA或重放上一轮命令。
2. 若继续单机对话，使用带设备令牌的诊断`voice_wake`建立一个新MQTT UDP session；每轮仍必须重新观测笑声忙闲边沿。
3. 下一功能优先为DeepSeek真实tool schema与受控动作映射；模型输出必须经过既有Dispatcher动作目录、参数、目标和confirmation校验，不能直接发设备JSON。
4. EVA2重新开机后补双设备隔离矩阵；所有测试必须按稳定device_id定向，不依赖IP或名称。
5. 再补WebSocket兼容Profile回归、Windows/PyInstaller Opus门禁和正式GitHub CI；未完成前不宣告完整Phase 5完成。
