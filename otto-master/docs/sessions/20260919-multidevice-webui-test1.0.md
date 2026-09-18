# test1.0 多设备控制台、对话加固与看山表情施工契约

## 基本信息

- 日期：2026-09-19
- 当前版本：`0.4.3`，本轮目标版本`0.5.0`
- 当前Phase：多设备WebUI与并发会话可视化；补强Phase 5/7纵向链路
- Git迭代支线：`test1.0`
- 配套固件仓库：`/Users/howtion/otto`，分支`codex/otto-portable`

## Gate 0：GitHub基线

- 基线分支：`test0.9`
- 本地commit：`a2c22fb144beece1676625c39deee2b7d223d9df`
- 远程commit：`a2c22fb144beece1676625c39deee2b7d223d9df`
- 本地与远程一致：是
- 当前工作区已有改动及归属：根目录`README.md`、`.DS_Store`、`ZhiForge-看山70张指令贴图.zip`及`ZhiForge-看山指令贴图/`均属于用户；不得修改或提交。固件仓库已有的看山表情、动作映射、音量迁移、正式对话控制和调度优先级改动属于本轮，最终固化为2.0.15。
- 密钥、数据库、日志、固件二进制和无关改动已排除：是
- `origin/test1.0`：不存在，支线名可用

## Gate 1：施工依据已阅读

- [x] `AGENTS.md`
- [x] `ONBOARD.md`
- [x] `CODEX_CONSTRUCTION_WORKFLOW.md`
- [x] `CODEX_MASTER_REQUIREMENTS.md`
- [x] `CODEX_ARCHITECTURE.md`
- [x] `docs/DEV_PROGRESS.md`
- [x] 当前Phase的`docs/CONSTRUCTION_PLAN.md`
- [x] `docs/SERVER_CONSOLE_REQUIREMENTS.md`
- [x] `docs/MESSAGE_CONTRACTS.md`
- [x] `docs/DEVICE_TRANSPORT_CONTRACT.md`
- [x] `docs/MQTT_CONTROL_CONTRACT.md`
- [x] `docs/VOLCENGINE_SPEECH_INTEGRATION.md`
- [x] `CODEX_RULES_TESTING.md`
- [x] `CODEX_RULES_GIT.md`

## 本轮目标

1. 为火山TTS的24 kHz S16LE PCM增加服务端可配置增益，默认`2.0`倍并做16位饱和钳位；它只影响云TTS，不二次放大固件本地笑声和动作音效。
2. 容忍DeepSeek在结构化工具调用前输出短前缀：尚未形成完整句子的前缀丢弃；已经提交的完整句子先完成TTS并stop，再串行执行唯一工具。
3. WebUI按稳定`device_id`显示和选择多台设备，支持显式目标的批量动作与批量stop，逐设备返回独立结果；空选择、通配符和隐式全体均拒绝。
4. WebUI显示Server组件健康、每设备连接/动作状态、每个语音session的状态、用户转写、助手文字、工具结果及更新时间；刷新和事件重连后从快照恢复。对话快照和事件流均需控制台授权，不能公开家庭语音文字。
5. WebUI可对1至16台显式选中设备批量进入或退出循环对话；命令经Message Bus和正式设备Gateway逐台下发、逐台关联ACK，不能借用8080诊断链、原始MQTT或浏览器直连设备。
6. 固件2.0.15仅替换中央大眼表情区为用户提供的看山图片；顶部状态栏和底部对话文字保持原布局。动作实际开始时切换对应动作图，结束或stop后恢复最新基础表情。
7. 降低动作任务优先级以减少舵机插值对音频链的抢占，并一次性把设备输出音量迁移到100；Server状态和真机心跳提供可验证证据。
8. ASR除云partial稳定端点外，增加“本轮已说话后VAD连续静音1.2秒”兜底；云端无partial时不得再硬等30秒。空final只触发一次本地大笑并重开监听，不能卡在recognizing或调用LLM。
9. Dispatcher将本地`sound_busy`纳入动作准入与完成，避免舵机先idle时提前放行下一动作；WebUI提供显式多设备快捷动作和目录自动verify。
10. 本机WebUI新标签页可通过只限loopback且不进入HTTP的fragment一次性装入控制令牌并立即清除地址栏；LAN访问仍需显式认证。

