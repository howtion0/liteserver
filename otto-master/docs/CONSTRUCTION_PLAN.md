# Otto Master施工计划

本文档规定从纯脚手架到 `1.0.0` MVP 的实现顺序。后续Phase不能以“文件存在”代替“功能通过验收”。

## 总路线

下表是最初的能力Phase与产品版本地图，不是已经使用的Git检查点编号。实际施工因增加4A-4E和纵向MVP检查点而顺延，当前真实支线序列见表后说明。

```text
Recovery version 0.1.0  branch test0.1  Phase 0 + Phase 1恢复基线
Phase 2  version 0.2.0  branch test0.2  SQLite持久化与迁移
Phase 3  version 0.3.0  branch test0.3  WebUI + REST + OTA + mDNS + Embedded MQTT Broker
Phase 4  version 0.4.0  branch test0.4  MQTT集群控制 + TCP回退 + WebSocket兼容
Phase 5  version 0.5.0  branch test0.5  Opus + 云端ASR/TTS
Phase 6  version 0.6.0  branch test0.6  Siri式WakeGate
Phase 7  version 0.7.0  branch test0.7  LLM + Dispatcher + Otto动作
Phase 8  version 0.8.0  branch test0.8  多设备集群、日志和容错
Phase 9  version 0.9.0  branch test0.9  Windows打包与端到端验收
MVP      version 1.0.0  branch test1.0  EVA1/EVA2完整链路通过
```

Phase 0和Phase 1已在强制门禁建立前完成但没有GitHub检查点，因此不得伪造两段历史；`test0.1` 是一次性恢复基线。支线编号是连续施工检查点，不是产品版本。若中途增加修复检查点，使用下一个自然编号，后续阶段顺延，不复用旧编号，也不特别处理 `test0.9` 到 `test1.0` 的变化。

实际检查点为：`test0.1=Phase 0+1`、`test0.2=Phase 2`、`test0.3=Phase 3`、`test0.4-0.8=Phase 4A-4E`、`test0.9=EVA1语音/WakeGate/单设备工具纵向MVP`。下一未占用支线是`test1.0`，用于多设备WebUI与并发会话可视化，不代表整个1.0.0 MVP已完成。

## 2026-09-18最快MVP关键路径

原Phase编号继续作为能力地图，但实际施工按可运行纵向切片排序，不为凑齐横向模块而延迟真机闭环：

```text
M0  关闭Phase 4剩余高风险门禁
    Broker重启恢复 + 真机重复命令ID + 两台最终idle

M1  单台EVA语音闭环（最快可听见结果）
    EVA1 WebSocket二进制Opus → PCM → 火山ASR
    固定回复文本 → 火山TTS PCM → 60 ms Opus → EVA1播放

M2  最小智能闭环
    WakeGate（你好EVA1）→ DeepSeek结构化意图
    先只开放白名单问答与一个低风险动作，失败时不动作

M3  双机与目标数据面
    MQTT加密UDP音频 → EVA2 → device/utterance隔离 → 双机并发

M4  可交付门禁
    故障注入、日志/密钥扫描、WebUI、macOS/Windows、PyInstaller
```

执行约束：EVA2在M1期间保持已验证MQTT控制基线；EVA1切换WebSocket语音Profile不得擦除Wi-Fi或每设备身份。每个里程碑必须先通过fake/provider测试，再做单台真机，最后才扩到双机。ASR/TTS先用已经实测通过的火山协议，LLM只用DeepSeek结构化输出；不在M1引入唤醒、对话记忆、动作规划或MQTT UDP，以缩短首个端到端闭环。

## 全Phase强制施工门禁

每个Phase以及Phase内的补充检查点，都必须完整执行 `CODEX_CONSTRUCTION_WORKFLOW.md`：

