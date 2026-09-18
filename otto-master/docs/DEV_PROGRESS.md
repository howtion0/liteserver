# Development Progress

## 当前版本

版本以 `pyproject.toml` 为准，当前为 `0.3.0`。

## Git迭代

- Phase 0+1恢复基线 `test0.1` 已推送，远程哈希为 `e0097c4649322175356487f13d73fde078afdd09`。
- Phase 2支线 `test0.2` 已推送，远程哈希为 `cf2bc419810df69cfa92dea0fa32df613d85fecd`。
- Phase 3本轮支线为 `test0.3`，明确从已验收的 `test0.2` 继续；当前等待远程macOS/Windows矩阵结果后形成验收提交。
- 此后每个阶段或补充检查点使用下一个 `testN.N` 编号。
- 支线编号与产品版本分别记录，互不驱动。

## 当前状态

| 项目 | 状态 |
|---|---|
| Phase 0 文档脚手架 | 已完成 |
| Phase 1 Runtime与Message Bus | 已完成 |
| Phase 2 SQLite | 已完成 |
| Phase 3 Web/OTA/mDNS/Embedded MQTT Broker | 进行中：macOS实现/联调/PyInstaller PASS，Windows CI待运行 |
| Phase 4 MQTT控制/TCP回退/WebSocket兼容 | 未开始 |
| Phase 5 Opus/ASR/TTS | 未开始 |
| Phase 6 WakeGate | 未开始 |
| Phase 7 LLM/Dispatcher/动作 | 未开始 |
| Phase 8 集群/日志/容错 | 未开始 |
| Phase 9 Windows打包 | 未开始 |
| Python业务实现 | Phase 1-3网络基座已实现；Device Session与后续业务仍为空占位 |
| 自动测试 | 26通过；Ruff与mypy strict通过 |
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

## 进行中

- `test0.3`远程macOS/Windows GitHub Actions矩阵待运行；结果未通过前Phase 3保持进行中。

## 下一步

先完成`test0.3`跨平台CI、文档闭环和远程哈希核对；通过后进入`test0.4`，实现Phase 4 Device MQTT Gateway、假设备Session和状态闭环，再安排EVA1/EVA2真机安全测试。

## 已知风险

- Python Opus库在Windows打包时可能需要额外动态库收集，留到Phase 5和Phase 9验证。
- 云ASR/TTS供应商尚未选定，适配接口必须避免绑定厂商类型。
- DeepSeek模型名称来自当前官方配置；实现阶段仍需用新密钥完成一次真实连通测试。
- ESP32云端唤醒需要固件在休眠时通过VAD触发音频上传，服务端完成后仍需配套固件改造。
- 固件2.0.5的MQTT入口缺少stop、独立hello/heartbeat和命令ID去重；Phase 4必须补齐后再启用QoS 1。
- Windows下aMQTT启动、ACL、Runtime网络集成和PyInstaller尚待远程矩阵实跑；在PASS前不宣告Phase 3跨平台完成。
- 设备在线状态、动作ACK/moving/idle和Broker重启后的设备恢复属于Phase 4，当前控制台明确返回组件未就绪，不伪造在线。
