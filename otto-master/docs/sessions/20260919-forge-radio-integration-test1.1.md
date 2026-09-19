# Otto Master Session Contract：Forge 电台与知乎官方 API 合并

## 基本信息

- 日期：2026-09-19
- 当前版本：`0.5.0`
- 当前Phase：Phase 8 控制台/可观测性加固，并前置验证 Phase 9 Windows 静态资源交付
- Git迭代支线：`test1.1`

## Gate 0：GitHub基线

- 基线分支：`test1.0`
- 本地commit：`18181851401e8ebef516c71d847b76e924c27f26`
- 远程commit：`18181851401e8ebef516c71d847b76e924c27f26`
- 本地与远程一致：是
- 当前工作区已有改动及归属：根目录 `README.md` 已有用户内容，本轮经用户明确要求在保留其内容的基础上补成GitHub入口并纳入提交；`.DS_Store`、参考ZIP、`SERVER_FRONTEND_API_PLAN.md`、看山贴图和完整 `forge-radio/` 参考目录仍属于用户，不纳入本轮提交；`webui/` 为空目录，是本轮允许写入的新前端源码目录
- 密钥、数据库、日志、固件和无关改动已排除：是；知乎 Access Secret 只存在于本机受保护 `.env`，禁止出现在源码、测试输出、文档或Git差异

## Gate 1：施工依据已阅读

- [x] `AGENTS.md`
- [x] `ONBOARD.md`
- [x] `CODEX_CONSTRUCTION_WORKFLOW.md`
- [x] `CODEX_MASTER_REQUIREMENTS.md`
- [x] `CODEX_ARCHITECTURE.md`
- [x] `docs/DEV_PROGRESS.md`
- [x] 当前Phase的 `docs/CONSTRUCTION_PLAN.md`
- [x] `docs/MESSAGE_CONTRACTS.md`、`docs/SERVER_CONSOLE_REQUIREMENTS.md` 与用户提供的 `SERVER_FRONTEND_API_PLAN.md`
- [x] `CODEX_RULES_TESTING.md`
- [x] `CODEX_RULES_GIT.md`
- [x] `forge-radio/docs/zhihu/` 官方能力参考、`forge-radio/backend/` 与 `forge-radio/frontend/` 合并参考

## 本轮目标

- 以 `forge-radio/frontend` 的视觉与交互为前端主版本，在仓库根 `webui/` 建立独立的 Vite/TypeScript 源码工程。
- 以现有 Otto Master 为后端主版本，保留真实设备、MQTT、命令、会话、鉴权、OTA和事件合同；删除/禁用参考工程的假设备后端与独立小智代理。
- 将参考工程的知乎官方只读查询能力纳入 Otto Master 单进程，使用环境变量凭据、严格输入验证、响应上限、并发上限、无自动重试和稳定错误。
- 让新前端同时操作知乎电台和 Otto 真实控制台，并由服务端直接提供已构建静态资源。
- 确保 Windows 运行不依赖 Node、Shell、Docker、外置MQTT Broker或POSIX权限语义；Node只用于开发期构建并提交可交付静态产物。

## 涉及模块

- `webui/`：Vite/TypeScript 前端源码、Forge 视觉资源和构建配置
- `src/otto_master/gateways/zhihu.py`：知乎官方 HTTP Gateway
- `src/otto_master/services/zhihu.py`：查询、画像、能力与有界事件服务
- `src/otto_master/gateways/web.py`：同源 REST、鉴权、静态资源和设备朗读入口
- `src/otto_master/config.py`、`runtime.py`、`config.yaml`：配置、环境密钥和生命周期装配
- `src/otto_master/web/`：由 `webui` 构建生成、供Python运行时/打包使用的静态快照
- `tests/`、`.github/workflows/`：后端、前端静态交付和Windows打包回归

## 允许修改的路径

- `webui/**`
- `otto-master/.env.example`
- `otto-master/config.yaml`
- `otto-master/pyproject.toml`
- `otto-master/src/otto_master/**`
- `otto-master/tests/**`
- `otto-master/docs/**`
- `otto-master/CODEX_ARCHITECTURE.md`
- `otto-master/README.md`
- `.github/workflows/otto-master-phase3.yml`
- 根目录 `.gitattributes`（仅为Vite生成JS中的Three.js GLSL模板关闭Git尾随空格误报）
- 根目录 `README.md`（仅保留已有内容并补充已验证的GitHub项目入口）

参考资料、ZIP、看山贴图、`.env`、数据库、日志、固件和设备仓库均不修改或提交。

## 输入与外部依赖