```text
[ ] 上一检查点已经push到GitHub并核对远程哈希
[ ] 已完整阅读当前施工依据
[ ] 已建立本轮Session Contract
[ ] 每条验收标准都有具体测试或人工观测方法
[ ] 只施工契约内范围
[ ] 失败测试已经返工并重跑
[ ] 所有必需测试最终PASS
[ ] DEV_PROGRESS、MODULE_STATUS、LOG和Session Contract已更新
[ ] 本轮唯一提交已push到新的testN.N支线
[ ] 远程支线哈希与本地HEAD一致
```

任意一项未满足，Phase状态只能是未开始、进行中、失败或阻塞，不得写成完成。历史测试、其他传输或其他设备的结果不能替代本轮验收。

## Phase 0：文档脚手架

目标：建立不含业务实现的工程骨架和施工治理体系。

交付：

- `pyproject.toml`、`config.yaml`、`.env.example`、`.gitignore`
- 空Python模块、空Web资源和空测试文件
- 宪法、架构、Git、测试、Session Contract
- 施工计划、进度、模块状态、日志、调试和消息合同
- MQTT控制合同和EVA1/EVA2真机验收基线
- 运行产物目录，但不伪造数据库、日志和固件

验收：

- [x] 工程位于 `liteserver/otto-master/`
- [x] 目录结构完整
- [x] 业务文件没有实现代码
- [x] 配置不含真实密钥
- [x] 当前状态明确标记为不可运行

## Phase 1：Runtime与Message Bus

目标：建立可启动和可关闭的异步内核。

实现：

1. 配置加载和环境变量替换。
2. `Message`、`MessageKind`和验证规则。
3. Message Bus publish/subscribe/unsubscribe。
4. Runtime依赖组装、后台任务和优雅关闭。
5. 基础结构化日志。

约束：

- 不接硬件、不接云API、不建WebUI。
- Message Bus不理解payload。
- 订阅者异常不能终止总线。

验收：

- [x] `python -m otto_master` 可以启动并干净退出
- [x] `ruff check src tests`
- [x] `mypy src`
- [x] `tests/test_message_bus.py`覆盖投递、多个订阅者和异常隔离

## Phase 2：SQLite持久化

目标：建立本地持久数据的单一入口。

实现：

1. 数据库连接生命周期。
2. 版本化迁移。
3. devices、groups、messages、commands、results、settings表。
4. 消息日志订阅者和敏感字段过滤。
5. 单写者或受控事务策略。

验收：

- [x] 首次运行生成有效 `data/otto.db`
- [x] 重复迁移幂等
- [x] 并发写入测试无未处理锁错误
- [x] Runtime关闭前刷新在途数据

## Phase 3：Web、REST、OTA、mDNS与Embedded MQTT Broker

目标：把当前控制面放进同一个Python进程。

实现：

1. FastAPI应用和静态Web资源。
2. 设备、事件、命令和配置API骨架。
3. Web状态推送。
4. OTA manifest与固件下载。
5. `zeroconf`发布 `master.local`，并周期监视本机IPv4；Wi-Fi/网段变化时原位更新记录，短暂回环地址或更新失败不覆盖最后一个有效LAN记录。
6. 在同一Python进程启动和关闭MQTT 3.1.1 Broker。
7. OTA按MAC下发 `master.local:1883`、独立client_id、凭据和每设备Topic。
8. Broker健康状态、匿名访问关闭和最小Topic ACL。
9. 对 `amqtt>=0.12,<0.13` 做macOS与Windows可行性验证，通过后才锁定依赖。
10. 按 `docs/SERVER_CONSOLE_REQUIREMENTS.md` 实现Server状态、Broker状态、设备列表、事件流和设置页的P0骨架。
11. Browser API只通过Web Gateway、Message Bus和Dispatcher进入控制链，浏览器不得直连MQTT Broker。

验收：

