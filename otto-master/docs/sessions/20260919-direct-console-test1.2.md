# Otto Master Session Contract：免令牌直控与动作目录自恢复

## 基本信息

- 日期：2026-09-19
- 基线版本：`test1.1@fd0cfff491e54188383c7023e7a516c252f03eca`
- Git迭代支线：`test1.2`
- 用户决策：家庭局域网内优先简单可用，WebUI默认不要求控制令牌

## 本轮目标

- Otto Master默认使用免令牌直控模式，打开同源WebUI即可操作设备和使用私有控制API。
- 保留可选的控制台鉴权开关，供未来跨不可信网络部署时显式启用。
- 保留同源/允许来源检查；设备MQTT凭据、OTA配网令牌和云API密钥边界不变。
- 在线设备动作目录为空时，WebUI自动执行只读设备验证并重新读取目录，避免EVA2/EVA3按钮永久置灰。

## 允许修改的路径

- `webui/**`
- `otto-master/.env.example`
- `otto-master/config.yaml`
- `otto-master/src/otto_master/config.py`
- `otto-master/src/otto_master/gateways/web.py`
- `otto-master/src/otto_master/web/**`（前端构建快照）
- `otto-master/scripts/**`
- `otto-master/tests/**`
- `otto-master/docs/**`
- `otto-master/CODEX_ARCHITECTURE.md`
- `otto-master/README.md`
- 根目录 `README.md`
- `.github/workflows/otto-master-phase3.yml`（仅在验收需要时）

用户参考ZIP、`forge-radio/`、看山贴图、`.env`、数据库、日志、固件和其他未跟踪文件均不修改或提交。

## 安全边界

- 默认直控意味着能访问Server监听地址的客户端可调用控制API；适用边界是用户当前可信家庭局域网。
- 浏览器跨来源请求仍按同源和`allowed_origins`拒绝，不能因关闭令牌而关闭Origin校验。
- `ota.provisioning_token_env`继续独立保护配网/OTA接口。
- MQTT主密码、火山、DeepSeek和知乎密钥仍只从本地环境读取，禁止进入响应、日志、文档和Git。
- 动作仍必须经过稳定`device_id`、在线状态、动作目录、参数Schema、显式确认、Dispatcher和ACK/终态门禁。

## 二元验收标准

| 编号 | 标准 | 验证 | 状态 |
|---|---|---|---|
| A1 | 默认配置下，控制API无`Authorization`也可使用 | Web集成测试与本机HTTP烟测 | PASS |
| A2 | 默认直控仍拒绝不在允许列表中的跨来源请求 | Web集成测试 | PASS |
| A3 | 显式开启控制台鉴权后，无令牌401、正确令牌通过 | Web集成测试 | PASS |
| A4 | 默认WebUI不显示令牌输入或“控制未授权”，设备/对话控件直接可用 | TypeScript构建与静态快照断言 | PASS |
| A5 | 在线设备动作目录为空时只读verify自动触发，目录刷新后动作按钮可用 | 静态行为断言及本机只读烟测 | PASS |
| A6 | 自动恢复目录本身不下发动作；改动完成后按用户授权让在线EVA2、EVA3各短距离前进一次，以命令`completed`且设备回到`idle`为通过 | 真实设备门禁与命令记录 | PASS |
| A7 | Ruff、mypy、完整pytest、前端干净构建和静态包烟测通过 | 本地完整门禁 | PASS |
| A8 | macOS/Windows GitHub矩阵通过，提交和远端`test1.2`一致 | push后CI | 待push后确认 |
| A9 | 桌面源码ZIP不含密钥/运行数据，独立mode-0600 TXT含云密钥和可恢复的现有EVA MQTT身份，Windows脚本与指南齐全；不生成EXE | ZIP清单、密钥扫描、导出测试和PowerShell CI语法检查 | 本地导出/审计PASS，PowerShell远程语法待CI |

## 非目标

- 不修改ESP32固件、mDNS、MQTT/UDP/WebSocket协议或语音链路。
- 除用户已明确授权的EVA2、EVA3各一次短距离前进验收外，不自动下发其他动作。
- 不删除可选安全模式实现，不把配网令牌或第三方API密钥改成免鉴权。
- 不合并`main`、不打tag或发布正式版本。

## 回滚点

- Server/WebUI：`test1.1@fd0cfff491e54188383c7023e7a516c252f03eca`
- 固件：本轮不变

## 最终结果

- 后端默认`console_auth_required=false`；无Authorization的设置、对话、verify和动作请求可用。可选安全模式继续验证Bearer，未配置令牌时503失败关闭。
- Origin边界保留并加固：白名单、环回、私网IP和`.local`同主机来源可用；首轮测试发现任意`Origin == Host`可能接受外部域名，已收窄并由恶意Host回归证明返回403。
- Forge总览默认显示“直接控制”，设置页默认不渲染令牌表单；命令页对在线空目录执行只读verify/refetch并在失败时显示设备名。
- Runtime重启后EVA2/EVA3均online，动作目录从0恢复为各15项。无Authorization、带同源Origin的一步walk命令分别为`fcbe303e-964c-489d-ad61-530e4183bc26`和`8f3f4aa2-4286-435e-a7eb-78d488f05f2d`，均`published → moving → completed`，最终`idle`且`sound_busy=false`。
- 本地门禁：`207 passed in 6.52s`；Ruff、mypy strict（41个源码文件）、锁文件、npm干净安装、TypeScript/Vite 41模块、npm 0漏洞、静态资源smoke和差异检查通过。首轮把Vite输出清理与pytest并行造成一次瞬时首页404，按构建后测试的正式顺序串行复验全绿。
- 固件、设备协议、云端语音/LLM、配网令牌和密钥配置未修改。Git提交、push及macOS/Windows CI待本轮最后收口。
- 新增Windows源码运行指南、密钥恢复/bootstrap/verify/run PowerShell脚本和无回显密钥导出工具；桌面交付明确只有提交树源码ZIP与独立密钥TXT，不生成EXE。TXT已按0600生成，ZIP在最终Git审计后由同一提交导出。
