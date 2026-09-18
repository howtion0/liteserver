# Otto Master 强制施工门禁

本文档是每轮施工必须执行的核心流程，不是建议清单。除非用户明确修改本流程，否则任何Phase、功能、修复、重构和固件配套工作都必须按顺序通过全部门禁。

```text
GitHub远程备份
  → 阅读施工文档
  → 写本轮施工契约与通过标准
  → 施工
  → 测试
      ├─ 失败：返工并重测
      └─ 通过：继续
  → 更新进度、模块状态和施工日志
  → 精确提交并上传GitHub
  → 验证远程分支与本地提交一致
```

禁止跳过、倒序执行或把“计划运行”“理论可行”“以前通过”写成“本轮通过”。

## Gate 0：GitHub远程备份

任何实现文件修改前，必须先确认当前可恢复基线已经存在于GitHub。

最低检查：

```bash
git fetch origin --prune
git branch --show-current
git status --short
git log -1 --oneline
git branch -a
git ls-remote --heads origin
```

有效GitHub备份必须同时满足：

1. 当前已验收基线有明确的 `testN.N` 支线。
2. 该支线已经push到 `origin`，不能只有本地commit、stash、zip或未跟踪文件。
3. 本地检查点commit与远程支线指向同一对象。
4. `docs/LOG.md` 和 `docs/DEV_PROGRESS.md` 能说明该检查点的状态。
5. 备份不包含 `.env`、API Key、数据库、日志、固件二进制或无关用户改动。

如果当前基线尚未上传：

- 停止新施工。
- 按 `CODEX_RULES_GIT.md` 创建下一个 `testN.N` 支线。
- 完成当前基线应有的验证和日志。
- 精确暂存、提交、push，并验证远程哈希。
- 只有备份成立后，才能进入下一轮施工。

如果工作区有来源不明或不属于本轮的改动，不得擅自打包进备份；先报告范围并隔离本轮路径。禁止用reset、clean或强制切换制造“干净工作区”。

## Gate 1：完整阅读施工依据

备份通过后，按顺序完整阅读：

1. `AGENTS.md`
2. `ONBOARD.md`
3. `CODEX_CONSTRUCTION_WORKFLOW.md`
4. `CODEX_MASTER_REQUIREMENTS.md`
5. `CODEX_ARCHITECTURE.md`
6. `docs/DEV_PROGRESS.md`
7. 当前Phase的 `docs/CONSTRUCTION_PLAN.md`
8. 与本轮相关的合同，例如 `docs/MESSAGE_CONTRACTS.md`、`docs/DEVICE_TRANSPORT_CONTRACT.md`、`docs/MQTT_CONTROL_CONTRACT.md`；Phase 5还必须完整阅读 `docs/VOLCENGINE_SPEECH_INTEGRATION.md`
9. `CODEX_RULES_TESTING.md` 和 `CODEX_RULES_GIT.md`

不得依赖聊天记忆替代文件内容。发现文档互相冲突时，先修正文档和记录决策，不得一边猜测一边实现。

## Gate 2：冻结本轮计划和测试通过标准

施工前必须从 `CODEX_SESSION_CONTRACT_TEMPLATE.md` 建立本轮Session Contract。没有书面契约，不得修改实现代码。

契约必须包含：

- 当前版本、Phase和 `testN.N` 支线。
- GitHub基线分支及本地/远程commit。
- 本轮唯一目标、明确非目标和允许修改的路径。
- 输入、外部依赖、EVA设备、固件版本和实际传输。
- 风险、回滚点和机器人安全清理方式。
- 每条验收标准对应的具体测试命令或人工观测方法。
- 必须运行的单元、集成、硬件、云API和跨平台检查。

通过标准必须二元可判定。例如：

```text
无效：MQTT基本可用
有效：EVA1和EVA2分别返回14个动作；EVA1执行swing时EVA2状态不变；stop后10秒内回到idle
```

本轮中途扩大范围时，必须先更新Session Contract、风险和测试矩阵，再继续施工。

## Gate 3：按契约施工