- [x] 浏览器可打开WebUI
- [x] macOS和Windows地址行为一致
- [x] 固件不存在时明确返回404
- [x] mDNS注册和注销均可观测
- [x] mDNS在Server换网后自动更新地址，刷新任务、次数和失败可从健康接口观测
- [x] 无需另装Broker即可在macOS和Windows启动MQTT服务
- [x] 匿名MQTT连接被拒绝，设备不能订阅其他MAC的down Topic
- [x] OTA返回 `otto/v1/devices/{device_id}/up|down`，不返回固定IP
- [x] Broker候选在macOS和Windows完成启动、鉴权、消息往返、关闭及PyInstaller冒烟测试
- [x] WebUI显示Server、Broker、SQLite、mDNS和OTA组件的真实健康状态
- [x] 页面刷新或事件流重连后，快照与增量状态一致
- [x] Browser API和WebUI不返回MQTT密码、云API Key或任意Topic发布入口；受保护的设备发放接口除外

## Phase 4：MQTT集群控制、TCP回退与Device Session

目标：以当前TCP真机行为为基线，让EVA1/EVA2通过MQTT完成等价集群控制，同时保留TCP诊断回退和Xiaozhi WebSocket兼容。

实现：

1. MQTT Gateway订阅每设备up Topic并向精确down Topic发送命令。
2. hello、heartbeat、动作目录、状态、ACK和错误的协议翻译。
3. 补齐固件MQTT `stop`、上线心跳和命令ID去重。
4. Device Manager按MAC派生device_id注册、重连和断开，名称只作显示。
5. 每设备Session状态机、串行动作队列和accepted/moving/completed状态。
6. TCP `otto-master/1` Gateway作为迁移回退，输出相同内部Message。
7. Xiaozhi WebSocket握手、JSON和二进制Opus继续作为兼容Profile。
8. WebUI显示在线、动态IP、名称、MAC、固件、能力、实际传输和心跳。
9. 实现只读连接验证和需要安全确认的动作验证，逐步展示查询、ACK、moving和idle结果。
10. 动作控制使用设备动作目录和参数Schema；stop独立高优先级，广播拆成单设备结果。

Phase 4A检查点（`test0.4`）只完成不移动设备的上行联调端：

- [x] Master凭据连接内嵌Broker并只订阅设备up Topic
- [x] hello、heartbeat、state、actions、ACK和error严格翻译为内部Message
- [x] MAC派生Session去重、重连代次和`connecting → online → stale → offline`状态
- [x] SQLite schema v2保存设备快照与动作目录，重启不伪造online
- [x] Web列表、详情、动作目录和事件流读取实时Manager状态
- [x] 两个fake设备通过受保护OTA和真实Broker同时联调且无动作下行
- [x] Phase 4B精确down状态/动作目录查询、correlation、超时与只读验证报告
- [x] Phase 4C fake设备MQTT动作、stop、持久命令历史与完整生命周期
- [x] Phase 4D认证TCP回退、Xiaozhi WebSocket v1、fake多传输隔离与动作/stop闭环
- [x] 固件2.0.6 MQTT hello/heartbeat/stop/有界命令缓存与锁定本地发放
- [x] EVA1/EVA2真实OTA、独立MQTT身份、14动作、action/stop与基础隔离
- [x] 锁定改配拒绝、相同命令ID真机重复投递、Server/Broker重启与双机恢复

验收：

- [x] EVA1和EVA2通过MQTT同时连接，当前IP变化不影响身份
- [x] 两台设备均返回完整14动作目录
- [x] EVA1执行 `swing → stop → idle` 时EVA2状态不变
- [x] EVA2执行 `swing → stop → idle` 时EVA1状态不变
- [x] 相同命令ID重复投递不会执行两次
- [x] MQTT动作和stop消息均不retain
- [x] 重连不产生重复在线记录
- [x] 一台设备OTA/重启时不影响另一台在线与idle状态
- [x] Server/Broker重启后设备自动重连、重新hello并恢复WebUI查询
- [x] TCP诊断回退可按稳定MAC同时只读观测两台设备，主控制面仍明确显示实际`transport=mqtt`
- [x] 只读连接验证不移动机器人，并逐项验证心跳、状态查询和动作目录
- [x] 安全动作验证必须完成 `accepted → moving → idle`，超时路径自动stop并确认idle
- [x] 命令生命周期不把publish或ACK标记为动作完成
- [x] WebUI重启后从SQLite与新设备状态恢复设备、动作目录和最终idle结果