## 涉及模块

- `config`、TTS、LLM、WakeGate、Runtime状态
- Web Gateway、事件投影、批量Dispatcher入口、静态WebUI
- 消息/语音/控制合同、施工进度和日志
- EVA固件Otto屏幕、动作调度、音量配置、看山图片资源
- GitHub Actions跨平台门禁

## 允许修改的路径

Otto Master仓库：

- `.github/workflows/otto-master-phase3.yml`
- `otto-master/config.yaml`
- `otto-master/pyproject.toml`
- `otto-master/uv.lock`
- `otto-master/src/otto_master/config.py`
- `otto-master/src/otto_master/devices/session.py`
- `otto-master/src/otto_master/dispatch/dispatcher.py`
- `otto-master/src/otto_master/gateways/device_protocol.py`
- `otto-master/src/otto_master/runtime.py`
- `otto-master/src/otto_master/services/asr.py`
- `otto-master/src/otto_master/services/llm.py`
- `otto-master/src/otto_master/services/tts.py`
- `otto-master/src/otto_master/services/wake_gate.py`
- `otto-master/src/otto_master/services/conversation_control.py`
- `otto-master/src/otto_master/gateways/web.py`
- `otto-master/src/otto_master/web/index.html`
- `otto-master/src/otto_master/web/app.js`
- `otto-master/src/otto_master/web/style.css`
- `otto-master/tests/test_config.py`
- `otto-master/tests/test_asr.py`
- `otto-master/tests/test_device_mqtt.py`
- `otto-master/tests/test_dispatcher.py`
- `otto-master/tests/test_llm.py`
- `otto-master/tests/test_tts.py`
- `otto-master/tests/test_web.py`
- `otto-master/tests/test_runtime.py`
- `otto-master/tests/test_wake_gate.py`
- `otto-master/tests/test_conversation_control.py`
- `otto-master/docs/**`
- `otto-master/CODEX_ARCHITECTURE.md`

固件仓库：

- `CMakeLists.txt`
- `BUILD_OTTO.md`
- `main/boards/otto-robot/NAILONG.md`
- `main/boards/otto-robot/otto_controller.cc`
- `main/boards/otto-robot/otto_controller_status.h`
- `main/boards/otto-robot/otto_emoji_display.cc`
- `main/boards/otto-robot/otto_emoji_display.h`
- `main/boards/otto-robot/otto_robot.cc`
- `main/application.cc`
- `main/application.h`
- `main/protocols/mqtt_protocol.cc`
- `main/boards/otto-robot/kanshan_emotions.c`
- `main/boards/otto-robot/kanshan_emotions.h`

用户贴图源目录、ZIP、根目录README、`.env`、数据库、JSONL日志、构建目录和固件二进制不在提交范围。

## 输入与外部依赖

- 是否需要ESP32：是
- 是否需要真实云API：是，最终TTS听感与真实对话烟测；自动测试使用fake
- 是否需要局域网：是
- 是否需要Windows验证：是，GitHub Actions；实体Windows仍不属于本轮可证明范围
- 是否需要MQTT Broker：内嵌真Broker
- 是否需要EVA真机：EVA1必需；EVA2当前关机，真实双机为可选补测，软件双设备隔离为必需
- 固件版本：EVA1目标`2.0.15`；EVA2保持原状态且不主动触碰
- 预期传输：MQTT控制/信令 + AES-CTR UDP Opus
- 安全清理：每次动作/语音测试结束stop并确认EVA1 idle、无活动音频session

## 冻结行为与边界

### TTS增益

- 配置字段为`audio.tts_pcm_gain`，范围`0.25..4.0`，默认配置`2.0`；代码级直接构造的安全回退为unity，生产配置必须显式给值。
- 增益应用于Provider返回的每个S16LE PCM采样，发生在Opus滚动编码器之前；正负样本均四舍五入后钳位到`[-32768, 32767]`。
- 奇数字节PCM视为Provider协议错误，不能猜测补齐。`1.0`必须逐字节保持不变。
- 状态接口公开增益数值，不公开密钥或音频内容。

### 文本与工具分流

