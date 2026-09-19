# Construction Log

本文件保存索引和最近施工记录。长期记录按版本归档到 `docs/logs/`。

## 归档

| 版本 | 文件 |
|---|---|
| 0.0.0 | `docs/logs/LOG-0.0.0.md` |

## 记录模板

```markdown
## YYYY-MM-DD / Phase N / 标题

### 版本
- `x.y.z`

### Git迭代
- `testN.N`

### 目标
- 本轮目标

### 修改范围
- 文件或模块

### 验证
- 命令：结果
- 未运行项及原因

### 风险
- 风险或无

### 回滚判断
- 是否需要回滚及目标

### 下一步
- 下一项工作
```

## 最近记录

### 2026-09-18 / Phase 4E / 双EVA固件2.0.6与真实MQTT基础门禁

- 版本：Otto Master保持`0.4.3`；EVA固件`2.0.5 → 2.0.6`。
- Git迭代：`test0.8`，基线为已验收`test0.7` / `2b22a724d7169c2f84c5e028b95f40de7c0c4964`；本地与真机门禁完成，正式CI待提交后运行。
- 目标：补齐固件MQTT hello/heartbeat/stop/命令去重与一次性本地发放，并用EVA1/EVA2证明本机Server可按MAC独立查询和控制。
- 固件修改：MQTT endpoint解析与5秒重连、`otto-mqtt/1` hello/5秒heartbeat、stop ACK、32项命令响应缓存、14动作参数Schema、NVS名称与凭据、TCP固件版本、目标MAC校验及发放锁；OTA后优先锁定本地MQTT配置。
- 安全：每设备client/username/up/down topic必须与MAC精确匹配；password/token/current_token递归脱敏；发放材料只在内存转发；`.env`与`.local-secrets/`保持Git忽略且未输出真实值。
- 构建：ESP-IDF 5.5.5成功；`xiaozhi.bin` 3,768,160字节，SHA256 `7580fe7f3d78641561d5a9968a5ed75b81697b2d5e2fe09b4ddad55220f5e551`。
- 本机自动门：针对MQTT/Server的22个测试和全量80个pytest通过；`uv lock --check`、Ruff、mypy 34源码、前端JS语法、diff检查通过。
- EVA1实测：`2.0.6`、MQTT online；verify 367.829 ms返回idle与14动作；swing进入moving后stop完成，2步及6步walk完成，6步约7.399秒。
- EVA2实测：OTA下载HTTP 200，稍晚于首个50秒轮询窗口在13:57:15以`2.0.6` hello；发放时将默认名修正为NVS `EVA2`。MQTT verify 189.897 ms返回idle与14动作；swing/stop及6步walk完成，6步约7.455秒。
- 隔离与收尾：任一设备动作时另一台保持idle；最终两台均`online / mqtt / 2.0.6 / idle`且`last_error=null`。
- 音效返工：首次错误地把不支持MCP的诊断TCP当作纯笑声入口，未计为通过；随后用Server的`swing amount=0`触发14.792秒内置音效，以心跳`sound.busy`确认累计超过60秒。一次ACK丢失正确触发Server安全stop，音频仍自然结束。
- 安全与去重：EVA1在无`current_token`的攻击性二次发放中返回`provisioning is locked`并保持原配置；相同`gate-e3-duplicate-20260918`动作包实投两次，设备返回两份完全一致的缓存ACK，未二次排队，随后安全stop。
- 恢复：Otto Master与内嵌Broker完整停止再启动，两台EVA无需重启即重新hello；EVA1/EVA2 verify分别615.000 ms和91.554 ms通过，WebUI HTTP 200，最终集群stop后均online/idle。
- 本地复验：`uv lock --check`、Ruff、mypy strict（34源码）、80个pytest、前端JS语法和diff检查全部通过。
- 未运行：`test0.8`正式CI与实体Windows；实体Windows属于Phase 9，不用本轮CI代替。
- 下一步：按用户指定顺序，在EVA1实现`完整大笑结束 → 开启收音 → 火山STT → DeepSeek流式回复 → 分句火山TTS → 顺序播放`；笑声期间禁用/丢弃收音，超时失败关闭。随后补WakeGate、MQTT UDP音频、第二台语音与Windows。

### 2026-09-18 / Phase 4D / TCP回退与Xiaozhi WebSocket兼容传输

- 版本：`0.4.3`
- Git迭代：`test0.7`，基线为已推送并通过正式矩阵的`test0.6` / `f359a1fc3a50360fbcb2de42cced2f48eeb6c164`
- 目标：让认证TCP `otto-master/1`与Xiaozhi WebSocket v1复用MQTT的内部设备消息、Verifier、Dispatcher和持久命令生命周期，并按`mqtt → websocket → tcp`安全选择传输。
- 修改：抽取共享设备协议翻译器；实现TCP真实Socket Gateway和设备WebSocket hello/listen/abort、Otto文本扩展及raw Opus引用缓冲；Runtime、健康、受保护发放、Manager、Session、Verifier和Dispatcher全部改为transport感知。
- 安全：TCP/WS同时核对设备token、MAC派生device_id和client_id；Gateway只发送匹配自身transport的命令；非首选Profile隔离，传输切换不重放动作；音频原始bytes不进入Message、SQLite或Browser事件。
- 本机验证：`uv lock --check`、Ruff、mypy strict（34个源码文件）、前端JS语法与`git diff --check`均PASS；`pytest -q`为80 PASS。真实loopback HTTP/WebSocket、TCP和内嵌MQTT并存，TCP/WS fake分别完成查询、action、stop及资源释放。
- 返工：Dispatcher原先只接收带correlation的状态事实，无法在无correlation的首选传输切换时终止已锁定命令；改为消费全部设备状态变化并按transport失败关闭。Verifier补充错误transport拒绝；WS音频按Phase 5文档补齐`utterance_id + sequence + frame_ref`引用合同。
- 未运行：EVA1/EVA2真机、固件修改、QoS 1、TLS、Xiaozhi v2/v3、Opus解码、ASR/TTS/LLM/WakeGate和实体Windows局域网；fake与CI不能替代这些验收。
- 远程验证：精确暂存树`1c98ca1eb7c2c7e5efbd7f58eefc4703d38fd95a`的GitHub Actions run `35309090968`通过；Windows job `105487213519`、macOS job `105487213801`均完成80个测试、静态检查、PyInstaller构建和Broker可执行文件实跑。正式SHA结果在提交/push后由交付报告与下一检查点回填。
- 下一步：完成`test0.7`唯一提交、push和正式SHA矩阵后按用户要求暂停，不自动创建下一支线或启动Phase 5。

