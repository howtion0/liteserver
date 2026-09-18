# Development Progress

## 当前版本

版本以 `pyproject.toml` 为准，当前为 `0.1.0`。

## Git迭代

- Phase 0和Phase 1正在建立恢复检查点；本轮支线为 `test0.1`。
- 本轮将把“Phase 0文档 + Phase 1实现”作为一次透明的恢复基线，而不是伪造两段历史。
- 恢复基线必须重跑Phase 1全部必需验证、补齐Session Contract和日志，随后commit、push并核对远程哈希。
- Phase 2使用 `test0.2`；此后每个阶段或补充检查点使用下一个 `testN.N` 编号。
- 支线编号与产品版本分别记录，互不驱动。

## 当前状态

| 项目 | 状态 |
|---|---|
| Phase 0 文档脚手架 | 已完成 |
| Phase 1 Runtime与Message Bus | 已完成 |
| Phase 2 SQLite | 未开始 |
| Phase 3 Web/OTA/mDNS/Embedded MQTT Broker | 未开始 |
| Phase 4 MQTT控制/TCP回退/WebSocket兼容 | 未开始 |
| Phase 5 Opus/ASR/TTS | 未开始 |
| Phase 6 WakeGate | 未开始 |
| Phase 7 LLM/Dispatcher/动作 | 未开始 |
| Phase 8 集群/日志/容错 | 未开始 |
| Phase 9 Windows打包 | 未开始 |
| Python业务实现 | Phase 1已实现；后续业务模块仍为空占位 |
| 自动测试 | 10通过 |
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
- Phase 1已实现YAML配置加载、dotenv/环境变量替换、消息合同校验、精确主题Message Bus、Runtime优雅关闭、统一线程池和JSONL结构化日志。
- Phase 1已通过 `ruff check src tests`、`mypy src`、10个pytest单元测试，以及 `python -m otto_master` 启动/中断退出冒烟测试。

## 进行中

- 无。Phase 1已完成，等待Phase 2 SQLite持久化。

## 下一步

完成 `test0.1` 恢复基线后，在 `test0.2` 进入Phase 2，实现SQLite连接生命周期、版本化迁移、消息日志订阅者和敏感字段过滤；继续不接硬件和真实云API。

## 已知风险

- Python Opus库在Windows打包时可能需要额外动态库收集，留到Phase 5和Phase 9验证。
- 云ASR/TTS供应商尚未选定，适配接口必须避免绑定厂商类型。
- DeepSeek模型名称来自当前官方配置；实现阶段仍需用新密钥完成一次真实连通测试。
- ESP32云端唤醒需要固件在休眠时通过VAD触发音频上传，服务端完成后仍需配套固件改造。
- 固件2.0.5的MQTT入口缺少stop、独立hello/heartbeat和命令ID去重；Phase 4必须补齐后再启用QoS 1。
- 内嵌MQTT Broker库必须在macOS与Windows完成启动、ACL、PyInstaller和断线恢复验证，不能只验证导入成功。