- 在任何完整可朗读句子进入下游队列前，模型短前缀只留在LLM内部缓冲。
- 若随后出现唯一合法工具调用，丢弃该未朗读前缀并记录计数；不得将它写历史或送TTS。
- 若已经向下游提交完整句子后才出现唯一工具，先完成该轮TTS并发送stop，再串行执行工具；语音和动作不得重叠。该工具轮仍不写普通文本历史。
- 多工具、未知工具、非法参数和目标注入继续拒绝。

### 多设备控制

- 批量动作必须提供1至16个唯一稳定`device_id`、显式`confirmation=true`和结构化动作参数。
- Server并发提交不同设备；每台设备仍由既有Dispatcher串行。返回值逐设备给出`accepted/failed`及command或稳定错误，不因一台失败掩盖其他结果。
- 不接受名称、IP、MQTT Topic、通配符或空集合来表达目标；批量stop遵循同样规则。
- 浏览器仍不直连MQTT，不持有设备凭据。

### 批量对话控制

- 会话start/stop必须提供1至16个唯一稳定`device_id`和显式`confirmation=true`；空集合、重复、名称、IP、Topic和通配符全部拒绝。
- Web Gateway只调用Conversation Control Service；Service检查设备在线、传输与`conversation_control`能力后，发布`device.conversation.start.requested|stop.requested`，Gateway再编码为精确单设备命令。
- 外部命令使用唯一且有界的字符串ID；固件对重复ID只重放缓存ACK，不得重复开关会话。ACK只表示设备接受了切换请求，真实进入对话仍以新`audio.input.started`和WakeGate session为准。
- 批量调用并发等待各设备ACK并逐台返回`accepted/failed`；一台失败不得取消或掩盖其他设备。退出请求幂等，空闲设备也可安全接受；启动请求不得在升级、配网或错误状态强行执行。
- 该路径不得使用旧TCP 8765/HTTP 8080诊断服务，不在浏览器或事件投影暴露MQTT凭据。

### 对话可视化

- 每个设备独立保留当前/最近session投影：session、utterance、state、用户final文字、助手句子、工具状态、错误码和更新时间。
- 投影只消费脱敏Message；不包含PCM、Opus、Base64、认证头或API Key。
- 快照有界，事件重连后可恢复；旧session事件不得覆盖新session。

### ASR端点与空结果恢复

- partial稳定与设备VAD静音是同一utterance的竞争端点，只有一个路径能关闭输入。VAD路径必须先看见`speaking=true`；开始说话前的`false`不能误触发。
- 连续静音1.2秒后先设置`input_finished`，再把sentinel排在已接收PCM尾帧之后；新帧丢弃，既有尾帧不能截断。
- 空白final不送DeepSeek、不上屏、不增加turn。WakeGate只安排一次本地`laugh`并重新走笑声完成门禁；重复或过期final被忽略。

### 动作与本地音效完成边界

- `sound_busy=true`时拒绝新普通动作；stop继续保持独立高优先级。
- 动作收到accepted并进入moving后，只有`action_state=idle`且`sound_busy!=true`才进入completed。旧设备不提供sound字段时维持原兼容行为。
- WakeGate的开场笑声若收到`device_state_unsafe`，在有界截止时间内重复查询，只有action idle且sound非busy才重新提交。
- WebUI快捷按钮仍从所选在线设备目录交集启用；空目标、离线目标或目录未取得时禁用，目录为0时先执行受保护的只读verify。

### 本机WebUI授权

- 控制令牌继续只从Server环境读取，静态HTML、API、事件和日志均不返回令牌。
- 开发机可以在`127.0.0.1/localhost/::1`页面通过`#console_token=...`片段一次性写入当前标签页`sessionStorage`；fragment不进入HTTP请求，读取后立即清除。非loopback主机名不能使用该入口。
- 无令牌或401时必须锁定动作、stop和对话控件，显示授权徽标并常驻提示“命令未进入服务端”；同时聚焦令牌输入，不能把未发送的动作显示成设备无响应。
- 正式控制台固定为8081；遗留8080诊断进程必须停止，避免用户进入不受当前Message Bus/Dispatcher约束的旧页面。

## 验收矩阵