### 2026-09-18 / Phase 5设计 / 火山ASR、TTS与Opus接入合同

- 版本：工作区当前为`0.4.3`；本轮不提升版本，不表示Phase 5已实现。
- Git迭代：当前为进行中的`test0.7` Phase 4D工作区，远程可恢复基线为`origin/test0.6` / `f359a1fc3a50360fbcb2de42cced2f48eeb6c164`；本轮文档补充未单独commit或push，避免抢先封存尚未验收的Phase 4D代码。
- 目标：冻结单Python、macOS开发/Windows部署条件下的火山ASR/TTS数据流、内部消息、依赖、重试、复用边界和二元验收，不编写业务代码。
- 云烟测：火山TTS 2.0流式HTTP返回24 kHz单声道PCM，首音频约524 ms、总请求约1243 ms；267102字节PCM编码为93个60 ms Opus帧，首帧回解成功。ASR 1.0时长版双向WebSocket首个partial约986 ms，源音频结束后约369 ms返回精确最终文本。
- 资源决定：TTS使用`seed-tts-2.0`；ASR当前使用已通过的`volc.bigasr.sauc.duration`。ASR 2.0资源返回403，按未授权/未开通处理，不能误报为API Key整体无效。
- 架构：Message Bus只传控制状态、音频元数据和短期`frame_ref`；PCM/Opus放在按设备与utterance隔离的有界内存数据面。TTS严格执行`start → sentence_start → audio → stop`，合成完成与设备播放完成分开建模。
- 复用：参考小智Server的WebSocket生命周期、PCM滚动缓冲、60 ms节奏、hello协商和TTS状态顺序；火山鉴权与二进制帧以当前官方协议为准，不复制旧协议或整套Server结构。
- 跨平台：只计划使用现有`httpx`、`websockets`、`opuslib-next`，不增加FFmpeg/pydub/numpy运行时链；Windows `libopus` 和PyInstaller收集列为Phase 5/9硬门禁。
- 本地环境：`.env`中的`OTTO_ASR_API_KEY`、`OTTO_TTS_API_KEY`和`DEEPSEEK_API_KEY`均已配置；文件权限为600且由Git忽略，文档、日志和Git中未写入真实值。因火山测试Key曾出现在用户截图中，正式部署前仍须轮换。
- 验证：文档存在、代码围栏配对、无尾随空白、关键端点/资源ID/交叉引用、Phase 5仍标记未开始、密钥模式扫描和`git diff --check`均PASS；`.env`加载、三项变量非空、ASR/TTS Key一致、权限600和Git忽略检查PASS。
- 未运行：Otto Master ASR/TTS实现测试、EVA1/EVA2语音与播放、MQTT加密UDP音频、WebSocket音频、Windows与PyInstaller；这些仍是Phase 5正式施工验收项。
- 下一步：先完成并验收当前Phase 4D；随后从最新稳定基线建立下一个未占用测试支线，按`docs/VOLCENGINE_SPEECH_INTEGRATION.md`建立Session Contract再实现Phase 5。

### 2026-09-18 / Phase 4C / MQTT动作、stop与持久命令生命周期

- 版本：`0.4.2`
- Git迭代：`test0.6`，基线为已推送的 `test0.5` / `8120d6341fc80d35f3ecf68e2320559c10a5604f`
- 目标：仅在fake设备上完成受保护Web API→Message Bus→Dispatcher→MQTT Gateway→精确单设备down Topic→ACK/state→SQLite的动作和stop闭环。
- 修改：新增命令状态合同与持久仓库，schema升至v3；实现每设备有界worker、跨设备并行、stop抢占/取消、集群stop拆分、乱序事实缓冲、状态查询轮询、超时安全stop与重启失败关闭；接入action/stop/cluster-stop/命令查询API。
- 协议：Gateway只接受`device.action.execute.requested|device.stop.execute.requested`，外部ID等于command ID，精确down Topic、QoS 0、`retain=false`；ACK只标记accepted，动作必须再观测moving和idle才completed。
- 安全：动作要求online/mqtt/enabled、actions能力、动作目录、参数Schema和显式confirmation；Browser无任意Topic/JSON入口；队列满、冲突ID、断线和超时都失败关闭，动作不自动重发。
- 本机验证：锁文件、Ruff、mypy strict和前端JS语法PASS；`pytest -q` 71 PASS。Dispatcher用例覆盖串行、并行、乱序、有界队列、stop抢占、ACK/idle超时、断线与集群stop。真实Broker双fake用例完成EVA1 `otto_action → stop`，EVA2无串线，重复command ID只下发一次，关闭后命令终态仍可查询。
- 远程验证：GitHub Actions探针run `35306214077`中Windows job `105478815027`、macOS job `105478815315`均success；两边均完成锁定安装、Ruff、mypy、71个pytest、PyInstaller构建和Broker可执行文件实跑。
- 最终验收：`test0.6` / `f359a1fc3a50360fbcb2de42cced2f48eeb6c164`已推送且远程哈希一致；正式run `35306518034`的Windows job `105479722338`、macOS job `105479722462`均success。
- 返工：扩展真Broker用例后，原0.5秒心跳stale阈值在新增动作链后正确触发，改为用例内显式刷新心跳与稳定测试阈值；Pydantic不支持当前隐式递归`JsonValue`生成Schema，恢复API边界`Any`并在Message合同层完成严格复制/拒绝；额外封堵归一化参数名覆盖保留字段。
- 未运行：EVA1/EVA2真机、固件改造、TCP回退、Xiaozhi WebSocket、QoS 1、云服务和实体Windows局域网。
- 状态：Phase 4C本地门禁、跨平台探针、最终push/哈希和正式SHA矩阵全部PASS；完整Phase 4仍进行中。
- 下一步：实现TCP `otto-master/1`回退与Xiaozhi WebSocket兼容传输，复用同一Dispatcher和命令生命周期。

