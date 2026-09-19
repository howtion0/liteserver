# Development Progress

## 当前版本

版本以 `pyproject.toml` 为准，当前为 `0.5.0`。

## Git迭代

- Phase 0+1恢复基线 `test0.1` 已推送，远程哈希为 `e0097c4649322175356487f13d73fde078afdd09`。
- Phase 2支线 `test0.2` 已推送，远程哈希为 `cf2bc419810df69cfa92dea0fa32df613d85fecd`。
- Phase 3支线 `test0.3` 已推送，远程哈希为 `7fbb368569f162aa135c197438aabfbe861474c4`；最终run `35299643584`的macOS/Windows jobs均PASS。
- Phase 4A本轮支线为 `test0.4`，明确从已验收的 `test0.3` 继续；跨平台探针run `35300950492`的macOS/Windows jobs均PASS。
- Phase 4A最终支线 `test0.4` 已推送，远程哈希为 `3d46780a89f2c1955f673ccfbad349b8f342c941`；最终run `35301297710`的macOS/Windows jobs均PASS。
- Phase 4B本轮支线为 `test0.5`，从已验收的 `test0.4` 继续；跨平台探针run `35303748098`的macOS/Windows jobs均PASS。
- Phase 4B最终支线 `test0.5` 已推送，远程哈希为 `8120d6341fc80d35f3ecf68e2320559c10a5604f`；首轮run `35304119667` Windows发生一次性Tests失败，相同正式SHA复验run `35304376265`的macOS/Windows jobs均PASS。
- Phase 4C最终支线 `test0.6` 已推送，远程哈希为 `f359a1fc3a50360fbcb2de42cced2f48eeb6c164`；探针run `35306214077`和正式run `35306518034`的macOS/Windows jobs均PASS。
- Phase 4D最终支线 `test0.7` 已推送，远程哈希为 `2b22a724d7169c2f84c5e028b95f40de7c0c4964`；正式run `35309478483`的macOS/Windows jobs均PASS。
- Phase 4E真机支线为 `test0.8`，从已验收的 `test0.7` 继续；两台EVA控制、锁定改配拒绝、重复ID和Server/Broker重启恢复均已通过，后续已被`test0.9/test1.0`的正式CI覆盖。
- Phase 5纵向MVP支线`test0.9`已推送，远程哈希为`a2c22fb144beece1676625c39deee2b7d223d9df`；GitHub Actions run `35384613680`的macOS/Windows jobs均PASS。
- `test1.0`已从上述基线完成多设备WebUI、对话/工具加固、TTS音量和看山表情。首个提交`b4a694b1c5b6bf39bdb4cb4288b42cfc27268876`的run `35400612369`暴露Windows测试时限；对话修复提交`1c10865b96bf87c70732d5e7000db09357d3b6dc`的run `35403162431`又暴露测试异步close断言抢跑。两处测试同步修复收口于`51a1b48142b3193d9e0a10545d19f0b61c1a21f6`，最终run `35403562279`的macOS/Windows Tests、原生Opus加载与打包smoke全部PASS。
- 同一支线继续处理同名Wi-Fi换网故障：Server从`192.168.172.225`切到`192.168.122.225`后，旧mDNS实现仍广播启动时地址；固件本地8765链虽显式查询mDNS，正式MQTT此前却仍把`.local`交给底层DNS。Server现已加入5秒IPv4监视、原位更新、瞬时回环保护和失败重试；EVA固件2.0.16让正式MQTT首次连接/每次重连复用显式解析。本地门禁、EVA1换网回连、EVA2/EVA3升级、三机同批动作和run `35408550039`跨平台CI均通过。
- 配套EVA固件2.0.15源码已推送到`howtion0/otto`的`codex/otto-portable`，远端SHA为`c6addc6a35bf54c6c28f07fde53828cd73bce1f0`；2.0.11恢复点仍为`abb769f1d0f3d1c03fb7a6106bd7288f31c66a98`。
- EVA固件2.0.16已完整构建、OTA到EVA1并推送`howtion0/otto`的`codex/otto-portable@c4ad28e45adb5f565469d4c14b52aedcb74c1ffb`；随后EVA2和EVA3分别以保底名称完整串口烧录并核对屏显/稳定MAC。EVA2/EVA3应用SHA256分别为`b8d4e7323b5a0d4d3486373bb9c2a0a0fd345a1d4bf9bc88565595d387b1cc1a`和`50f159e5646f47045027b94878527a728e2fab03b0333761710a0a46472652a8`；源码仍是同一远端SHA，2.0.15的`c6addc6`继续作为回滚点。
- 当前工作支线为`test1.1`，从已验收的`test1.0@18181851401e8ebef516c71d847b76e924c27f26`继续：以Forge电台为前端主版本建立独立`webui/`源码，以Otto Master为后端主版本接入知乎官方只读API，并把完整静态快照纳入Python包和跨平台PyInstaller门禁。实现与本地门禁已完成，远程提交、push和本轮macOS/Windows CI待收口。
- 此后每个阶段或补充检查点使用下一个 `testN.N` 编号。
- 支线编号与产品版本分别记录，互不驱动。