| 编号 | 二元验收标准 | 验证命令或人工步骤 | 必需 | 最终状态 | 证据 |
|---|---|---|---|---|---|
| A1 | TTS 2.0倍增益、正负钳位、unity和奇数字节拒绝精确通过 | `uv run pytest -q tests/test_tts.py tests/test_config.py` | 是 | PASS | 增益实现、配置边界及跨Provider块半采样回归均通过；全量176项包含该覆盖 |
| A2 | LLM丢弃未朗读短前缀；完整句子先播完并stop后再执行唯一工具 | `uv run pytest -q tests/test_llm.py tests/test_wake_gate.py` | 是 | PASS | 顺序断言覆盖`answer:stop < tool:execute`；瞬时笑声状态查询超时会在总时限内重试 |
| A3 | 两个fake设备批量动作并发、结果隔离、空目标/通配/未确认拒绝，批量stop清理 | `uv run pytest -q tests/test_web.py tests/test_dispatcher.py` | 是 | PASS | fake双设备并发、独立结果和拒绝路径通过；EVA1真实两次3步前进、左转、太空步及stop均completed，同设备重叠点击正确串行 |
| A4 | 对话快照按设备/session隔离并包含ASR、助手文字、工具结果；敏感/音频字段不泄漏 | `uv run pytest -q tests/test_web.py tests/test_runtime.py` | 是 | PASS | 投影跨设备、旧session、文字保留/替换及脱敏回归通过 |
| A5 | WebUI具备多选目标、快捷/高级批量控制、组件健康和独立对话泳道，前端语法通过 | `node --check src/otto_master/web/app.js`及HTTP浏览器/API烟测 | 是 | PASS | 新授权页事件流在线，目录自动恢复15项；真实前进/转向/太空步从`webui:batch`进入MQTT并completed；授权徽标、未授权控件锁定及常驻错误已覆盖，旧8080进程已停止；Node语法通过 |
| A5b | 批量对话start/stop经正式Message Bus和设备Gateway逐台关联ACK，显式目标/确认/能力门禁及结果隔离通过 | 协议、Service、Web与Runtime自动测试；EVA1真实start→新session→stop→waiting | 是 | PASS | 软件隔离/拒绝路径通过；EVA1固件2.0.15先回关联ACK再异步切换音频，真实start约104 ms接受，stop约19 ms接受并回waiting |
| A5c | 云端无partial时VAD静音1.2秒仍能收句；空final不调用LLM且只笑一次重开监听；动作等待本地音效排空 | ASR/WakeGate/Dispatcher自动回归与EVA1故障复现 | 是 | IN PROGRESS | 491帧/零partial故障已复现；修复后真机1.202秒触发`vad_silence`且不再30秒超时，空final、sound drain及开场笑声等待idle+非busy自动回归通过；空final重开监听待真机复验 |
| A6 | 固件恰有21个表情与22个动作贴图；中央图替换且状态栏/底部文字路径保持 | 静态断言、ESP-IDF完整构建和EVA1真机观察 | 是 | IN PROGRESS | 43个描述符纳入构建，2.0.15完整构建和OTA通过；动作图切换/恢复仍待用户目视确认 |
| A7 | EVA1升级2.0.15后动作图随实际动作切换并恢复，心跳显示音量100，动作最终idle | OTA回连、heartbeat、低风险动作、stop/idle及用户目视确认 | 是 | IN PROGRESS | EVA1回连报告2.0.15与音量100，多种真实动作completed且最终idle；只缺目视确认 |
| A8 | 真实火山TTS经2.0倍增益与设备音量100播放，EVA1可清楚听见且无明显削波/卡顿 | 真实短句TTS播放、遥测清理及用户听感确认 | 是 | IN PROGRESS | 服务状态确认增益2.0、设备心跳确认100；等待用户试听结论 |
| A9 | 自动门禁与跨平台CI通过 | `uv lock --check`; Ruff; mypy; 全量pytest; `git diff --check`; GitHub macOS/Windows jobs | 是 | IN PROGRESS | 本地锁文件、Ruff、mypy（38个源码文件）、176项pytest、Node语法和diff check通过；CI待push |
| A10 | EVA1+EVA2真实同时在线、并发对话/控制不串线 | EVA2开机后真机双设备测试 | 否 | NOT RUN | EVA2当前由用户关机；不能用fake冒充真机通过 |
| A11 | 结束时EVA1 idle、活动语音/UDP session为0，无遗留测试进程 | 只读健康、设备与会话状态检查 | 是 | PASS | EVA1 online/idle、sound_busy=false；WakeGate sessions与UDP sessions均为0，Dispatcher active/queued均为0 |

