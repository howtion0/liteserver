# Git协作与回滚规则

## 1. 仓库信息

```text
remote: git@github.com:howtion0/liteserver.git
default branch: main
project directory: otto-master/
```

## 2. 修改前检查

在已经初始化Git的环境中，先执行：

```bash
git branch --show-current
git status --short
git diff -- otto-master
```

确认当前分支、已有用户改动和本次工作范围。已有改动默认属于用户，不得清除、覆盖或顺手整理。

## 3. 迭代支线

### 3.1 强制规则

- 每次形成可提交的阶段检查点，都必须先建立一个全新的迭代支线。
- 支线只使用 `testN.N` 格式，从 `test0.1` 开始按 `0.1` 自然递增：`test0.1`、`test0.2`、……、`test0.9`、`test1.0`、`test1.1`。
- 不再使用 `docs/...`、`fix/...`、`feat/...` 或 `refactor/...` 等并行命名体系。
- 一个支线只承载一个阶段检查点和一次最终验收提交。开发中的未完成修改可以保留在工作区，但不得提前产生零散提交。
- 如果已完成检查点后还需要追加修复，必须使用下一个 `testN.N` 支线，不得在旧支线追加提交或改写历史。
- 支线编号只表示施工先后，不表达语义化版本含义。`test0.9` 自然递增到 `test1.0`，不代表产品自动成为 `1.0.0`。
- 支线名称不得复用。失败或废弃的支线也占用编号，并在 `docs/LOG.md` 记录原因和后继支线。
- 只读检查不创建支线。用户要求开始施工时，自动执行门禁要求的GitHub基线备份、创建支线、阶段commit和push，无需每轮再次授权。

### 3.2 基线与合并

1. 施工前先执行 `CODEX_CONSTRUCTION_WORKFLOW.md` Gate 0，确认上一可恢复检查点已在GitHub且远程哈希正确。
2. 创建前fetch，并同时检查本地分支、远程分支和 `docs/LOG.md`；使用所有已占用编号之后的下一个编号，不凭记忆猜测。
3. 新支线从 `main` 的最新已验收状态创建，不从更早的测试支线继续分叉。
4. `main` 禁止直接提交，只接收已经验收的 `testN.N` 支线。
5. 合并前必须更新阶段文档并完成该阶段全部必需验证；FAIL、BLOCKED或NOT RUN都禁止合并。
6. 合并使用fast-forward或squash，使 `main` 每个检查点只增加一个可追踪提交，不产生无意义的合并噪声。
7. 合并后 `main` 表示最新稳定检查点；下一个支线再从合并后的 `main` 创建。
8. 远程测试支线默认保留作为阶段检查点，只有用户明确要求时才删除。

创建示例：

```bash
git fetch origin --prune
git switch main
git pull --ff-only
git switch -c test0.1
```

如果工作区存在未提交内容，不得为了执行上述命令而强制切换、清理或隐藏用户修改；应先报告状态并确定本轮文件归属。

### 3.3 计划编号

以下计划已经包含当前仓库的一次性历史恢复检查点。若中间增加补充修复，后续继续使用实际的下一个编号，不回填、不跳回。

| 检查点 | 计划支线 |
|---|---|
| Phase 0 + Phase 1恢复基线 | `test0.1` |
| Phase 2 SQLite | `test0.2` |
| Phase 3 Web/OTA/mDNS | `test0.3` |
| Phase 4 ESP32连接与Session | `test0.4` |
| Phase 5 Opus/ASR/TTS | `test0.5` |
| Phase 6 WakeGate | `test0.6` |
| Phase 7 LLM/Dispatcher/动作 | `test0.7` |
| Phase 8 集群/日志/容错 | `test0.8` |
| Phase 9 Windows与端到端验收 | `test0.9` |
| MVP验收 | `test1.0` |

### 3.4 历史未备份恢复规则

如果启用本规则时，工作区已经包含多个已完成阶段，但这些阶段从未形成远程检查点：