## 当前状态

| 项目 | 状态 |
|---|---|
| Phase 0 文档脚手架 | 已完成 |
| Phase 1 Runtime与Message Bus | 已完成 |
| Phase 2 SQLite | 已完成 |
| Phase 3 Web/OTA/mDNS/Embedded MQTT Broker | 已完成；`test1.0`补充换网自动刷新加固，本地门禁通过 |
| Phase 4 MQTT控制/TCP回退/WebSocket兼容 | 进行中；Phase 4A-4D软件门禁、Phase 4E双机基线和`test1.0`三台2.0.16正式MQTT批量控制均通过；mDNS提交run `35408550039`跨平台CI通过，WebSocket真机Profile仍待验收 |
| Phase 5 Opus/ASR/TTS | 进行中；火山ASR/TTS、Opus、MQTT加密UDP、独立partial/VAD/12秒硬上限三端点、2.0倍饱和增益和EVA1真实闭环已实现，EVA2单会话烟测成功；多设备并发语音、WebSocket真机与Windows实体矩阵未完成 |
| Phase 6 WakeGate | 进行中；每轮2秒笑声门禁、循环问答、首次空final笑声恢复/连续第二次退出、仅非空partial取消的8秒静默、按钮/正式Web控制进出、瞬时状态查询重试和失败自恢复已实现 |
| Phase 7 LLM/Dispatcher/动作 | 部分完成；DeepSeek流式`tools/tool_calls`、当前语音设备Schema、参数校验、TTS后串行工具和Dispatcher桥已实现；动作完成会等待本地音效排空，持久设备组与双机语音工具隔离未完成 |
| Phase 8 集群/日志/容错 | 部分完成；Forge 3D WebUI已接入真实健康、设备、动作目录交集、批量动作/stop、正式对话、脱敏对话投影和知乎只读查询；长期运行与完整集群策略未完成 |
| Phase 9 Windows打包 | 进行中；前端静态快照已递归纳入Python package data，本机PyInstaller onefile静态资源实跑通过，macOS/Windows远程矩阵待本轮push后确认，实体Windows局域网仍未验收 |
| Python业务实现 | Phase 1-4基座及Cloud/ASR/LLM/TTS、RobotToolBridge、DeviceAudioRouter、MQTT UDP、循环WakeGate、多设备Web控制台和知乎官方只读Gateway/Service纵向链已实现 |
| 自动测试 | 本轮203通过；Ruff、mypy strict（41个源码文件）、TypeScript/Vite干净构建、npm审计、锁文件、静态资源package/PyInstaller smoke和Runtime HTTP闭环通过；`test1.1`远程CI待push后确认 |
| 硬件验证 | EVA1/EVA2/EVA3均运行2.0.16并分别以`.127/.117/.59`通过`master.local`正式MQTT在线；三机batch请求3、接受3、失败0，分别在5.222/6.229/6.221秒完成并回idle。EVA2另完成单会话MQTT+UDP问答烟测；实体Windows与多设备并发语音仍待门禁 |

## 已完成