只有A1-A9（含A5b、A5c）与A11全部PASS才允许形成`test1.0`验收提交。A10若设备保持关机必须如实记录NOT RUN，但不冒充双机真机验收。

## 非目标

- 不合并`main`、不创建tag或GitHub Release。
- 不实现浏览器麦克风代替设备麦克风，不伪造“同时对话”真机结果。
- 不让浏览器发送原始MQTT Topic/JSON，不把自然语言广播直接变成动作。
- 不把诊断8080的`voice_wake`旁路包装成正式WebUI功能。
- 不改变顶部状态栏或底部对话文字布局，不修改用户原始贴图。
- 不完成实体Windows局域网、防火墙或完整PyInstaller GUI交付。

## 风险

- 2.0倍PCM增益可能让已经接近满幅的TTS发生钳位；实现必须饱和而非整数回绕，并通过短句真人听感确认。若有明显破音，下一轮应使用压缩/限幅而非继续提高硬增益。
- DeepSeek流式顺序不稳定；仅允许丢弃尚未进入TTS的短前缀，不能撤回已经播放的语音。
- 批量动作有物理风险；WebUI必须展示精确目标并要求确认，真机默认只用低风险原地动作。
- 看山C数组占用Flash；构建必须检查app分区余量。
- EVA2离线意味着本轮只能证明软件双设备并发，不能声称真实双机语音已通过。

## 回滚点

- Otto Master：`test0.9@a2c22fb144beece1676625c39deee2b7d223d9df`
- 固件：`codex/otto-portable@abb769f1d0f3d1c03fb7a6106bd7288f31c66a98`（2.0.11）
- 本轮2.0.15固件源码检查点：`codex/otto-portable@c6addc6a35bf54c6c28f07fde53828cd73bce1f0`；该项是当前前进点，不替代上述2.0.11回滚点。
- EVA1保留经OTA回退到2.0.11的能力；任何失败先stop并确认idle，再决定回退。

## 计划验证

```text
uv lock --check
uv run ruff check src tests
uv run mypy src
uv run pytest -q tests/test_config.py tests/test_tts.py tests/test_llm.py
uv run pytest -q tests/test_web.py tests/test_runtime.py tests/test_dispatcher.py
uv run pytest -q
node --check src/otto_master/web/app.js
git diff --check
idf.py build
EVA1 OTA → 2.0.15 reconnect → heartbeat volume=100
EVA1 low-risk action image → restored base image → idle
EVA1 real amplified TTS → user listening confirmation → goodbye/stop → no active session
push test1.0 → verify remote hash → GitHub macOS/Windows success
```

## 测试—返工记录

