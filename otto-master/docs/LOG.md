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