- 在 `liteserver/otto-master/` 建立标准Python包目录。
- 建立非敏感 `config.yaml` 和本地 `.env` 模板。
- 建立Runtime、Message Bus、Gateway、Device、Service、Dispatcher、Audio和Storage模块骨架。
- 建立WebUI、数据、固件、日志和测试占位目录。
- 将参考ZIP的宪法、架构、施工、进度和日志方法改写为Otto Master版本。
- 将“Event Bus”统一为“Message Bus”。
- 明确macOS开发、Windows部署和单Python进程边界。
- LLM Provider已选定为DeepSeek官方OpenAI兼容接口；密钥仍只从本地环境变量读取。
- ASR/TTS Provider已选定为火山引擎豆包语音：ASR 1.0时长版双向WebSocket与TTS 2.0流式HTTP已接入Otto Master，并完成EVA1 MQTT+UDP真机闭环；跨平台与第二台设备验收仍未完成。
- TTS烟测得到24 kHz单声道PCM并用`opuslib-next`编码为93个60 ms Opus帧，首帧解码通过；ASR对同一音频返回精确最终文本。详细证据和接入合同见`docs/VOLCENGINE_SPEECH_INTEGRATION.md`。
- 已将MQTT确定为目标集群控制通道：同一Python进程内嵌Broker，TCP作为迁移回退，WebSocket保留兼容。
- 已记录用户提供的固件 `2.0.5` 真机基线：EVA1/EVA2同网段在线、各14动作、`swing`与`stop`后回到idle；该结果来自当前TCP控制链路，不计为MQTT验收通过。
- 已冻结打包前Server控制台P0需求：系统与Broker健康、设备接入、只读连接验证、安全动作验证、命令生命周期、事件恢复、安全、OTA和Windows冒烟标准。
- Phase 1已实现YAML配置加载、dotenv/环境变量替换、消息合同校验、精确主题Message Bus、Runtime优雅关闭、统一线程池和JSONL结构化日志。
- Phase 1已通过 `ruff check src tests`、`mypy src`、10个pytest单元测试，以及 `python -m otto_master` 启动/中断退出冒烟测试。
- Phase 2已实现aiosqlite连接生命周期、schema migration、设备/分组/消息/命令/结果/设置表、消息observer、敏感字段脱敏、payload大小限制和单写者事务入口。
- Phase 2已通过首次建库、重复迁移、100路并发消息写入、脱敏、Runtime关闭刷盘和全量16个pytest测试；默认入口实际生成 `data/otto.db` 并正常关闭。
- Phase 3已实现FastAPI静态控制台、稳定错误与correlation ID、组件健康、设备/命令API骨架、设置白名单、OTA manifest/下载/受保护发放、事件snapshot+cursor恢复和慢客户端隔离。
- Phase 3已实现同进程aMQTT 0.12.1 Broker、自有凭据存储、禁止匿名、client_id绑定与Master/每设备最小Topic ACL；本机真实MQTT往返和跨设备拒绝通过。
- Phase 3已实现`master.local`服务记录注册/解析/注销；本机广播解析到实际局域网地址，Runtime真实HTTP/WebSocket启动、事件推送、反向关闭及端口释放通过。
- macOS PyInstaller onefile Broker smoke已构建并运行，输出`mqtt-broker-smoke:pass`。
- GitHub Actions run `35299220306`：Windows job `105458103512`、macOS job `105458103740`均通过锁定安装、Ruff、mypy、26个测试、PyInstaller构建和对应平台可执行文件实跑。
- Phase 3最终GitHub Actions run `35299643584`：macOS job `105459377060`、Windows job `105459377204`均通过锁定安装、Ruff、mypy、28个测试和PyInstaller Broker smoke。
- Phase 4A已实现Master MQTT Client Gateway，只订阅设备up通配Topic；外部JSON经过64 KiB、深度、节点数、重复字段、消息类型和MAC/Topic身份校验后才进入Message Bus。
- Phase 4A已实现Device Manager、稳定MAC Session、`connecting → online → stale → offline`状态、Gateway不可用降级、SQLite schema v2设备快照与动作目录恢复。
- Phase 4A已把Web设备列表、详情和动作目录切到实时Manager快照；真实内嵌Broker测试中两个受保护OTA发放的fake设备同时上报hello、heartbeat、state和不同动作目录，状态和目录未串设备，且未收到任何动作下行。
- Phase 4A本机通过`uv lock --check`、Ruff、mypy strict和45个pytest；Runtime关闭后HTTP/MQTT端口释放，v1数据库迁移与重启离线恢复通过。
- Phase 4A GitHub Actions探针run `35300950492`：macOS job `105463270409`、Windows job `105463270272`均通过锁定安装、Ruff、mypy、45个测试、PyInstaller构建和Broker可执行文件实跑。
- Phase 4A最终GitHub Actions run `35301297710`：macOS job `105464305839`、Windows job `105464305626`均success，最终SHA与远程分支一致。
- Phase 4B已实现内部只读查询白名单、精确单设备down Topic编码、QoS 0/non-retain发布、发布成功/失败事件和出站指标；Browser API不能注入Topic或原始JSON。
- Phase 4B已实现Device Verifier：检查Broker/Gateway、online Session、实际传输、心跳和能力后，依次发送状态/动作目录查询，同时按ID、响应topic和device target关联，支持超时、断线与关闭中断。
- `POST /api/v1/devices/{device_id}/verify`已接入受保护控制面，返回逐步PASS/FAIL、命令ID、延迟、动作数量和失败原因；真实Broker双fake测试验证无跨设备下行。
- Phase 4B本机通过锁文件、Ruff、mypy strict、53个pytest和前端语法检查；无响应验证按配置超时且pending清零。
- Phase 4B GitHub Actions探针run `35303748098`：macOS job `105471585473`、Windows job `105471585595`均通过锁定安装、Ruff、mypy、53个测试、PyInstaller构建和Broker可执行文件实跑。
- Phase 4B最终SHA `8120d6341fc80d35f3ecf68e2320559c10a5604f` 在复验run `35304376265`中双平台通过：macOS job `105473436674`、Windows job `105473436870`。首轮Windows Tests单次失败后，本机连续10轮全量测试与同SHA复验均无复现。
- Phase 4C已实现SQLite命令仓库和schema v3索引：原子创建、合法转移、全历史查询、重复ID幂等/冲突拒绝，以及重启将未完成命令标记disconnected而不重放。
- Phase 4C已实现每设备有界串行Dispatcher、跨设备并行、stop抢占与待执行取消、集群stop拆分、乱序事实缓冲、ACK/完成超时安全stop和断线终态。
- Phase 4C已将受保护action/stop/cluster-stop和命令查询API接入Runtime；动作必须online、mqtt、能力/目录/参数合法且显式确认。Browser仍不能提供Topic或原始MQTT JSON。
- Phase 4C真实Broker双fake测试验证EVA1精确执行`otto_action → stop`、完整持久状态链、QoS 0/non-retain、EVA2无串线与相同command ID只下发一次；本机71个测试通过。
- Phase 4C跨平台探针run `35306214077`通过：Windows job `105478815027`、macOS job `105478815315`均success，包含锁定安装、静态检查、71个测试、PyInstaller构建和Broker可执行文件实跑。
- Phase 4C最终SHA `f359a1fc3a50360fbcb2de42cced2f48eeb6c164` 已推送且与`origin/test0.6`一致；正式run `35306518034`的Windows job `105479722338`、macOS job `105479722462`均success。
- Phase 4D已抽取MQTT/TCP/WebSocket共享设备协议翻译器，三种Gateway只发送与命令`transport`一致的下行；Device Session记录`available_transports`并按`mqtt → websocket → tcp`选择首选传输，非首选状态/目录不能污染当前快照。
- Phase 4D已实现认证的TCP `otto-master/1`换行JSON Gateway：首帧MAC/token/client_id交叉验证、64 KiB边界、同设备新连接替换、查询、动作、stop、断线和Gateway不可用事实均通过真实loopback Socket测试。
- Phase 4D已实现Xiaozhi WebSocket v1：Bearer与设备身份认证、官方hello/listen/abort、Otto文本扩展和raw Opus二进制分流。每个listen建立`utterance_id`，Message只携带`frame_ref`与序号，原始音频只留在每设备有界内存并在断开时清理。
- Phase 4D已让Verifier和Dispatcher锁定所选传输；错误传输响应不能完成查询，在途动作遇到传输切换进入`disconnected`，不会跨传输重放。TCP与WebSocket fake设备分别完成动作及stop的完整持久生命周期。
- Phase 4D本机通过`uv lock --check`、Ruff、mypy strict（34个源码文件）、80个pytest、前端JS语法和`git diff --check`；真实HTTP/WebSocket、TCP与内嵌MQTT在同一Runtime并存并释放端口。
- Phase 4D跨平台探针run `35309090968`通过：Windows job `105487213519`、macOS job `105487213801`均success，包含锁定安装、Ruff、mypy、80个测试、PyInstaller构建和Broker可执行文件实跑。
- Phase 4D最终SHA `2b22a724d7169c2f84c5e028b95f40de7c0c4964` 已推送且与`origin/test0.7`一致；正式run `35309478483`的Windows与macOS jobs均success。
- EVA固件`2.0.6`已实现MQTT URL解析/重连、连接hello、5秒heartbeat、stop ACK、32项命令响应去重缓存、14动作Schema、NVS设备名和一次性锁定发放；ESP-IDF 5.5.5构建产物3,768,160字节，SHA256 `7580fe7f3d78641561d5a9968a5ed75b81697b2d5e2fe09b4ddad55220f5e551`。
- EVA1与EVA2均经真实OTA和独立凭据接入本机内嵌Broker。两台verify分别在367.829 ms和189.897 ms返回idle与14动作；各自`swing → stop → idle`和6步`walk`完成，6步分别约7.399秒与7.455秒，另一台保持idle。
- EVA1使用`amount=0`的swing音效路径完成累计超过60秒的内置笑声播放；以诊断心跳的`sound.busy`而非仅命令ACK确认，最终两台均online/idle、无错误。
- EVA1在缺少`current_token`的二次发放攻击中返回`provisioning is locked`且保持原身份在线；相同`gate-e3-duplicate-20260918`动作ID实投两次仅产生完全一致的缓存ACK，未二次入队。
- Otto Master与内嵌Broker完整停止后重新启动，两台EVA无需重启即在约3秒内重新hello；随后EVA1/EVA2 verify分别615.000 ms和91.554 ms通过，WebUI HTTP 200，集群stop后两台均online/idle。
- `test0.9`已实现无numpy/FFmpeg运行时的Opus编解码、火山流式ASR/TTS、DeepSeek SSE、ASR/LLM/TTS有界生产者/消费者队列、文字上屏和MQTT+AES-128-CTR UDP音频数据面。
- EVA1首个真实回合完成约2秒本地大笑、火山ASR final、DeepSeek短回答、湾区大叔音分句TTS和85个60 ms Opus帧播放；用户与助手文字均在屏幕运行态更新。
- 循环加固把回答后流程改为同一session内再次大笑并开放新utterance；最终只允许非空ASR partial取消8秒计时，设备VAD只辅助ASR端点，纯静默或仅VAD噪声由Server退出；按钮由设备`goodbye`退出，再按一次建立新session。
- 固件2.0.11把本地笑声统一为24 kHz OpusHead、34个60 ms包，并等待解码/PCM/I2S完全排空；`laugh`保持moving直到实际播放结束。EVA1连续两个门禁均观测完整busy边沿，第二次约2.1秒完成，8秒静默后UDP sessions=0。
- DeepSeek Gateway已发送真实`tools/tool_choice`并组装流式`tool_calls`；LLM Service丢弃工具前尚未成句的前缀，已成句内容则先完成TTS再串行执行唯一工具，并继续拒绝工具后的文字、多调用、未知工具和非严格JSON。RobotToolBridge从当前设备目录生成收窄Schema，把目标锁定到当前语音session，并只通过现有Dispatcher等待真实终态。
- EVA1先完成无舵机`self_otto_laugh → laugh → completed`；连续工具实测暴露“把工具结果伪装成assistant文本会影响下一轮选择”，修复为工具轮不进入普通文本历史。最终“大笑→后退两步”连续两轮均completed并只读确认idle；此前两次前进一步已由后退两步补偿。
- `test0.9`最终门禁为`144 passed`；Ruff、mypy strict（37个源码文件）和锁文件检查均通过。固件由ESP-IDF 5.5.5构建，2.0.11应用镜像3,788,720字节，SHA256 `4c4298363b621599ea11c8daba2dc3efbc644538666a9f10f7115ece49e200bf`。
- `test1.0`新增WebUI设备多选、动作交集、批量动作/stop、逐设备独立结果和Server对话泳道；`GET /api/v1/conversations`在刷新后恢复每设备状态、转写、回答、工具和错误，旧session与敏感/音频字段不能污染投影。
- TTS Service在Opus编码前对24 kHz S16LE PCM应用可配置饱和增益，当前生产配置为2.0；EVA1设备输出音量已从90提升并持久化为100。最终2.0.15镜像由ESP-IDF 5.5.5完整构建，大小3,830,880字节、SHA256 `e1ca9051c8f6a2aac1bef3e47323c1927ef6bebc3b901ae52bc393f9ad4595e6`，OTA回连已报告版本2.0.15与音量100。
- 固件2.0.16把既有手写mDNS解析器从仅供8765验证链复用到正式MQTT；NVS仍只保存`mqtt://master.local:1883`，每次连接拿到的IPv4只存在于当次Client。ESP-IDF 5.5.5完整构建镜像3,831,136字节，SHA256 `03377107cb829ce741dd5239d6d87c0d207b0f8a889532c45b953d9ae44ba9a1`；EVA1 OTA后以2.0.16、当前DHCP IP和MQTT hello自动回连。
- 固件把中央旧大眼GIF替换为21个看山对话表情，并为实际动作增加22个贴图描述符；顶部状态栏和底部用户/助手文字控件保持原路径。动作开始覆盖中央图，结束或stop恢复最近基础表情；用户目视验收仍未回填。
- `test1.0`正式对话控制已沿Message Bus/MQTT完成EVA1 start/stop关联ACK；固件先ACK再异步切换音频通道，避免UDP协商阻塞造成Server假超时。
- 03:45左右的顺滑回合均在1.1至3.1秒获得火山partial；后续卡顿轮上传491帧且VAD正常但没有partial，旧逻辑因此等满30秒。ASR现使用相互独立的partial稳定、说话后VAD静音1.2秒和12秒硬上限端点，VAD抖动不能再续掉partial/硬上限；取消时原子摘除活动utterance。首次空final只笑一次并重开，连续第二次正常退出。
- 修复后EVA1完成五轮有效问答，五轮均以`partial_stability`在3.309至8.304秒收句，final耗时0.078至0.196秒，TTS全部完成；其中“奶酪前进”虽有轻微识别误差，仍正确触发`self_otto_walk_forward`并完成动作。另有连续两次空final熔断与9次VAD-only事件下精确8秒`idle_timeout`的真机证据，最终ASR/UDP活动数为0。
- 用户在上述修复后真机链路上确认“对话感觉没问题了”，因此EVA1当前2.0倍TTS音量、可听性、卡顿和整体对话体验的主观验收记为PASS。
- WebUI新增常用动作快捷按钮、显式目标与参数、动作目录自动verify和仅限loopback的无日志fragment授权引导。EVA1真实完成两次3步前进、一次左转和一次太空步；同设备重叠点击被Dispatcher正确串行。后续一次“点击前进无动作”经命令表和消息流确认根本没有进入正式8081；本机同时存在遗留8080入口且页面没有持久授权判别。现已关闭旧进程，并增加授权徽标、未授权控件锁定及常驻提交结果。
- 修复后的8081页面再次真机操作已完成前进、抖动、弯腰和大笑，均沿`webui:batch`到达MQTT并completed；动作图遥测随动作切换并恢复。三步`swing`实际超过统一15秒完成时限而触发安全stop，生产完成门限已调整为30秒，ACK门限保持3秒。
- 当前本地门禁为`187 passed`；锁文件、Ruff、mypy strict（38个源码文件）、Node语法和`git diff --check`通过。新增mDNS测试覆盖自动地址更新、瞬时回环保护、失败保留旧记录及重试恢复。真实EVA1批量动作、对话start/stop、五轮问答、工具与退出清理在换网前通过；run `35400612369`与`35403162431`暴露的两个Windows测试同步边界均已修复且未放宽生产逻辑，上一代码检查点run `35403562279`双平台及打包smoke全部PASS。
- EVA2完整串口升级到2.0.16后，正式verify和按钮触发的单会话笑声→ASR→DeepSeek/TTS→再次监听均成功；EVA3以正确名称、MAC和受保护独立MQTT身份接入。正式三机批次`array-eva1-eva2-eva3-20260919-01`请求3、接受3、失败0，所有命令均到completed且三台最终online/idle。
- `test1.1`在仓库根新增独立Vite/TypeScript `webui/`，保留Forge电台3D模型、贴图和交互；Server页已改接Otto真实健康、设备、动作目录、批量命令、对话、设置、固件和事件API。参考工程的假设备状态、Node/Worker生产后端和浏览器小智音频桥均未迁入。
- Python后端新增知乎官方只读Gateway/Service：只访问`developer.zhihu.com`，支持额度探针、多种单页查询、非敏感画像和有界元数据事件；2路并发、超时、2 MiB响应上限、无重试和稳定错误均有自动测试。Access Secret只从环境读取，真实额度探针和一次最小热榜查询已通过且输出不含密钥或原始内容。
- Forge电台结果可由用户显式选择稳定`device_id`后复用既有TTS Service朗读；浏览器仍不接触设备凭据、MQTT、云端语音或知乎密钥。全部知乎/画像/朗读API沿用控制台Bearer授权。
- 前端`npm ci --ignore-scripts && npm run build`通过，41个模块构建为哈希JS/CSS和离线模型/图片/GIF快照；Python静态入口、嵌套包数据及本机PyInstaller onefile实跑均通过。Runtime优雅关闭后8081/1883/8884全部释放，最终构建重启后健康、Forge首页、授权边界、知乎配置和设备列表HTTP smoke通过；只读观察到3台登记、2台在线，本轮未下发动作。
- 本轮本地最终门禁为`203 passed`、Ruff、mypy strict（41个源码文件）、`uv lock --check`、npm 0漏洞、前端禁用链扫描、精确密钥扫描及`git diff --check`。外部知乎烟测只调用一次额度和一次最小只读查询；远程macOS/Windows CI仍须在提交push后回填。

