# ONBOARD：Otto Master 快速入口

## 1. 项目是什么

Otto Master 是一个 Python 编写的机器人集群主控。它在单个进程中统一承担：

- ESP32 MQTT集群控制、TCP回退、WebSocket兼容与Opus音频接入
- 火山ASR/TTS、DeepSeek流式对话和受限机器人工具
- 每轮约2秒本地笑声门禁、循环问答、8秒无讲话退出和按钮进出
- 单机、分组和集群动作分发
- Forge电台3D WebUI、REST API、OTA、mDNS和知乎官方只读查询
- SQLite 本地持久化

开发在 macOS 进行，主要部署目标为 Windows。

## 2. 架构一句话

```text
Gateways → Message Bus → Device Sessions / WakeGate / Dispatcher / Storage
                       ↘ Cloud Services / Worker Pool
```

这是模块化单体，不是微服务：模块分开，部署仍然只有一个 Python 服务。

## 3. 核心边界

```text
外部世界                Otto Master 内部

ESP32 MQTT/TCP/WS ┐
ESP32 UDP/Opus  ──┤
WebUI HTTP/WS  ───┼─ Gateway ─ Internal Message ─ Message Bus
Cloud HTTP/WS  ───┤                                  │
mDNS / OTA     ───┘                                  ├─ WakeGate
                                                    ├─ Sessions
                                                    ├─ Dispatcher
                                                    └─ SQLite
```

外部协议和内部消息必须分离。Gateway负责翻译，业务模块不解析MQTT Topic、Otto TCP JSON或Xiaozhi原始JSON。内嵌MQTT Broker是设备网络层，不能代替进程内Message Bus。

## 4. 当前状态

精确版本以 `pyproject.toml` 为准。

| 项目 | 状态 |
|---|---|
| Phase 0 文档和目录 | 已完成 |
| Phase 1 Runtime与Message Bus | 已完成 |
| Phase 2 SQLite | 已完成 |
| Phase 3 网络控制面 | 已完成；macOS/Windows CI与两平台PyInstaller smoke通过 |
| Phase 4 MQTT与Device Session | 进行中；软件门禁、EVA1/EVA2双机和EVA1/EVA2/EVA3正式MQTT批量动作通过，WebSocket真机Profile待验收 |
| Phase 5-7 语音/对话/工具 | 进行中；EVA1完整MQTT+UDP问答和工具闭环、EVA2单会话通过，多设备并发语音待验收 |
| Python业务代码 | Runtime、设备、语音、Dispatcher、Forge控制台和知乎官方只读Gateway/Service已接入同一进程 |
| WebUI | 源码位于仓库根`webui/`，Forge 3D界面已接真实健康、设备、命令、对话、设置、OTA和知乎API；可信LAN默认直接控制，空动作目录自动只读恢复；生产使用Python包内构建快照 |
| SQLite数据库 | 运行时自动创建并迁移 |
| 固件文件 | 未放入 |
| 测试 | `test1.2`本机207项、Ruff、mypy、TypeScript/Vite、Runtime HTTP、静态资源smoke、Windows源码密钥导出测试和EVA2/EVA3免令牌前进门禁通过；本轮远程macOS/Windows CI待push后确认 |

当前工程由一个Python进程启动控制面、Broker、MQTT/UDP/TCP/设备WebSocket、Device Manager、Dispatcher、WakeGate、云端语音/LLM和知乎只读Service。EVA1/EVA2/EVA3均有2.0.16稳定身份和正式MQTT控制证据；`test1.2`未修改固件，EVA2/EVA3各一步前进已真实完成，但没有把它冒充并发语音验收。默认直控只适用于可信家庭局域网；对不可信网络应开启`server.console_auth_required`。未完成项以`docs/DEV_PROGRESS.md`为准。

## 5. 文档索引