### 2026-09-18 / Phase 4B / MQTT只读下行与连接验证

- 版本：`0.4.1`
- Git迭代：`test0.5`，基线为已推送的 `test0.4` / `3d46780a89f2c1955f673ccfbad349b8f342c941`
- 目标：在不移动设备的前提下，从受保护Browser API经Message Bus和Master MQTT Client向单设备精确下发状态/动作目录查询，并验证业务响应而非仅publish成功。
- 修改：Gateway增加只读命令白名单、精确down编码、QoS 0/non-retain publish及结果指标；新增Device Verifier预检、pending correlation、超时/故障中断和逐步报告；Runtime/Web接入`POST /api/v1/devices/{device_id}/verify`。
- 安全：浏览器不能提供Topic或原始MQTT JSON；仅允许`otto_query`与`otto_actions`；响应必须同时匹配ID、内部topic和device target；本轮不发送action/stop。
- 本机验证：`uv lock --check`、Ruff、mypy、前端JS语法均PASS；`pytest -q` 53 PASS。真实Broker中验证EVA1 fake只触达自己的down Topic，两个命令均QoS 0/non-retain，EVA2无串线；EVA2无响应路径按0.2秒失败且pending为0。
- 远程验证：GitHub Actions探针run `35303748098`中macOS job `105471585473`、Windows job `105471585595`均success；两边均完成锁定安装、Ruff、mypy、53个pytest、PyInstaller构建和Broker可执行文件实跑。
- 最终验收：`test0.5` / `8120d6341fc80d35f3ecf68e2320559c10a5604f` 已推送且远程哈希一致。首轮run `35304119667` macOS success、Windows Tests单次failure；本机连续10轮全量测试无复现，相同SHA复验run `35304376265`的macOS job `105473436674`和Windows job `105473436870`均success。
- 返工：首次mypy发现可选响应和动作列表未被布尔别名正确收窄，改为显式`is None`/`isinstance`后类型检查与回归通过；补充错误target和错误ID均不能完成请求。
- 未运行：EVA1/EVA2真机、动作/stop、命令队列、固件改造、TCP/WebSocket兼容、云服务和实体Windows局域网。
- 状态：Phase 4B本地、跨平台探针、最终push/哈希与正式SHA复验全部PASS；完整Phase 4仍进行中。
- 下一步：实现持久命令仓库、每设备有序动作/stop及fake设备完整状态生命周期。

### 2026-09-18 / Phase 4A / MQTT假设备只读联调端

- 版本：`0.4.0`
- Git迭代：`test0.4`，基线为已推送的 `test0.3` / `7fbb368569f162aa135c197438aabfbe861474c4`
- 目标：在不移动真机的前提下打通受保护OTA发放、真实Broker上行、协议翻译、Device Session、SQLite和Web API/事件流。
- 修改：新增Master MQTT Client Gateway；实现hello/heartbeat/state/actions/ACK/error翻译和MAC/Topic校验；实现设备状态机、心跳过期、Gateway故障降级、schema v2设备/动作持久化以及实时设备只读API。
- 安全：仅订阅`otto/v1/devices/+/up`，本检查点没有下行动作发布；所有设备包限制64 KiB、16层、4096节点，拒绝重复字段、未知类型、无效结构和身份冲突；Browser API不返回MQTT凭据。
- 本机验证：`uv lock --check` PASS；`ruff check src tests` PASS；`mypy src` PASS；`pytest -q` 45 PASS。两个独立fake设备经真实aMQTT Broker同时online，动作目录和状态不串线；down订阅超时证明测试期间无动作消息；Gateway关闭后设备立即退出online；Runtime关闭后端口释放；SQLite v1→v2迁移及重启offline恢复通过。
- 远程验证：GitHub Actions探针run `35300950492`中macOS job `105463270409`、Windows job `105463270272`均success；两边均完成锁定安装、Ruff、mypy、45个pytest、PyInstaller构建和Broker可执行文件实跑。
- 返工：首轮Ruff发现Device Manager两处错误类型不符合规范，改为`TypeError`；随后补齐重复hello代次、在线heartbeat持久化、动作外键写入顺序和JSON深度/节点边界并完成受影响回归。
- 未运行：EVA1/EVA2真机、固件改造、MQTT下行查询/动作/stop、命令去重、TCP回退、Xiaozhi WebSocket、实体Windows局域网。
- 状态：Phase 4A本地与跨平台探针门禁PASS；完整Phase 4仍进行中，等待`test0.4`最终push和远程哈希核对。
- 下一步：下一检查点实现fake设备只读下行查询与correlation链，再推进安全动作和兼容传输。

### 2026-09-18 / Phase 3 / 本地网络控制面与Embedded MQTT Broker