## Phase 5：Opus与云端ASR/TTS

目标：打通语音上行和语音回传。Phase 5原始边界只包含LLM文本问答；`test0.9`为最快纵向MVP提前拉入了Phase 7的“当前语音设备单工具”安全切片，`test1.0`再补多设备Web控制面，但没有开放广播自然语言控制。

Provider和协议已经通过独立烟测冻结；`test0.9`又完成EVA1的MQTT+加密UDP单机纵向MVP。完整设计、数据流、实测证据和剩余范围见 `docs/VOLCENGINE_SPEECH_INTEGRATION.md`。单机闭环不等于双设备、双Profile和Windows门禁全部完成。

实现：

1. 在 `audio/opus.py` 实现16/24 kHz、单声道、60 ms帧的Opus/PCM转换、跨块滚动缓冲、尾帧补齐和参数校验。
2. 按 `device_id + utterance_id` 建立有界、短生命周期音频数据面；Message Bus只传元数据和 `frame_ref`，不传或持久化原始音频。
3. 在 `gateways/cloud.py` 接入火山当前API Key鉴权、ASR二进制WebSocket协议和TTS流式HTTP，并把Provider错误转换为稳定内部错误。
4. 在 `services/asr.py` 实现火山ASR 1.0时长版适配器和fake provider；输入为16 kHz PCM，输出partial/final/failed规范消息。
5. 在 `services/tts.py` 实现火山TTS 2.0适配器和fake provider；直接请求24 kHz PCM，应用可配置S16LE饱和增益后再编码为60 ms Opus，避免MP3和FFmpeg转码链。
6. 严格实现 `tts start → sentence_start → Opus frames → stop`；start后任何失败、断开或取消都在清理路径尝试stop。
7. MQTT Profile使用现有加密UDP Opus，WebSocket Profile使用二进制Opus；两者共享相同内部音频和TTS状态合同。
8. 当前没有独立`tts_finished`；发送端按60 ms节奏完成全部帧并发出`tts stop`后，循环模式重新执行本地笑声门禁并开放下一条utterance。只有8秒静默、按钮退出或失败才关闭session。
9. 复用小智Server的连接生命周期、PCM滚动缓冲、60 ms节奏和TTS状态顺序；火山鉴权和二进制帧格式以当前官方文档为准，不复制旧协议。
10. 网络层仅使用现有 `httpx`、`websockets`、`opuslib-next`；不增加仅macOS可用的依赖，Windows `libopus` 和PyInstaller收集必须进入验收。

### `test0.9` 最快纵向 MVP 检查点

本检查点按最新最快路线先在EVA1跑通MQTT信令与AES-128-CTR UDP Opus的单台纵向闭环；WebSocket保留兼容实现。执行该检查点时EVA2由用户关机，因此本结果不会替代后续双设备和双Profile矩阵；EVA2后来重新在线也不自动补足验收。