| 目的 | 文档 |
|---|---|
| 最高原则和产品边界 | `CODEX_MASTER_REQUIREMENTS.md` |
| 强制施工门禁 | `CODEX_CONSTRUCTION_WORKFLOW.md` |
| 模块职责、依赖和副作用边界 | `CODEX_ARCHITECTURE.md` |
| Git协作与回滚 | `CODEX_RULES_GIT.md` |
| 测试标准 | `CODEX_RULES_TESTING.md` |
| 单次施工契约 | `CODEX_SESSION_CONTRACT_TEMPLATE.md` |
| 分阶段施工路线 | `docs/CONSTRUCTION_PLAN.md` |
| 当前进度 | `docs/DEV_PROGRESS.md` |
| 模块状态 | `docs/MODULE_STATUS.md` |
| 施工记录 | `docs/LOG.md` |
| 消息格式和主题 | `docs/MESSAGE_CONTRACTS.md` |
| MQTT、TCP与设备WebSocket传输合同 | `docs/DEVICE_TRANSPORT_CONTRACT.md` |
| MQTT控制、迁移和EVA真机验收 | `docs/MQTT_CONTROL_CONTRACT.md` |
| Phase 5火山ASR/TTS、Opus数据流和复用边界 | `docs/VOLCENGINE_SPEECH_INTEGRATION.md` |
| Server控制台与打包前验收 | `docs/SERVER_CONSOLE_REQUIREMENTS.md` |
| Windows源码包恢复与启动 | `docs/WINDOWS_PORTABLE_GUIDE.md` |
| 调试路线 | `docs/DEBUG_GUIDE.md` |

## 6. 开发环境

```powershell
Copy-Item .env.example .env
uv sync --all-extras --locked
uv run python -m otto_master
```

```bash
cp .env.example .env
uv sync --all-extras --locked
uv run python -m otto_master
```

使用Python 3.11+。生产运行不需要Node；只在修改仓库根`../webui/`时运行`npm ci && npm run build`，构建快照写入`src/otto_master/web/`。

## 7. 每次施工前

1. 先执行GitHub备份门禁：fetch、检查工作区，并确认上一检查点已push且远程哈希与本地一致。
2. 当前可恢复基线尚未在GitHub时，停止新施工，先完成当前检查点的验证、日志、commit和测试支线push。
3. 完整阅读 `CODEX_CONSTRUCTION_WORKFLOW.md` 规定的施工依据，不依赖聊天记忆。
4. 检查当前Phase、目标、非目标、已知风险和前置条件。
5. 自动确认下一个未使用的 `testN.N` 编号，并从最新已验收的 `main` 创建支线，不需要再次请求授权。
6. 从模板建立本轮Session Contract，写明允许修改路径、回滚点和测试矩阵。
7. 每条验收标准必须有二元可判定的测试命令或人工观测方法。
8. 以上全部完成后才允许修改实现代码。

## 8. 每次施工后

1. 按Session Contract运行类型检查、单元、集成、硬件和其他必需测试。
2. 任一测试失败就记录证据、定位、返工、重跑原测试和受影响回归，直到全部必需测试PASS。
3. FAIL、BLOCKED或NOT RUN均不得标记Phase完成，不得合并main。
4. 全部PASS后更新 `docs/DEV_PROGRESS.md`、`docs/MODULE_STATUS.md`、`docs/LOG.md` 和本轮Session Contract。
5. 检查diff、密钥和暂存范围，一个 `testN.N` 支线只形成一次验收提交。
6. 自动commit并push同名测试支线到GitHub，再比较远程哈希与本地HEAD；无需再次请求授权。
7. 只有远程备份、测试、日志和哈希核对全部成立后，才能报告本轮完成。
8. 合并 `main`、tag、release、force-push和删除远程分支必须单独授权。

## 9. 安全红线

- `.env` 只能保存在本机。
- 云API超时或唤醒验证失败时，机器人保持休眠。
- 广播动作必须显式标记目标范围。
- MQTT动作命令禁止retain；启用QoS 1前必须完成命令ID去重。
- 真机测试必须在结束时向涉及设备发送stop并确认idle。
- 运行时Socket、音频缓冲、线程对象不得写入SQLite。
- WebUI输入必须经过消息校验，不能直接调用设备Socket或发布MQTT。