- 版本：`0.3.0`
- Git迭代：`test0.3`，基线为已推送的 `test0.2` / `cf2bc419810df69cfa92dea0fa32df613d85fecd`
- 目标：在同一Python进程打通Web/API、状态事件、OTA、mDNS、SQLite查询与安全的MQTT 3.1.1 Broker。
- 修改：新增aMQTT适配与动态每设备凭据/ACL、FastAPI控制面、静态控制台、EventHub snapshot+cursor、OTA manifest/下载/发放、mDNS服务、Runtime网络生命周期和跨平台CI/PyInstaller smoke。
- 安全：匿名MQTT拒绝；用户名、密码和client_id交叉验证；设备只能发布自己的up并订阅自己的down；Browser API无任意Topic入口；非loopback修改、WebSocket与设备发放均失败关闭；响应和事件脱敏。
- 本机验证：`ruff check src tests` PASS；`mypy src` PASS；`pytest -q` 28 PASS；真实HTTP/WebSocket事件推送与端口释放PASS；真实MQTT认证、ACL、往返与关闭PASS；mDNS注册、解析`master.local`和注销PASS；macOS PyInstaller onefile Broker smoke输出`mqtt-broker-smoke:pass`。
- 返工：真实WebSocket关闭测试发现服务端未并行监听disconnect，导致Uvicorn等待心跳并占用端口；改为同时等待客户端帧与事件队列，并增加强制关闭路径后复测通过。
- 远程验证：GitHub Actions run `35299220306`中Windows job `105458103512`、macOS job `105458103740`全部success；两边均完成锁定安装、Ruff、mypy、26个pytest、PyInstaller构建和Broker可执行文件实跑。
- 未运行：ESP32/EVA真机、Device MQTT Gateway和动作闭环属于Phase 4；Windows实体局域网mDNS、防火墙和完整应用包属于Phase 9。
- 状态：Phase 3实现和跨平台门禁PASS；仍不合并main，按规则只提交并push `test0.3`。
- 下一步：完成远程跨平台矩阵与哈希核对后进入`test0.4`假设备/Device Session联调。

### 2026-09-18 / Phase 0 / Otto Master文档脚手架

- 版本：`0.0.0`
- Git迭代：计划使用 `test0.1`，尚未提交
- 目标：在liteserver仓库建立不含业务实现的Otto Master目录和治理文档。
- 修改：配置模板、空模块、宪法、架构、施工计划、进度、模块状态、消息合同和调试指南。
- 验证：目录、Python占位、TOML、YAML和运行产物规则检查通过；未运行应用测试。
- 风险：项目当前不可运行；云Provider和Windows Opus打包尚未验证。
- 下一步：Phase 1 Runtime与Message Bus。

### 2026-09-18 / Phase 0 / 选定DeepSeek LLM Provider

- 版本：`0.0.0`
- Git迭代：计划纳入 `test0.1`，尚未提交
- 目标：记录Otto Master第一套LLM Provider选择，不实现调用代码。
- 修改：配置DeepSeek官方OpenAI兼容Base URL、`deepseek-flash`模型和环境变量名称。
- 安全：聊天中出现的旧密钥未写入任何文件；本地 `.env` 继续保持空值。
- 验证：YAML解析和空密钥检查。
- 下一步：轮换已暴露密钥；Phase 7实现Provider时再做真实API连通测试。

### 2026-09-18 / Phase 0 / 融合MQTT集群控制与真机验收基线

- 版本：`0.0.0`
- Git迭代：计划纳入 `test0.1`，尚未提交
- 目标：把已验证TCP控制行为迁移为MQTT集群控制合同，并纳入后续施工与测试。
- 修改：MQTT内嵌Broker边界、Topic、JSON、QoS、鉴权、TCP回退、施工阶段和EVA1/EVA2真机测试规则。
- 外部基线：用户报告EVA1/EVA2已运行固件 `2.0.5`，位于同一Wi-Fi，均能查询14个动作并完成 `swing → stop → idle`；控制身份不依赖静态IP。
- 代码核对：固件MQTT已支持action/query/actions，但MQTT stop、独立hello/heartbeat和命令去重仍需实现；现有TCP链路继续作为回退。
- 验证：文档与本地固件源码交叉检查；未启动Otto Master、未执行MQTT真机测试、未修改或烧录固件。
- 下一步：Phase 3验证内嵌Broker跨平台可行性；Phase 4使用EVA1/EVA2完成MQTT等价验收。

### 2026-09-18 / Phase 1 / Runtime与Message Bus

- 版本：`0.1.0`
- Git迭代：未创建支线，未commit、未push；将与Phase 0合并纳入 `test0.1` 恢复基线
- 目标：完成配置加载、消息合同、进程内Message Bus和Runtime生命周期。
- 修改：`config.py`、`messages.py`、`message_bus.py`、`runtime.py`、`structured_logging.py`、包入口及Phase 1测试。
- 验证：`ruff check src tests`通过；`mypy src`通过；`pytest -q`为10通过；`python -m otto_master`启动并通过Ctrl-C干净关闭。
- 未运行：真实云API、ESP32硬件、Web、SQLite和跨平台Windows验证；这些不属于Phase 1。
- 风险：Message Bus当前只支持精确主题，持久化和外部Gateway尚未实现。
- 下一步：先建立 `test0.1` Phase 0+1恢复基线并上传GitHub，再进入Phase 2。

### 2026-09-18 / 治理 / 强化强制施工门禁

- 版本：`0.1.0`
- Git迭代：文档变更纳入待建立的 `test0.1` 恢复基线；本轮未擅自提交现有来源混合的工作区。
- 目标：把“先备份、再计划、施工、测试返工、日志、上传”的流程从建议升级为不可跳过的施工门禁。
- 修改：新增并贯通GitHub基线、施工依据阅读、Session Contract、二元验收矩阵、测试返工闭环、文档收尾、测试支线提交与远程哈希核对规则。
- 授权边界：创建 `testN.N`、验收commit和push测试支线随施工请求自动授权；合并main、tag、正式发布、force-push、删远端分支和改写历史必须单独授权。
- 历史处理：Phase 0和Phase 1均未形成远程检查点，不伪造历史；下一步用 `test0.1` 建立包含两阶段的恢复基线，Phase 2再使用 `test0.2`。
- 验证：文档一致性和关键规则文本检查；未修改Python业务代码，未运行应用测试或硬件测试。
- 风险：当前Phase 0+1仍未备份到GitHub，在恢复基线push并核对哈希前禁止Phase 2施工。
- 下一步：执行 `test0.1` 恢复基线门禁。