- 是否需要ESP32：否
- 是否需要真实云API：是；只做一次知乎额度/权限探针和一次最小只读查询，不记录内容或密钥
- 是否需要局域网：否；运行态只读设备列表可作为附加烟测，不作为本轮硬件门禁
- 是否需要Windows验证：是；GitHub Windows CI、PyInstaller静态资源收集和启动导入烟测
- 是否需要MQTT Broker：内嵌真Broker（完整Runtime烟测时使用）
- 是否需要EVA真机：否
- 固件版本：不改固件；现有设备基线 `2.0.16`
- 预期传输：HTTP/同源WebSocket控制台；设备传输合同保持 `mqtt`
- 安全清理：不下发动作；若附加烟测启动Runtime，结束时优雅关闭并确认端口释放

## 验收标准

- [x] 新WebUI保留Forge 3D/电台视觉，Server页使用Otto真实API与稳定 `device_id`，所有mutation在无控制令牌时保持锁定。
- [x] 知乎能力只调用官方 `developer.zhihu.com` 只读接口，不支持cookie、抓取、发布、自动翻页或自动重试。
- [x] Access Secret只从环境读取，所有GET、错误、日志、事件、SQLite和前端产物均不泄露凭据。
- [x] Python后端和浏览器使用同源 `/api/v1/*`，不引入第二个生产服务或浏览器直连MQTT/设备。
- [x] 前端构建产物由Python包离线提供，Windows运行时无需Node；嵌套模型、贴图、CSS和JS均进入包与PyInstaller收集。
- [x] macOS本地静态检查、完整pytest、真实知乎最小烟测和HTTP集成烟测通过。
- [ ] 推送后的macOS/Windows GitHub矩阵和PyInstaller烟测全部通过。

## 验收矩阵

| 编号 | 二元验收标准 | 验证命令或人工步骤 | 必需 | 最终状态 | 证据 |
|---|---|---|---|---|---|
| A1 | 知乎参数、URL、额度、错误、响应上限和密钥脱敏有自动测试 | `uv run pytest tests/test_zhihu.py -q` | 是 | PASS | Zhihu+Web+Narration定向25项及全量203项通过；官方URL、无重试、2路并发、2 MiB边界和脱敏断言通过 |
| A2 | 知乎Web API受控制台鉴权保护并与既有Otto API共存 | `uv run pytest tests/test_web.py -q` | 是 | PASS | 全量回归通过；未授权状态为401，授权状态/工具/画像/查询/事件/朗读及稳定错误均覆盖 |
| A3 | 独立前端可复现构建且TypeScript无错误 | `cd ../webui && npm ci && npm run build` | 是 | PASS | 干净安装26个包、npm审计0漏洞；TypeScript和Vite 41模块构建成功 |
| A4 | Python静态入口能提供Forge首页及嵌套JS/CSS/模型/表情资源 | Web ASGI测试和 `tests/packaging/static_assets_smoke.py` | 是 | PASS | ASGI静态测试、源码smoke及macOS PyInstaller onefile实跑均返回ok |
| A5 | 后端完整静态检查与回归通过 | `uv run ruff check src tests && uv run mypy src && uv run pytest -q` | 是 | PASS | Ruff PASS；mypy 41个源码文件PASS；最终203 passed in 6.15s |
| A6 | 本机环境凭据通过官方额度探针，并完成一次最小只读查询 | 显式external smoke；输出仅状态、能力ID和计数，不输出密钥/原始内容 | 是 | PASS | 官方额度验证返回9组可用能力ID；最小`hot limit=1`返回1项，未输出原文或凭据 |
| A7 | 单Python Runtime启动后新首页、健康、设备和知乎状态可访问，停止后8081释放 | 本机HTTP smoke + 端口检查 | 是 | PASS | 新Runtime SIGINT完整关闭且8081/1883/8884释放；重启后Forge哈希JS、healthy、知乎401/授权状态及3台设备列表通过，2台在线 |
| A8 | Windows/macOS工作流都会重建前端并生成包含Web静态资源的PyInstaller烟测程序 | 工作流YAML结构检查、前端可复现构建、本机PyInstaller onefile实跑 | 是 | PASS | 双平台矩阵含Node重建/快照diff/`--collect-data otto_master`/可执行文件实跑；YAML与本机onefile PASS；远程执行归入Gate 6 push后确认 |
| A9 | 暂存区只含允许路径且无真实凭据 | `git diff --check`、受限密钥模式扫描、`git diff --cached --name-only` | 是 | PASS | 114条允许路径（112个当前blob、2个旧静态文件删除）；5个本地敏感值逐个扫描无命中；用户参考目录/ZIP/贴图未暂存；diff与便携路径检查PASS |