1. 每次新的提问轮次开始时先关闭 ASR 输入并触发 EVA1 内置大笑。
2. 必须真实观测 `sound.busy=true` 后再观测 `sound.busy=false`；动作 ACK、idle、固定延时或预计音频长度均不能代替播放完成证据。
3. 大笑期间收到的麦克风帧直接丢弃；完成边沿之后只接受一个新建 utterance，才允许发送火山 ASR。
4. ASR final 文本交给 DeepSeek；流式增量文本按完整句切分，使用有界队列顺序调用火山 TTS，不等待完整回答才开始首句播放。
5. 一轮回答只发送一个 `tts start` 和一个最终 `tts stop`；每句先发 `sentence_start` 再发该句 60 ms Opus 帧。任何失败、取消或断开均进入 stop/清理路径。
6. EVA1 的 MQTT/WebSocket Profile 切换必须认证、可逆且保留原 MQTT 回滚资料；不得改动 EVA2 Profile。
7. 状态机、Provider 和句子切分先用 fake 时钟/Provider 验证，再运行真实 API 和 EVA1 真机；首个闭环见 `docs/sessions/20260918-voice-mvp-test0.9.md`，循环加固见`docs/sessions/20260919-voice-loop-test0.9.md`。
8. ASR、LLM、TTS各阶段使用有界生产者/消费者队列：当前45秒对话窗口下ASR 750帧、LLM 4项、TTS句子4/PCM 16/Opus 48；队列满必须失败关闭，不允许无限积压。
9. DeepSeek输入硬截断512字符，输出硬截断96字符并限制`max_tokens=96`；角色固定为奶龙，回答通常1至3句；火山TTS固定湾区大叔音。
10. 回答后在同一session重新执行“2秒本地笑声→新utterance→监听”，形成有界循环；每次开放监听后8秒内没有非空ASR partial即由Server退出。设备VAD只辅助ASR端点，不得因房间噪声或扬声器尾音延长WakeGate窗口。
11. Otto按钮在idle时进入循环对话，在connecting/listening/speaking时退出；按钮退出必须产生设备`goodbye`并释放UDP session，再按一次建立全新session。
12. DeepSeek请求携带由当前设备动作目录收窄得到的`tools/tool_choice`，流式组装至多一个`tool_call`；目标设备锁定当前语音session，参数经Schema再次校验后只通过现有Dispatcher执行。成功动作不追加TTS，失败只播固定失败提示，显式`laugh`完成后复用为下一轮笑声门禁。
13. 工具轮不写成普通assistant文本历史；真实tool/tool-result结构尚未进入`ChatMessage`前，宁可不保留该轮，也不能用“已执行”文本污染下一轮工具选择。

`test0.9`完成点（2026-09-19）：EVA1运行固件2.0.11。除既有“笑声→ASR→DeepSeek/TTS→再次笑声→下一轮监听”、按钮退出/重入和8秒静默退出外，真实DeepSeek还完成`self_otto_laugh`以及连续“大笑→后退两步”的工具调用，均由Dispatcher等待`completed`，最终只读验证为idle。工具文本历史缺陷在实测中暴露并修复；两次已完成的前进一步随后用后退两步补偿。最终本地`144 passed`、Ruff和mypy通过；详细记录见`docs/sessions/20260919-llm-tools-test0.9.md`。

### `test1.0` 多设备控制台与真机体验加固

1. 火山TTS的24 kHz S16LE PCM在Opus编码前应用`audio.tts_pcm_gain`，范围`0.25..4.0`，生产配置为2.0；逐样本饱和钳位并正确处理Provider块之间拆开的半个采样。
2. DeepSeek在工具前尚未成句的短前缀可丢弃；若完整句子已经进入TTS，则必须完成播放并发送stop后再串行执行唯一工具。工具后文字和多工具仍失败关闭，工具轮不写普通文本历史。
3. 笑声门禁的单次状态查询超时在总笑声时限内重试；开场动作因设备状态不安全被拒绝时，持续查询到`action_state=idle`且`sound_busy!=true`才重试提交。始终未观测完整`sound.busy true→false`仍失败关闭。
4. WebUI按稳定device_id多选1至16台设备，使用动作目录交集，显式确认后并发提交批量动作或stop；每台设备独立结果，同一设备仍由Dispatcher串行。
5. Server维护每设备有界对话投影，WebUI显示状态、用户转写、助手句子、工具/动作和错误；旧session、敏感字段和音频字段不能污染快照。
6. WebUI提供前进、后退、左右转、跳跃、左右摇摆、太空步、抖动、弯腰、大笑、复位和stop快捷按钮；目标仍来自显式勾选，目录缺失时先做只读verify。控制令牌只保存在浏览器会话；本机开发启动可通过仅限loopback、不会进入HTTP请求的URL fragment一次性注入并立即从地址栏移除。未授权或401时锁定所有mutation控件并常驻说明“未进入服务端”；正式8081控制面不再与旧8080诊断进程并存。
7. ASR使用相互独立的“非空partial稳定1.2秒”“已观测说话后VAD连续静音1.2秒”和“utterance开始后12秒硬上限”三个端点，任一获胜后排空尾帧并结束云输入；清理先原子摘除活动utterance。首次空final只触发一次本地大笑并重开监听，连续第二次空final正常退出，不调用LLM或卡在recognizing。Dispatcher等待舵机idle和本地sound非busy后才完成动作。
8. EVA固件2.0.15用21个看山对话表情和22个动作贴图替换中央旧大眼区域，保留顶部状态栏和底部聊天文字；动作结束/stop恢复最近基础表情。设备音量一次性迁移到100，动作任务优先级低于音频任务；正式对话控制先返回关联ACK，再异步切换音频通道。
9. 真机默认三步`swing`可略超15秒；生产动作完成时限提高到30秒，仍保留超时自动stop。ACK时限不变，避免用放宽设备接收门限掩盖断线。
10. 固件2.0.16把既有显式mDNS解析器接入正式MQTT：首次连接和每次重连都重新解析`master.local`，当次IPv4不写NVS；连接成功后hello/heartbeat继续按MAC、名字和当前DHCP IP更新Server动态表。