### 2026-09-18 / Phase 2 / SQLite持久化与迁移

- 版本：`0.2.0`
- Git迭代：`test0.2`，基线为已推送的 `test0.1` / `e0097c4649322175356487f13d73fde078afdd09`
- 目标：建立SQLite连接生命周期、版本化迁移、消息日志脱敏和Runtime关闭刷盘。
- 修改：`storage/database.py`、`storage/migrations.py`、`storage/__init__.py`、`message_bus.py`、`runtime.py`、Phase 2测试、版本与施工记录。
- 验证：首次建库schema version=1；重复迁移通过；100路并发消息写入通过；敏感字段和原始音频字段脱敏；Runtime关闭后12条在途消息全部落盘；全量 `pytest -q` 为16通过；Ruff和mypy strict通过；默认入口生成 `data/otto.db` 并输出数据库关闭日志。
- 返工：首次静态检查发现storage导入排序和`__all__`排序问题，按ruff提示修正后重跑通过；无功能测试失败。
- 未运行：真实云API、ESP32硬件、Web、MQTT、Windows和跨平台打包；这些不属于Phase 2。
- 风险：设备、Web和外部Gateway仍未实现；SQLite目前由单进程单连接控制，后续多进程部署不在MVP范围。
- 下一步：在 `test0.2` 远程检查点基础上创建 `test0.3`，进入Phase 3。

### 2026-09-18 / 需求 / 冻结打包前Server控制台P0范围

- 版本：`0.1.0`
- Git迭代：编写时 `test0.1` 恢复基线已建立，工作区处于进行中的 `test0.2` Phase 2；本需求文档尚未commit或push，避免把未验收的Phase 2代码一并提交。
- 目标：列清合格MQTT Server控制台在打包前必须完成的前端、设备接入、连接验证、动作控制和跨平台验收要求。
- 修改：新增控制台信息架构、设备状态机、只读与动作两级验证、Browser API、安全边界、命令闭环、事件恢复、OTA、P0测试矩阵和打包准入定义。
- 关键决定：浏览器只连接Python Web Gateway，不持有MQTT凭据或直接发布Topic；publish和ACK不等于动作完成，必须跟踪到moving和idle。
- 验证：文档存在性、关键需求和交叉引用检查；未修改前后端业务代码，未运行应用或真机测试。
- 风险：控制台、MQTT Gateway和Browser API仍未实现，本记录不能作为功能通过证据。
- 下一步：先完成并验收当前Phase 2，再按后续新支线和Phase 3、Phase 4实现控制台P0能力；合并main仍需单独授权。

### 2026-09-18 / Phase 0+1 / 恢复基线 test0.1

- 版本：`0.1.0`
- Git迭代：`test0.1`
- 目标：将此前未形成Git检查点的Phase 0/Phase 1工程备份到GitHub，作为Phase 2的可恢复基线。
- 修改：新增 `docs/sessions/20260918-phase0-test0.1.md`，并更新当前进度；精确提交 `otto-master/**`。
- 排除：根目录已有 `README.md` 修改，以及 `.env`、虚拟环境、数据库、JSONL日志和固件。
- 验证：结果回填到Session Contract；远程支线哈希在push后核对。
- 下一步：在 `test0.1` 远程检查点基础上创建 `test0.2`，进入Phase 2 SQLite。

### 2026-09-18 / test0.9 / 流式语音MVP实现与真机暂停点

- 实现：无numpy/FFmpeg的Opus编解码；火山流式ASR/TTS与DeepSeek SSE；按设备ASR准入、句子切分、有序TTS、WakeGate大笑忙闲边沿门禁；WebSocket在`listen/start`前安全丢弃小智唤醒词预录帧。
- 验证：Ruff、mypy通过；预录帧修复前全量114项通过，修复后WebSocket真连接定向测试通过；一次真实火山TTS→Opus→ASR和DeepSeek流式烟测成功，后续DeepSeek瞬时失败被安全归一化。
- 固件：加入认证可逆的MQTT/WebSocket Profile选择、正确WebSocket身份/能力hello及带令牌的`voice_wake`验收入口。EVA1已确认2.0.7和WebSocket首选配置；EVA2保持2.0.6 MQTT。
- 未通过：Mac播放唤醒词没有触发EVA1；没有观察到真实大笑，也没有完成真机ASR→LLM→TTS闭环。
- 叫停状态：2.0.8镜像编译成功（SHA-256 `60ef5c7748767266c4470d6144dfa1b79a54b77cf512d0c22f64f8ecb719fd4d`）且OTA已下发EVA1，但回连版本未确认。按用户要求停止跑测；恢复时第一步必须只读查版本，不得重复OTA。
- Git：尚未提交或push；根目录用户`README.md`继续排除。

### 2026-09-19 / test0.9 / 笑声不卡、循环对话与8秒退出加固