| 轮次 | 测试 | 初始状态 | 失败摘要或阻塞证据 | 修复 | 重测结果 |
|---|---|---|---|---|---|
| 1 | LLM/WakeGate定向测试 | FAIL | 新断言引用`LlmSentence`但测试文件漏导入 | 补齐显式导入 | 47项定向测试通过 |
| 2 | 全量pytest | FAIL | 旧UDP测试直接构造`AudioConfig`时没有新增增益字段；本机`.env`启用控制台令牌后旧Runtime WebSocket测试未隔离环境 | `AudioConfig`提供安全unity构造回退；Runtime测试显式关闭控制台令牌 | 加入配置边界覆盖后全量161项通过 |
| 3 | EVA1真实循环对话 | FAIL | DeepSeek先输出完整句子再给工具导致`mixed_text_and_tool_call`；一次笑声状态查询瞬时超时导致session失败 | 完整句子TTS结束后串行工具；笑声查询在总门限内重试瞬时超时 | 自动顺序/重试回归通过，真实复测待最终试听 |
| 4 | TTS真机音量 | FAIL | 用户确认1.5倍增益与设备音量90仍偏小 | 服务端改为2.0倍；EVA1音量升至100并固化在2.0.13 | OTA回连、版本与音量遥测通过；主观听感待确认 |
| 5 | 正式Web对话start | FAIL | 固件先同步打开音频通道再回ACK，UDP协商可能超过Server 3秒关联等待，设备实际开始但控制面假超时 | 2.0.15在主循环先发送并缓存ACK，再异步执行会话切换 | EVA1真实start约104 ms ACK，随后新MQTT/UDP session建立；stop约19 ms ACK并回waiting |
| 6 | 03:45与当前卡顿对比 | FAIL | 顺滑回合1.1至3.1秒持续收到火山partial；故障轮491帧和VAD已到Server但零partial，旧ASR只靠partial端点并等满30秒 | 增加已说话后的VAD静音1.2秒端点，保留队列尾帧 | 真机最后一次VAD false后1.202秒收句，148 ms收到云final；30秒悬挂消失 |
| 7 | VAD兜底首次复测 | FAIL | 火山返回空final，WakeGate因直接忽略空文本而永久停在recognizing | 空final不进LLM，幂等切到laughing，完成一次本地笑声后重开监听 | 自动回归覆盖空白与重复final且只新增一次动作；真机复验待完成 |
| 8 | WebUI快捷前进 | FAIL | 新打开标签页没有旧页的sessionStorage令牌；页面可读状态但mutation被401拒绝，Server/设备均未收到walk | 加入loopback-only fragment授权引导、立即清除URL片段，并在401时聚焦令牌输入 | 新页事件订阅恢复；两次前进、左转和太空步由`webui:batch`真实completed |
| 9 | 动作/音效生命周期 | FAIL | 舵机idle快于本地OGG，旧Dispatcher可能提前completed并放行下一动作；一次旧采样仅证明查询瞬间仍busy | 准入拒绝`sound_busy=true`，完成同时等待action idle和sound非busy | 自动测试证明idle/busy期间保持moving；EVA1一歩walk在本地音效结束后5.31秒completed |
| 10 | WebUI再次点击前进 | FAIL | 正式8081没有新命令、消息或MQTT发布，EVA1仍online/idle；本机同时遗留8080诊断入口，页面也未持续区分只读可见与控制授权 | 停止旧8080进程；增加授权徽标、无/失效令牌控件锁定、401聚焦及常驻提交状态 | 修复后用户从8081真实执行前进、抖动、弯腰和大笑，均进入`webui:batch`并completed；入口/授权问题复验通过 |
| 11 | WebUI修复后连续动作 | PARTIAL | 前进、抖动、弯腰和大笑均completed且动作图遥测正确；合法三步`swing`在15.254秒被统一完成时限误判并触发安全stop | 生产`completion_timeout_seconds`提高到30，ACK仍为3秒，超时安全stop不取消 | 前四项及后续两次前进真机通过；30秒门限下的`swing`待重启后复验 |

## 实际结果

- 类型检查：mypy strict通过，38个源码文件无问题
- 单元测试：全量176项通过
- 集成测试：内嵌MQTT、HTTP/WebSocket、快捷/批量控制、正式对话控制、控制授权可见性、对话投影、ASR双端点、空final恢复、音效排空及Runtime生命周期均在全量测试中通过
- 固件构建：ESP-IDF 5.5.5完整构建2.0.15，应用镜像3,830,880字节，SHA256 `e1ca9051c8f6a2aac1bef3e47323c1927ef6bebc3b901ae52bc393f9ad4595e6`
- 固件Git：`howtion0/otto`远端`codex/otto-portable@c6addc6a35bf54c6c28f07fde53828cd73bce1f0`，本地与远端SHA一致
- 硬件测试：进行中；2.0.15 OTA/回连、音量100遥测、正式对话start/stop、VAD兜底收句及多种WebUI动作已取得客观证据；空final重开监听、表情切换与TTS听感待用户最终确认
- 实际设备与传输：EVA1 / MQTT+UDP；EVA2离线
- stop与idle清理：批量stop已接受并完成；当前EVA1 online/idle、无活动语音或UDP session、无在途命令

## Gate 5：文档收尾

- [x] `docs/DEV_PROGRESS.md`已更新
- [x] `docs/MODULE_STATUS.md`已更新
- [x] `docs/LOG.md`已更新
- [x] 架构和协议合同与实现一致
- [x] 本轮未运行项及原因已如实记录

## Gate 6：测试支线上传

- 测试支线：`test1.0`
- 验收提交：待生成
- push结果：未执行
- 远程commit：待生成
- 本地HEAD与远程一致：否
- 本轮是否获单独授权合并main：否
- 本轮是否获单独授权tag或正式发布：否