当前状态（2026-09-19）：本地锁文件、Ruff、mypy strict、187项pytest、Node语法和差异检查通过；Server新增mDNS地址监视与原位更新，固件2.0.16把显式mDNS接入正式MQTT，完整构建并推送`c4ad28e`。EVA1无需固定IP或重新配网即在新网段自动回连；EVA2/EVA3随后完整烧录2.0.16并分别以正确屏显名称、稳定MAC和独立MQTT身份上线。正式三机batch请求3、接受3、失败0，三条walk均走完requested/published/accepted/moving/completed并回online/idle；EVA2另完成一次按钮触发的MQTT+UDP问答烟测。Server实现与实测记录提交`caf7f7f`对应run `35408550039`的macOS/Windows矩阵全部PASS。换网前EVA1五轮问答及用户对话/TTS主观PASS继续有效；EVA2单会话和三机控制都不冒充多设备并发语音通过。详细记录见`docs/sessions/20260919-multidevice-webui-test1.0.md`和`docs/sessions/20260919-mdns-roaming-test1.0.md`。

纵向 MVP 的硬门禁是“大笑真实结束后才能开始听”。若没有完整的 `sound.busy true→false` 证据，本轮必须失败关闭，ASR 与 LLM 调用数必须为零。

验收：

- [x] 编解码单元测试覆盖16/24 kHz、60 ms帧、跨块缓冲、尾帧补齐和非法参数
- [x] fake ASR/TTS覆盖partial、final、取消、超时、限流、乱序和所有stop清理路径
- [x] 真实火山外部测试返回目标文本；无Key必须记为NOT RUN，不能算PASS
- [ ] EVA1和EVA2分别完成语音转写且不串设备、utterance或帧序号
- [ ] 指定文本分别能在EVA1和EVA2播放，另一台不播放，目标设备最终回到listening
- [ ] MQTT加密UDP与WebSocket二进制两种Profile分别完成至少一个闭环
- [x] API Key无效、403、429、5xx、超时和设备断开不使Runtime崩溃或遗留会话
- [x] 默认日志、SQLite和Browser事件流不保存原始语音、Base64音频、API Key或认证头
- [x] DeepSeek真实流式tool call、严格参数解析、Dispatcher完成语义和EVA1 `laugh/walk`安全实测通过
- [ ] macOS与Windows通过Opus编解码、fake Provider集成和PyInstaller导入冒烟

## Phase 6：Siri式WakeGate

目标：实现“你好设备 → 设备回应 → 再说命令”。

实现：

1. sleeping、wake_check、ack_playing、listening状态。
2. 休眠时ASR结果只用于唤醒匹配。
3. 命中后TTS回应“{device_name} 在”。
4. 收到播放完成才开启6秒命令窗口。
5. 15秒连续会话窗口和显式结束。
6. 超时、低置信度和API错误全部fail-closed。