- 根因：本地OGG与云端TTS共用固件解码/播放队列；旧顺序先发`tts start`触发`ResetDecoder`再播本地笑声，会清空或打断笑声。旧笑声还是20 ms Opus包，而当前输出链以60 ms为基线。另一个真机问题是无舵机`laugh`瞬间回idle，Dispatcher看不到`moving → idle`，会占住命令15秒并取消下一轮笑声。
- 固件：笑声转为约2.0065秒、24 kHz OpusHead、单声道、34个60 ms包；`PlaySound`等待压缩队列、解码、PCM队列和I2S写入全部排空。`laugh`保持moving直到音频结束，按钮改为idle进入循环、对话态发送goodbye退出，并增加VAD上报与WebSocket关闭消息。
- Server：WakeGate变为`waiting → laughing → listening → recognizing → answering → laughing...`；每轮都必须观测`sound.busy true→false`。当时实现由当前轮VAD/partial取消8秒静默计时；后续`test1.0`真机证明VAD会受噪声影响，现已收窄为仅非空partial。超时由Server关闭，按钮goodbye正常退出；不同新session可从失败状态恢复。
- 真机：EVA1经局域网OTA升级至2.0.11。真实回合识别“没事。”并播放回答后再次大笑、进入下一轮；设备goodbye退出和新session重入均出现。2.0.11连续两个独立笑声门禁均出现`moving/laugh/busy=true → idle/false`，第二次约2.1秒完成；另一次监听8秒后以`idle_timeout`退出，UDP sessions=0、EVA1 idle。
- 工具边界：DeepSeek当前是纯文本SSE，没有请求`tools/tool_choice`，也不消费`tool_calls`；“大笑、前进、后退”等自然语言不会调用Dispatcher。提示词中的`self.otto.*`仍只是合同，下一步必须实现真实工具桥和Schema校验。
- 验证：ESP-IDF 5.5.5完整构建2.0.11，应用镜像3,788,720字节，SHA256 `4c4298363b621599ea11c8daba2dc3efbc644538666a9f10f7115ece49e200bf`；Otto Master `127 passed`，Ruff、mypy strict（36个源码文件）、Node语法、锁文件和两仓`git diff --check`通过。
- 运行态：正式Runtime固定在HTTP 8081，与诊断/OTA服务8080并存；EVA1 2.0.11 MQTT online/idle，EVA2关机。未提交、未push、未运行正式CI；按用户要求本轮文档整理后停止。

### 2026-09-19 / test0.9 / DeepSeek真实工具桥与EVA1动作闭环

- 架构：对照原小智的“MCP tools/list → LLM functions → tools/call”流程，在Otto Master复用现有设备动作目录和Dispatcher，不复制第二套设备控制协议。DeepSeek名使用`self_otto_*`，Server固定当前语音设备，模型不能提供MAC、Topic、transport或广播目标。
- 实现：Cloud Gateway支持`tools/tool_choice`和流式tool delta；LLM Service以有界队列组装至多一个调用并严格校验；新增RobotToolBridge收窄步数/速度/幅度/方向、排除home/校准，等待持久命令终态；WakeGate实现文本/TTS与工具分流、成功不复述、失败固定提示和显式笑声门禁复用。
- 缺陷与修复：首轮实现把工具结果写成普通assistant“已执行”文本，导致同一LlmService下一轮后退请求不再稳定选择工具。两次安全验收各完成前进一步后，后退均在下发前被唯一工具门禁拦截。修复为工具轮不进入普通文本历史，并新增连续工具回归；随后真实“大笑→后退两步”均completed，补偿前两步并最终只读确认EVA1 idle。
- 真API/真机：无设备DeepSeek烟测返回`self_otto_laugh {}`；EVA1只读verify后完成`self_otto_laugh → laugh → completed`。修复后连续第二轮返回`self_otto_walk_forward {direction:-1,steps:2}`，Dispatcher等待真实completed。EVA2保持关机且未触碰。
- 验证：`uv lock --check`、Ruff、mypy strict（37个源码文件）和全量`144 passed`。常驻Runtime已恢复到HTTP 8081/MQTT 1883/UDP 8884并健康，诊断8080保留；未commit、未push、未运行正式CI。
- 暂停点：代码工具链已完成。下一轮先让用户通过EVA1按钮和真实麦克风口述大笑/前进/后退，确认上屏、无成功复述和循环门禁，再在EVA2开机后补双设备隔离。
- 固件恢复点：2.0.11源码已通过ESP-IDF 5.5.5复建并推送`howtion0/otto`的`codex/otto-portable@abb769f1d0f3d1c03fb7a6106bd7288f31c66a98`；镜像SHA256保持`4c4298363b621599ea11c8daba2dc3efbc644538666a9f10f7115ece49e200bf`。

### 2026-09-19 / test1.0 / 多设备WebUI、看山表情与对话音量加固