## 非目标

- 不迁移 `forge-radio/vendor/`、参考Node/Worker生产后端、假 `/api/v1` 设备状态或浏览器PCM小智桥。
- 不增加知乎发布、点赞、关注、cookie抓取、递归翻页或自动重试。
- 不修改ESP32固件、不改变MQTT/UDP/WebSocket设备协议、不宣称多设备并发语音或实体Windows整机验收完成。
- 不合并 `main`、不创建tag或正式release。

## 风险

- Forge参考控制台字段与Otto真实API不同，必须在前端适配，不能改变后端真实语义去迎合占位返回。
- 3D模型和贴图为嵌套大文件，构建与包数据规则必须递归收集并在Windows路径下验证。
- 知乎凭据已通过聊天提供，虽然不会进入仓库，正式发布前仍建议轮换。
- 官方能力按账号授权可能不同，前端必须依据额度探针显示不可用能力，不能伪报成功。

## 回滚点

- Server回滚：`test1.0@18181851401e8ebef516c71d847b76e924c27f26`
- 固件不变：`howtion0/otto codex/otto-portable@c4ad28e45adb5f565469d4c14b52aedcb74c1ffb`

## 计划验证

```text
cd /Users/howtion/liteserver/webui && npm ci && npm run build
cd /Users/howtion/liteserver/otto-master
uv run pytest tests/test_zhihu.py tests/test_web.py -q
uv run ruff check src tests
uv run mypy src
uv run pytest -q
uv run python tests/external/zhihu_smoke.py
uv run python tests/packaging/static_assets_smoke.py
git diff --check
```

## 测试—返工记录

| 轮次 | 测试 | 初始状态 | 失败摘要或阻塞证据 | 修复 | 重测结果 |
|---|---|---|---|---|---|
| 1 | 静态检查首次组合命令 | FAIL | 从`webui/`工作目录调用后端Ruff，路径不存在 | 回到`otto-master/`分别执行 | Ruff与mypy PASS |
| 2 | CI文件与前端禁用链只读审计 | FAIL | CI相对路径和前端源码层级先后写错；`rg`错误码被早期shell条件误判为无命中 | 从仓库根读取工作流，并先`test -d webui/app/src`再扫描 | 工作流内容确认；禁用链扫描PASS |
| 3 | Runtime HTTP脚本 | FAIL | 首次正则转义错误；后续把构建包相对`/v1`路径写成完整`/api/v1`，又用内部`state`而非公开`status`统计在线数 | 给断言加标签并按真实HTML/API字段修正 | Forge资源、健康、鉴权、知乎状态和3台设备/2台在线PASS |
| 4 | 暂存区差异检查 | FAIL | Vite生成JS内Three.js GLSL模板保留上游尾随空格；便携路径扫描又从错误工作目录运行 | 仅对生成JS路径设置Git `-whitespace`属性；从仓库根加目录断言重跑 | `git diff --cached --check`与便携路径扫描PASS |
| 5 | 最终本地门禁 | PASS | 无生产缺陷 | 不适用 | 锁文件、Ruff、mypy、203 pytest、前端构建、静态包/PyInstaller、真实知乎和Runtime闭环PASS |

## 实际结果

- 类型检查：Ruff与mypy strict PASS；TypeScript `tsc --noEmit` PASS
- 单元测试：最终203 passed in 6.15s；Zhihu+Web+Narration定向25 passed
- 集成测试：前端干净构建、源码与PyInstaller静态资源、真实知乎额度/最小查询、Runtime HTTP和优雅关闭均PASS
- 硬件测试：不要求；不下发动作
- 实际设备与传输：附加只读观察3台登记、EVA2/EVA3 MQTT online，不作为本轮动作或语音验收
- stop与idle清理：本轮未下发动作；Runtime烟测完成一次优雅关闭和端口释放，最终服务已重新启动

## Gate 5：文档收尾

- [x] `docs/DEV_PROGRESS.md` 已更新
- [x] `docs/MODULE_STATUS.md` 已更新
- [x] `docs/LOG.md` 已更新
- [x] 架构、控制台要求和协议合同与实现一致
- [x] 本轮未运行项及原因已如实记录

## Gate 6：测试支线上传

- 测试支线：`test1.1`
- 验收提交：待生成
- push结果：未执行
- 远程commit：待生成
- 本地HEAD与远程一致：否
- 本轮是否获单独授权合并main：否
- 本轮是否获单独授权tag或正式发布：否