1. 不得伪造过去的分支、提交时间或拆分出无法证明的历史。
2. 暂停下一Phase施工，先审查当前文件归属并重跑当前最高已完成阶段的全部必需测试。
3. 使用下一个未占用的 `testN.N` 建立“恢复基线”，在Session Contract和 `docs/LOG.md` 明确它合并包含了哪些历史阶段。
4. 恢复基线测试全部PASS后，自动创建一次验收commit并push；远程哈希一致后才允许下一Phase施工。
5. 下一施工阶段继续使用再下一个编号，不回头补造分支。

当前仓库适用一次性迁移：Phase 0和Phase 1都尚未形成GitHub检查点，因此 `test0.1` 用作“Phase 0 + Phase 1恢复基线”；Phase 2使用 `test0.2`，后续继续自然递增。

## 4. 提交规则

- 每个 `testN.N` 支线只允许一个阶段验收提交；提交后如需继续修改，开启下一个支线。
- 禁止对已发布检查点使用 `commit --amend`、rebase改写或force-push。
- 不使用 `git add .` 或 `git add -A` 混入无关文件。
- 只暂存本次明确修改的路径。
- 提交信息说明目的和范围，不写虚假的测试结果。
- 不提交 `.env`、数据库、JSONL日志和固件二进制。
- 必需测试全部PASS、日志完成后，自动commit并push测试支线；不得自动合并、tag、release、force-push或删除远程分支。

授权边界固定如下：

- 自动执行：fetch、基线核对、创建下一测试支线、验收commit、push测试支线、核对远程哈希。
- 必须单独授权：合并main、创建或推送tag、GitHub Release或其他正式发布、force-push、删除远程分支、任何历史改写。
- “提交支线”只表示对 `testN.N` 的commit和push，不等于批准合并或发布。

推荐提交格式：

```text
testN.N: <phase or checkpoint purpose>

Scope:
- <module or docs>

Validation:
- <actual commands and results>
```

例如：

```text
test0.2: build sqlite persistence

Scope:
- database lifecycle
- migrations and repositories
- redacted message persistence

Validation:
- pytest tests/test_storage.py: passed
- ruff check src tests: passed
```

## 5. GitHub验收规则

- Pull Request标题使用 `[testN.N] <phase or checkpoint purpose>`。
- PR正文必须列出目标、实际改动、已运行验证、未运行验证、风险和回滚方法。
- PR目标分支固定为 `main`，不得让测试支线互相合并。
- 只要有必需测试FAIL、BLOCKED或NOT RUN，PR不得标为ready、不得合并。
- 如果PR验收后需要继续修改，关闭或标记旧PR为superseded，从 `main` 创建下一个 `testN.N` 支线重新提交完整检查点。
- GitHub应为 `main` 开启禁止force-push、禁止删除和必须通过PR合并；进入Phase 1后再把跨平台测试加入required status checks。

## 6. 回滚原则

优先最小范围、可恢复的回滚：

1. 修复当前文件。
2. 恢复本轮明确修改的文件。
3. 使用新提交反向修复。
4. 只有用户明确授权时才使用可能丢失工作区内容的操作。

禁止默认使用：

```text
git reset --hard
git clean -fd
git checkout -- <broad path>
```

回滚后要重新运行相同验证，并在 `docs/LOG.md` 记录原因。

## 7. Phase收尾

Phase状态发生变化时：

1. 更新 `pyproject.toml` 版本。
2. 更新 `docs/DEV_PROGRESS.md`。
3. 更新 `docs/MODULE_STATUS.md`。
4. 更新 `docs/LOG.md`。
5. 运行阶段要求的验证。
6. 在日志和Session Contract中记录本轮 `testN.N` 支线。
7. 全部必需测试PASS后，确认该支线尚无阶段提交，再执行精确暂存和唯一一次commit。
8. 自动push同名测试支线，并验证远程哈希与本地HEAD一致。
9. 只有用户明确授权时，才合并到 `main`、创建tag或正式发布。