- 基线：`test0.9@a2c22fb144beece1676625c39deee2b7d223d9df`已与远端一致，GitHub Actions run `35384613680`的macOS/Windows jobs均成功；本轮创建`test1.0`，版本提升至0.5.0。
- Web Gateway：新增1至16个显式device_id的批量动作与批量stop，跨设备并发、同设备仍串行，逐设备返回独立结果；空目标、重复目标、通配符、名称/IP和未确认请求均拒绝。新增按设备/session隔离的脱敏对话投影和`GET /api/v1/conversations`。
- WebUI：新增设备多选、在线选择/清空、动作目录交集、前进/后退/转向/跳跃/摇摆/太空步/抖动/弯腰/大笑/复位快捷按钮、高级批量动作/stop确认、正式对话start/stop、Server运行状态和独立对话泳道；刷新或事件重连后从Server快照恢复。动作目录为空时自动执行受保护verify；本机新标签页可用仅限loopback且不进入HTTP的fragment装入控制令牌并立即清除地址栏。
- WebUI入口加固：用户后续点击前进时，正式8081的命令表、Message Bus和MQTT均无新记录，EVA1同时保持online/idle，故边界确定在浏览器请求之前。本机仍有昨日下午遗留的8080诊断进程，且只读页面可见时旧UI没有持续区分控制授权，容易误判“已发送”。已精确停止该旧进程，只保留8081；新UI增加控制授权徽标、无/失效令牌时锁定mutation控件、401聚焦令牌框和常驻提交结果。
- WebUI修复后真机复验：用户在重新授权的8081页面连续执行前进、抖动、弯腰和大笑，服务端逐条记录`webui:batch → accepted → moving → completed`，最终idle；显示遥测分别出现`action_walk`、`action_jitter`、`action_bend`、`action_laugh`并恢复`base_emotion`。随后三步`swing`在15.254秒触发`moving_or_idle_timeout`和安全stop，暴露生产完成门限短于合法动作；现提高到30秒，设备ACK门限仍为3秒。
- 对话修复：真实DeepSeek曾先输出完整句子再返回工具，旧逻辑以`mixed_text_and_tool_call`失败；现改为未成句前缀丢弃，已提交完整句子先完成TTS并stop，再串行执行唯一工具。另一次笑声状态查询瞬时超时现会在总门限内重试；始终没有完整busy边沿仍失败关闭。
- 音量：TTS Service在Opus编码前对24 kHz S16LE PCM应用可配置饱和增益并正确处理跨Provider块的半个采样。首轮1.5倍短句烟测为113,398 PCM字节、峰值30,365→32,768、RMS 4,338.5→6,412.2、饱和样本约0.32%；用户仍反馈偏小，最终生产配置改为2.0。
- 固件：中央旧大眼GIF替换为21个看山对话表情与22个动作贴图描述符；顶部状态栏及底部ASR/回答文字不变，动作开始覆盖中央图，结束/stop恢复最近基础表情。动作任务优先级降至3，持久输出音量迁移至100。
- ASR加固：03:45左右顺滑回合均在1.1至3.1秒收到火山partial；05:21故障轮已上传491个60 ms帧且VAD正常，却没有任何partial，旧逻辑因此等到30秒超时。首轮先增加说话后VAD静音1.2秒兜底并在真机1.202秒收句；后续又发现共享计时器会被VAD抖动覆盖，最终改为partial/VAD/12秒硬上限三个独立端点并保留队列尾帧。空final从一次恢复扩展为连续第二次正常退出，真机恢复已通过。
- 动作加固：Dispatcher把`sound_busy=true`纳入准入和完成判据，舵机先idle而本地OGG未排空时命令保持moving。WakeGate开场笑声若被`device_state_unsafe`拒绝，不再只查询一次或在idle+busy时空转，而是在有界截止时间内反复查询到action idle且sound非busy再提交。EVA1最短walk经正式批量API在91 ms收到ACK、5.31秒completed；随后用户从新WebUI真实完成两次3步前进、一次左转和一次太空步，同设备重叠点击保持串行。
- 正式对话控制：固件2.0.15对`otto_conversation`先回关联ACK再异步切换可能阻塞的音频通道；EVA1真实start约104 ms接受并建立MQTT/UDP session，stop约19 ms接受并回waiting。
- 构建与OTA：ESP-IDF 5.5.5完整构建2.0.15，镜像3,830,880字节，SHA256 `e1ca9051c8f6a2aac1bef3e47323c1927ef6bebc3b901ae52bc393f9ad4595e6`。EVA1 OTA后hello确认2.0.15，运行态确认音量100；Server健康状态确认TTS增益2.0、EVA1 MQTT online。
- 固件提交：上述2.0.15源码已精确提交并推送到`howtion0/otto`的`codex/otto-portable@c6addc6a35bf54c6c28f07fde53828cd73bce1f0`；远端SHA复核一致，未推main、未创建tag。
- 真机：正式Web控制已完成多种动作和对话start/stop，最终idle；EVA1后续完成五轮问答、语音动作、空识别熔断和8秒静默退出，用户确认当前“对话感觉没问题了”，2.0倍TTS与整体流畅度主观PASS。EVA2已重新在线但本轮未触碰，没有把在线状态或fake双设备并发冒充真机双设备通过。动作图切换/恢复仍待用户目视确认。
- 自动验证：首轮全量测试暴露旧`AudioConfig`直接构造缺省值和本机控制台令牌污染Runtime WebSocket测试，分别用安全unity回退和显式测试环境隔离修复；后续新增ASR三端点/原子清理、连续空final、VAD-only静默、音效排空和WebUI授权回归。最终`uv lock --check`、Ruff、mypy strict（38个源码文件）、183项pytest、Node语法和`git diff --check`通过；Windows测试同步边界随后收口，最终run `35403562279`双平台及打包smoke全部PASS。
- 运行链路：EVA1继续使用“MQTT控制/信令 + AES-128-CTR UDP Opus → 本机Otto Master → 火山ASR/TTS + DeepSeek”，不是官方小智云后端。
- 排除：根目录用户`README.md`、`.DS_Store`、贴图源目录与ZIP、`.env`、数据库、日志、构建输出和固件二进制不进入提交；密钥未写入文档或Git差异。

### 2026-09-19 / test1.0 / EVA1对话端点、空识别与静默退出收口

