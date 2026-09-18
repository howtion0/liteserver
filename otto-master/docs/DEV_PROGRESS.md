# Development Progress

## 当前版本

版本以 `pyproject.toml` 为准，当前为 `0.4.3`。

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
- Phase 4E真机支线为 `test0.8`，从已验收的 `test0.7` 继续；两台EVA控制、锁定改配拒绝、重复ID和Server/Broker重启恢复均已通过，正式CI待提交后运行。
- Phase 5纵向MVP工作支线为`test0.9`；EVA1的真实语音闭环、循环对话、本地门禁及DeepSeek单设备工具桥已通过，本地门禁完成，本检查点提交后推送该支线并运行正式跨平台CI。
- 配套EVA固件2.0.11源码已推送到`howtion0/otto`的`codex/otto-portable`，远端SHA为`abb769f1d0f3d1c03fb7a6106bd7288f31c66a98`。
- 此后每个阶段或补充检查点使用下一个 `testN.N` 编号。
- 支线编号与产品版本分别记录，互不驱动。

## 当前状态

| 项目 | 状态 |
|---|---|
| Phase 0 文档脚手架 | 已完成 |
| Phase 1 Runtime与Message Bus | 已完成 |
| Phase 2 SQLite | 已完成 |
| Phase 3 Web/OTA/mDNS/Embedded MQTT Broker | 已完成 |
| Phase 4 MQTT控制/TCP回退/WebSocket兼容 | 进行中；Phase 4A-4D软件门禁完成，Phase 4E两台EVA的2.0.6 OTA、MQTT控制、锁定改配、重复ID及Server/Broker重启恢复已通过；仅本支线正式CI待验收 |
| Phase 5 Opus/ASR/TTS | 进行中；火山ASR/TTS、Opus、MQTT加密UDP和EVA1真实闭环已实现，EVA2、WebSocket真机与Windows矩阵未完成 |
| Phase 6 WakeGate | 进行中；每轮2秒笑声门禁、循环问答、8秒静默退出、按钮进出和失败自恢复已在EVA1验收 |
| Phase 7 LLM/Dispatcher/动作 | 部分完成；DeepSeek流式`tools/tool_calls`、当前语音设备Schema、参数校验和Dispatcher桥已实现并在EVA1验证；分组/广播和双机语音工具隔离未完成 |
| Phase 8 集群/日志/容错 | 未开始 |
| Phase 9 Windows打包 | 未开始 |
| Python业务实现 | Phase 1-4基座及Cloud/ASR/LLM/TTS、RobotToolBridge、DeviceAudioRouter、MQTT UDP和循环WakeGate纵向链已实现 |
| 自动测试 | 144通过；Ruff、mypy strict和锁文件检查通过；test0.9正式CI尚未运行 |
| 硬件验证 | EVA1运行2.0.11并通过真实流式问答、循环门禁、按钮/静默退出以及DeepSeek `laugh/walk`工具；连续“大笑→后退两步”真实完成并回到idle。EVA2关机，实体Windows仍属于后续门禁 |

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
- 循环加固把回答后流程改为同一session内再次大笑并开放新utterance；VAD/partial取消8秒计时，纯静默由Server退出，按钮由设备`goodbye`退出，再按一次建立新session。
- 固件2.0.11把本地笑声统一为24 kHz OpusHead、34个60 ms包，并等待解码/PCM/I2S完全排空；`laugh`保持moving直到实际播放结束。EVA1连续两个门禁均观测完整busy边沿，第二次约2.1秒完成，8秒静默后UDP sessions=0。
- DeepSeek Gateway已发送真实`tools/tool_choice`并组装流式`tool_calls`；LLM Service拒绝混合文本/工具、多调用、未知工具和非严格JSON。RobotToolBridge从当前设备目录生成收窄Schema，把目标锁定到当前语音session，并只通过现有Dispatcher等待真实终态。
- EVA1先完成无舵机`self_otto_laugh → laugh → completed`；连续工具实测暴露“把工具结果伪装成assistant文本会影响下一轮选择”，修复为工具轮不进入普通文本历史。最终“大笑→后退两步”连续两轮均completed并只读确认idle；此前两次前进一步已由后退两步补偿。
- 当前全量门禁为`144 passed`；Ruff、mypy strict（37个源码文件）和锁文件检查均通过。固件由ESP-IDF 5.5.5构建，2.0.11应用镜像3,788,720字节，SHA256 `4c4298363b621599ea11c8daba2dc3efbc644538666a9f10f7115ece49e200bf`。

## 进行中

- `test0.9`的EVA1纵向MVP和本地自动门已完成；固件检查点已上传，Otto Master本检查点正在执行最终提交/push与正式CI。
- 完整Phase 5仍欠EVA2语音隔离、WebSocket真机Profile、Windows/PyInstaller Opus门禁。EVA2由用户关机，不能把单机结果写成双机通过。
- 当前单设备工具桥已完成；仍欠从真实按钮/麦克风走完整ASR工具回合的主观验收，以及EVA2开启后的双设备目标隔离。

## 下一步

恢复施工后先由用户在EVA1按钮对话中口述“大笑、前进一步、后退一步”，确认ASR文字上屏、工具执行、成功后无额外TTS及下一轮笑声门禁。随后EVA2开机补双设备语音/工具隔离，再补WebSocket真机Profile、正式CI与实体Windows。

## 已知风险

- Python Opus库在Windows打包时可能需要额外动态库收集，留到Phase 5和Phase 9验证。
- 火山ASR/TTS适配器、音频背压和EVA1真机已验证；EVA2、WebSocket真机和Windows `libopus` 打包尚未验证。Provider字段仍须通过Gateway隔离，不能泄漏到领域合同。
- 当前账号的ASR 2.0资源请求返回403，Phase 5先使用已验证的ASR 1.0时长版；2.0开通前不得自动切换或把403误报为密钥整体失效。
- DeepSeek真实tool schema和tool-call消费已实现，但当前只允许当前语音设备的单调用；工具轮暂不保留对话历史，直到`ChatMessage`支持规范的assistant tool_calls与tool result结构。双设备语音隔离和长时间稳定性仍待验收。
- 固件2.0.11已补齐循环按钮、VAD、笑声播放完成和动作生命周期；当前控制消息仍保持QoS 0/non-retain。
- Windows真实局域网mDNS、防火墙提示和完整应用打包仍留给Phase 9实体Windows环境；本轮Windows CI已覆盖aMQTT认证/ACL、Runtime网络集成和Broker PyInstaller可执行文件。
- fake与两台真机的accepted/moving/completed、stop、设备隔离、锁定改配拒绝、重复ID及Server/Broker重启恢复均已通过。
- TCP和设备WebSocket当前是无TLS的局域网兼容入口，不得直接暴露到互联网或不可信网络；生产化前需要TLS终止、证书校验和对应威胁模型。