- 只修改Session Contract列出的路径和职责。
- 先实现最小闭环，再扩展边界条件。
- 不顺手重构无关模块，不覆盖用户修改。
- 新发现的协议差异、依赖变化或硬件限制先写入合同/架构文档。
- 外部协议通过Gateway进入，业务模块不得绕过Message Bus或Dispatcher。
- 真机动作优先使用低风险原地动作；测试结束必须stop并确认idle。

## Gate 4：测试—返工闭环

施工完成后按 `CODEX_RULES_TESTING.md` 运行本轮约定测试。

状态只有四种：

| 状态 | 含义 | 是否允许完成Phase |
|---|---|---|
| PASS | 本轮要求的检查全部真实通过 | 是 |
| FAIL | 至少一项运行后失败 | 否 |
| BLOCKED | 外部依赖、硬件或权限使测试无法继续 | 否 |
| NOT RUN | 尚未运行 | 否 |

任何一项FAIL时必须：

1. 保存失败命令、关键错误和环境。
2. 定位根因，不通过删除断言、放宽参数或跳过测试掩盖问题。
3. 修复实现或修正错误合同。
4. 重新运行原失败测试。
5. 运行受影响的回归测试。
6. 重复直到所有必需项PASS。

禁止行为：

- 把旧版本、TCP链路或另一台设备的成功当成本轮MQTT成功。
- 用“看起来正常”代替命令结果、状态变化或日志证据。
- 因测试困难而删测试、改成skip或降低验收标准。
- 必需测试未运行时提升版本、标记Phase完成或合并main。

如果确实BLOCKED，必须记录阻塞证据并保持Phase未完成。只有用户明确要求结束本轮时，才允许将阻塞状态作为独立检查点上传；阻塞支线不得合并到main，恢复施工时使用下一个 `testN.N`。

## Gate 5：完成文档闭环

所有必需测试PASS后，提交前必须更新：

1. `docs/DEV_PROGRESS.md`：真实版本、Phase和下一步。
2. `docs/MODULE_STATUS.md`：每个受影响模块的真实实现度。
3. `docs/LOG.md`：目标、改动、失败返工、最终测试结果、风险和回滚点。
4. 本轮Session Contract：实际命令、结果、设备、固件、传输和清理状态。
5. 架构或消息合同：如果实现产生了已批准的合同变化。

日志必须区分：

- 本轮实际通过。
- 用户提供的历史基线。
- 未运行。
- 已知但未解决的风险。

## Gate 6：精确提交并上传GitHub

完成文档闭环后执行：

1. 检查 `git status` 和目标文件diff。
2. 检查密钥、数据库、日志、固件和无关文件没有进入暂存区。
3. 只暂存Session Contract允许的路径，禁止 `git add .` 和 `git add -A`。
4. 在当前全新 `testN.N` 支线创建唯一一次验收提交。
5. push同名支线到GitHub，不直接push `main`。
6. 使用 `git ls-remote --heads origin testN.N` 验证远程支线存在。
7. 比较本地 `git rev-parse HEAD` 与远程哈希，必须完全一致。
8. 向用户报告支线、commit、测试结果和未运行项。

普通施工请求视为对上述“备份检查点、创建test支线、阶段提交和push”的持续授权；merge、tag、release、force-push和删除远程分支仍需用户明确授权。

权限边界：

| 操作 | 是否自动执行 |
|---|---|
| fetch并检查GitHub基线 | 是 |
| 创建下一个 `testN.N` 支线 | 是 |
| 测试全部PASS后的阶段commit | 是 |
| push同名测试支线并核对远程哈希 | 是 |
| 合并到 `main` | 否，必须单独授权 |
| 创建或推送tag | 否，必须单独授权 |
| GitHub Release或其他正式发布 | 否，必须单独授权 |
| force-push、删除远程分支、改写历史 | 否，必须单独授权 |

这里的“上传GitHub”默认只表示push测试支线，不表示合并或正式发布。

只有同时满足以下条件才算本轮完成：

```text
远程基线已备份
AND 施工契约已冻结
AND 所有必需测试PASS
AND 施工日志已更新
AND testN.N已push
AND 本地与远程commit一致
```

少一项都只能报告进行中、失败或阻塞，不能报告“完成”。