- 排除账户原因：DeepSeek余额接口仍返回可用，火山ASR与TTS在同轮真实调用均成功；卡顿轮的直接错误是ASR等待30.048秒超时，不是已证实的欠费或Key失效。
- 根因：旧ASR只有一个共享endpoint task，VAD true/false会不断取消或覆盖已经开始的partial稳定计时；设备VAD又会受房间噪声和扬声器尾音影响。空final恢复没有连续次数上限，ASR取消还可能在任务完成前暂留活动utterance。
- Server修复：partial稳定、说话后VAD静音与12秒硬上限改为三个独立一次性端点；VAD true只取消VAD静音计时，获胜路径排空尾帧并取消其余计时器。关闭先原子摘除活动utterance，迟到帧分为unmatched/stale/post-endpoint。配置强制`max_utterance + speech_grace < cloud_timeout`。
- WakeGate修复：仅当前轮非空ASR partial可以取消8秒无讲话计时；VAD保留为ASR端点提示但不再给对话续命。首次空final只笑一次并重开，连续第二次以`empty_transcription_limit`正常关闭，只有有效非空final清零连续空识别计数；Runtime状态公开相关计数但不公开音频或Provider敏感字段。
- EVA1真机：一轮连续两次空final后只发生一次恢复笑声，随后回waiting且无第三次笑声/failed；另一轮完成五次有效问答，均由`partial_stability`收句，端点约3.309至8.304秒、final约0.078至0.196秒，五次TTS全部完成。“奶酪前进”正确进入`self_otto_walk_forward`并由Dispatcher完成动作，按钮`device_goodbye`退出正常。
- 静默门禁：独立会话中设备继续产生9次VAD-only事件，WakeGate仍在开放监听后约8秒以`idle_timeout`退出；最终EVA1 waiting/idle，活动ASR与UDP session均为0。EVA2虽重新在线但本轮未向其下发语音或动作命令。
- 用户验收：在上述修复后的EVA1链路上，用户确认“对话感觉没问题了”；当前对话流畅度、设备音量100与服务端2.0倍TTS听感由待确认改为主观PASS，未将该结论外推到动作贴图或EVA2。
- 自动验证：锁文件、Ruff、mypy strict（38个源码文件）、Node语法、`git diff --check`和全量183项pytest通过。`b4a694b`的run `35400612369`在Windows暴露10 ms笑声重试测试时限；`1c10865`的run `35403162431`又暴露waiting后异步close断言抢跑。前者改为0.1/1秒，后者显式等待会话关闭事实，生产配置和状态机均未放宽；本地30轮Windows敏感用例通过。收口提交`51a1b48`对应run `35403562279`的macOS/Windows Tests、原生Opus加载和打包smoke全部PASS。

### 2026-09-19 / test1.0 / 同名Wi-Fi换网与mDNS自动刷新

- 现场：开发机从`192.168.172.225`切到`192.168.122.225`，EVA1/EVA2已分别取得新网段`.127/.117`且可达，但旧Runtime继续广播旧地址。EVA2在Server重启修正记录后自动MQTT回连；EVA1可ping、可通过8765显式mDNS hello上报当前名字/IP，却没有正式MQTT连接。
- 双层根因：Server `MdnsGateway`只在进程构造时获取一次IPv4；设备`OttoMasterLink`虽每次重试都显式查mDNS，正式`MqttProtocol`却没有复用结果，而是把`.local`名称继续交给ESP MQTT/路由器DNS。
- Server修复：新增5秒地址监视、Zeroconf原位更新、切换瞬间回环保护、更新失败保留旧记录/自动重试；关闭先停止监视再注销最新记录。健康接口和WebUI组件状态可见当前地址、刷新次数、失败次数与监视任务。
- 固件修复：2.0.16在正式MQTT首次连接和每次重连前调用显式mDNS解析，只把当次IPv4交给Client，不写NVS。ESP-IDF 5.5.5完整构建3,831,136字节镜像，SHA256 `03377107cb829ce741dd5239d6d87c0d207b0f8a889532c45b953d9ae44ba9a1`。
- 真机：EVA1经本地升级链OTA到2.0.16后，无需固定IP或重新配网，以`192.168.122.127`发出正式MQTT hello并持续heartbeat；EVA2随后经串口完整烧录升级到2.0.16，保留Wi-Fi/名称/凭据并以`.117`上线，屏幕确认`EVA2/2.0.16`。设备首选transport与Server凭据幂等核对均正常，密钥未输出或写入文档。
- 重启与动作门禁：Runtime重启后EVA1约0.37秒、当时仍为2.0.9的EVA2约9.79秒自动hello，机器人均未重启或重配。受保护verify全项通过后，正式批量API只向EVA1提交一步walk；命令`df274527-df9e-4e40-b1b3-23ccaf6b790c`完整经过requested/published/accepted/moving/completed，5.238秒完成，最终EVA1 idle、sound false、显示恢复base_emotion，EVA2未被选中。
- EVA2扩展：首次OTA下载和`upgrade_started`不能证明切换，重启探针仍为2.0.9后改用串口烧录；2.0.16应用SHA256为`b8d4e7323b5a0d4d3486373bb9c2a0a0fd345a1d4bf9bc88565595d387b1cc1a`。verify 98.511 ms通过，按钮会话`faa4eea4-b809-4e34-b835-0b221fcbbd5c`完成笑声、转写、奶龙回复/TTS和再次监听；该单会话不替代并发语音验收。
- EVA3扩展：串口MAC复核为`288485478f34`，2.0.16应用SHA256为`50f159e5646f47045027b94878527a728e2fab03b0333761710a0a46472652a8`。USB重枚举使写入工具末尾返回非零码，随后以资源校验、完整启动、屏幕`EVA3/2.0.16`、`.59` hello和正式verify 171.521 ms共同确认成功；受保护发放只命中EVA3，临时密钥文件已删除。
- 阵列门禁：EVA1/EVA2 batch `array-eva1-eva2-20260919-01`请求2、接受2、失败0。三机batch `array-eva1-eva2-eva3-20260919-01`请求3、接受3、失败0；命令`c0ea38d2-18ca-4935-95c7-fd96b75e3875`、`aa933b9f-1565-421a-ac94-e1d84d89777d`、`1856da68-5a84-428a-b71d-50d4b252108c`分别在5.222、6.229、6.221秒completed，最终三台online/idle，Gateway无拒绝/发布失败。
- 固件Git：2.0.16源码已推送`howtion0/otto codex/otto-portable@c4ad28e45adb5f565469d4c14b52aedcb74c1ffb`，本地与远端SHA一致。
- 自动验证：全量187项pytest、Ruff、mypy strict（38个源码文件）、Node语法、锁文件和差异检查通过；Server测试覆盖地址变化、回环保护及失败后恢复，固件完整构建通过。当前Runtime广播`192.168.122.225`且监视健康。
- 待完成：精确提交并push Server mDNS修复与文档，核验macOS/Windows CI；多设备并发语音/工具、WebSocket真机和实体Windows留到下一轮。
