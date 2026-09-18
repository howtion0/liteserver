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

### 2026-09-18 / Phase 3 / 本地网络控制面与Embedded MQTT Broker

- 版本：`0.3.0`
- Git迭代：`test0.3`，基线为已推送的 `test0.2` / `cf2bc419810df69cfa92dea0fa32df613d85fecd`
- 目标：在同一Python进程打通Web/API、状态事件、OTA、mDNS、SQLite查询与安全的MQTT 3.1.1 Broker。
- 修改：新增aMQTT适配与动态每设备凭据/ACL、FastAPI控制面、静态控制台、EventHub snapshot+cursor、OTA manifest/下载/发放、mDNS服务、Runtime网络生命周期和跨平台CI/PyInstaller smoke。
- 安全：匿名MQTT拒绝；用户名、密码和client_id交叉验证；设备只能发布自己的up并订阅自己的down；Browser API无任意Topic入口；非loopback修改、WebSocket与设备发放均失败关闭；响应和事件脱敏。
- 本机验证：`ruff check src tests` PASS；`mypy src` PASS；`pytest -q` 26 PASS；真实HTTP/WebSocket事件推送与端口释放PASS；真实MQTT认证、ACL、往返与关闭PASS；mDNS注册、解析`master.local`和注销PASS；macOS PyInstaller onefile Broker smoke输出`mqtt-broker-smoke:pass`。
- 返工：真实WebSocket关闭测试发现服务端未并行监听disconnect，导致Uvicorn等待心跳并占用端口；改为同时等待客户端帧与事件队列，并增加强制关闭路径后复测通过。
- 未运行：Windows测试与Windows PyInstaller smoke等待本支线触发的GitHub Actions矩阵；ESP32/EVA真机、Device MQTT Gateway和动作闭环属于Phase 4，未运行。
- 状态：在Windows矩阵PASS前保持Phase 3进行中，不合并main。
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
