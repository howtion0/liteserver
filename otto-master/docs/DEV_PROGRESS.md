# Development Progress

## 当前版本

版本以 `pyproject.toml` 为准，当前为 `0.4.1`。

## Git迭代

- Phase 0+1恢复基线 `test0.1` 已推送，远程哈希为 `e0097c4649322175356487f13d73fde078afdd09`。
- Phase 2支线 `test0.2` 已推送，远程哈希为 `cf2bc419810df69cfa92dea0fa32df613d85fecd`。
- Phase 3支线 `test0.3` 已推送，远程哈希为 `7fbb368569f162aa135c197438aabfbe861474c4`；最终run `35299643584`的macOS/Windows jobs均PASS。
- Phase 4A本轮支线为 `test0.4`，明确从已验收的 `test0.3` 继续；跨平台探针run `35300950492`的macOS/Windows jobs均PASS。
- Phase 4A最终支线 `test0.4` 已推送，远程哈希为 `3d46780a89f2c1955f673ccfbad349b8f342c941`；最终run `35301297710`的macOS/Windows jobs均PASS。
- Phase 4B本轮支线为 `test0.5`，从已验收的 `test0.4` 继续；跨平台探针run `35303748098`的macOS/Windows jobs均PASS。
- 此后每个阶段或补充检查点使用下一个 `testN.N` 编号。
- 支线编号与产品版本分别记录，互不驱动。

## 当前状态

| 项目 | 状态 |
|---|---|
| Phase 0 文档脚手架 | 已完成 |
| Phase 1 Runtime与Message Bus | 已完成 |
| Phase 2 SQLite | 已完成 |
| Phase 3 Web/OTA/mDNS/Embedded MQTT Broker | 已完成 |
| Phase 4 MQTT控制/TCP回退/WebSocket兼容 | 进行中；Phase 4A上行与Phase 4B只读双向验证已通过当前门禁 |
| Phase 5 Opus/ASR/TTS | 未开始 |
| Phase 6 WakeGate | 未开始 |
| Phase 7 LLM/Dispatcher/动作 | 未开始 |
| Phase 8 集群/日志/容错 | 未开始 |
| Phase 9 Windows打包 | 未开始 |
| Python业务实现 | Phase 1-3基座及Phase 4A/4B MQTT Session、精确查询与连接验证已实现 |
| 自动测试 | 53通过；Ruff与mypy strict通过；Phase 4B macOS/Windows探针矩阵通过 |
| 硬件验证 | 未运行 |

## 已完成

- 在 `liteserver/otto-master/` 建立标准Python包目录。
- 建立非敏感 `config.yaml` 和本地 `.env` 模板。
- 建立Runtime、Message Bus、Gateway、Device、Service、Dispatcher、Audio和Storage模块骨架。
- 建立WebUI、数据、固件、日志和测试占位目录。
- 将参考ZIP的宪法、架构、施工、进度和日志方法改写为Otto Master版本。
- 将“Event Bus”统一为“Message Bus”。
- 明确macOS开发、Windows部署和单Python进程边界。
- LLM Provider已选定为DeepSeek官方OpenAI兼容接口；密钥仍只从本地环境变量读取。
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

## 进行中

- Phase 4B本地与跨平台探针验收已通过；等待`test0.5`最终提交/push及精确SHA的正式矩阵核对。
- 完整Phase 4尚未完成：没有动作/stop生命周期、命令仓库/队列、TCP回退、Xiaozhi WebSocket、固件hello/heartbeat/stop/去重或EVA真机结果。

## 下一步

完成`test0.5`远程门禁后建立下一检查点：实现动作/stop命令仓库、每设备有序队列和`requested → published → accepted → moving → completed` fake闭环，再进入兼容传输和EVA真机安全验收。

## 已知风险

- Python Opus库在Windows打包时可能需要额外动态库收集，留到Phase 5和Phase 9验证。
- 云ASR/TTS供应商尚未选定，适配接口必须避免绑定厂商类型。
- DeepSeek模型名称来自当前官方配置；实现阶段仍需用新密钥完成一次真实连通测试。
- ESP32云端唤醒需要固件在休眠时通过VAD触发音频上传，服务端完成后仍需配套固件改造。
- 固件2.0.5的MQTT入口缺少stop、独立hello/heartbeat和命令ID去重；Phase 4必须补齐后再启用QoS 1。
- Windows真实局域网mDNS、防火墙提示和完整应用打包仍留给Phase 9实体Windows环境；本轮Windows CI已覆盖aMQTT认证/ACL、Runtime网络集成和Broker PyInstaller可执行文件。
- fake设备双向只读验证已实现；动作accepted/moving/completed关联、Broker重启后设备自动恢复、真机固件能力和安全动作仍属于后续Phase 4检查点。