## 进行中

- `test1.1` Forge前端、知乎只读后端、根目录GitHub README和本地门禁已完成；尚需精确提交、push并等待同一SHA的macOS/Windows CI与静态资源PyInstaller job通过。
- `test1.0`真机能力保持不变；仍需由用户确认看山动作图切换/恢复。`test1.1`没有修改固件，也没有用只读设备在线状态替代任何动作或语音真机验收。
- 完整Phase 5仍欠多设备并发语音/工具隔离、WebSocket真机Profile和实体Windows Opus/局域网门禁。EVA2单会话成功不能冒充双机或三机并发语音通过。
- 多设备WebUI已经具备显式目标和并发控制；持久设备组、普通广播策略和多设备同时语音真机仍是后续范围。

## 下一步

先完成`test1.1`精确提交、远端哈希和macOS/Windows CI收口后暂停。下一轮再由用户确认看山动作图，并补多设备并发语音/工具隔离、WebSocket真机Profile与实体Windows。

## 已知风险

- Forge静态资源已通过本机PyInstaller收集，GitHub工作流也已增加macOS/Windows同一可执行文件实跑；远程结果在`test1.1` push前不能预判为PASS，实体Windows仍需验证防火墙、mDNS和Opus DLL分发。
- 火山ASR/TTS适配器、音频背压、EVA1完整真机和EVA2单会话已验证；多设备并发语音、WebSocket真机和Windows `libopus` 打包尚未验证。Provider字段仍须通过Gateway隔离，不能泄漏到领域合同。
- 当前账号的ASR 2.0资源请求返回403，Phase 5先使用已验证的ASR 1.0时长版；2.0开通前不得自动切换或把403误报为密钥整体失效。
- DeepSeek真实tool schema和tool-call消费已实现，但当前只允许当前语音设备的单调用；工具轮即使有已朗读前置句也不保留普通文本历史，直到`ChatMessage`支持规范的assistant tool_calls与tool result结构。双设备语音隔离和长时间稳定性仍待验收。
- 服务端2.0倍数字增益配合设备音量100可能对接近满幅的PCM产生饱和钳位；最终以用户听感为准，若有明显破音应改用压缩/限幅而不是继续提高硬增益。
- 固件2.0.16继承循环按钮、VAD、笑声播放完成、动作生命周期、看山贴图、音量100迁移和正式对话控制先ACK后切换，并补上MQTT显式mDNS解析；当前控制消息仍保持QoS 0/non-retain。
- Windows真实局域网mDNS、防火墙提示和完整应用打包仍留给Phase 9实体Windows环境；本轮Windows CI已覆盖aMQTT认证/ACL、Runtime网络集成和Broker PyInstaller可执行文件。
- Server mDNS地址监视解决换网后继续广播旧IP，固件2.0.16则避免正式MQTT依赖路由器DNS或旧`.local`缓存。完全空白的新Server仍不认识设备旧密码，必须迁移`.local-secrets/mqtt-credentials.json`或增加受认证配对流程；mDNS只负责定位，不替代身份认证。
- fake、历史双机门禁和当前三机的accepted/moving/completed、设备隔离均已通过；锁定改配拒绝、重复ID及Server/Broker重启恢复已有双机真机证据。
- 知乎凭据曾由用户在对话中提供；仓库、日志、SQLite、浏览器和Git差异扫描均未发现原值，但正式发布前仍应轮换该凭据。
- TCP和设备WebSocket当前是无TLS的局域网兼容入口，不得直接暴露到互联网或不可信网络；生产化前需要TLS终止、证书校验和对应威胁模型。