验收：

- [ ] 未说唤醒词不会触发动作
- [ ] EVA1和EVA2不会互相误唤醒
- [ ] 机器人自己的回应不会被识别成用户命令
- [ ] 命令窗口超时后恢复休眠

## Phase 7：LLM、Dispatcher与Otto动作

目标：将已唤醒命令转为受控动作并投递设备。

实现：

1. [x] DeepSeek适配器支持文本与单个流式`tool_calls`结构化输出。
2. [x] 当前语音设备的动作Schema、参数范围、目标锁定和工具结果事件。
3. [ ] Dispatcher设备组和自然语言集群路由；单设备既有路由已复用。
4. [x] 每台设备有序动作队列。
5. [ ] 命令结果和错误回传WebUI；当前已进入Message Bus和持久命令历史。

验收：

- [ ] “EVA1前进”只控制EVA1
- [ ] “EVA2后退”只控制EVA2
- [ ] 广播必须显式确认目标范围
- [ ] 非法动作或参数被拒绝

## Phase 8：集群、可观测性与容错

目标：让多设备长时间运行可检查、可恢复。

实现：

1. 分组和广播汇总结果。
2. 心跳、延迟和队列深度指标。
3. JSONL结构化日志与WebUI事件流。
4. 云API并发限制、退避和熔断策略。
5. 消息队列背压和慢消费者策略。
6. MQTT连接数、消息延迟、命令ACK、完成超时和重连指标。

验收：

- [ ] 多设备并发互不串话
- [ ] 单设备或单Provider故障被隔离
- [ ] 日志可按device_id和correlation_id追踪
- [ ] 队列达到上限时行为明确
- [ ] QoS 1重复投递测试不产生重复动作

## Phase 9：Windows打包与端到端验收

目标：从macOS开发结果生成可在Windows运行的交付物。

实现：

1. PyInstaller打包配置。
2. 静态Web资源、证书和Opus动态库收集。
3. Windows启动、退出和防火墙说明。
4. 配置、数据、日志和固件目录布局。
5. 升级与回滚文档。
6. 完成 `docs/SERVER_CONSOLE_REQUIREMENTS.md` 全部P0控制台与打包冒烟项目。

验收：

- [ ] Windows干净环境可以启动
- [ ] WebUI、mDNS、SQLite、OTA和内嵌MQTT Broker可用
- [ ] EVA1和EVA2完整语音及动作流程通过
- [ ] 退出后无残留进程和锁定数据库
- [ ] 静态资源离线可用，不依赖Node.js、CDN或外部MQTT Broker
- [ ] 浏览器长时间打开、刷新和断网恢复后，设备和命令状态保持一致
- [ ] 端口冲突、防火墙、数据目录和组件启动失败都有明确诊断

## 1.0.0 MVP验收场景

```text
1. Windows启动otto-master.exe
2. EVA1与EVA2通过master.local取得MQTT配置并连接
3. WebUI按名称和MAC显示两台设备在线及 `transport=mqtt`
4. 用户说“你好 EVA1”
5. EVA1回应“EVA1 在”，EVA2保持安静
6. 用户说“向前走两步”
7. EVA1执行，WebUI显示命令和结果
8. WebUI显式广播EVA1前进、EVA2后退
9. 两台设备独立执行并返回结果
10. 重启Master后SQLite历史仍可查看
11. 重启MQTT Broker后两台设备恢复连接和状态查询
```

Phase 4真机步骤、Topic和JSON以 `docs/MQTT_CONTROL_CONTRACT.md` 为准。固件`2.0.5`的TCP测试只保留为历史行为基线；MQTT控制先在两台`2.0.6`真机通过，EVA1随后完成2.0.11语音/工具纵向链、2.0.15看山/音量/正式对话控制以及2.0.16 MQTT显式mDNS重连。当前EVA1/EVA2/EVA3均运行2.0.16，三机正式同批动作已完成；EVA2单会话问答成功仍不能替代下一轮多设备并发语音/工具验收。
