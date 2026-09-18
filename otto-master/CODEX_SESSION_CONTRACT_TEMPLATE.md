# Otto Master Session Contract

每轮施工必须复制本模板形成独立记录，建议路径为：

```text
docs/sessions/YYYYMMDD-phaseN-testN.N.md
```

没有填写GitHub基线、阅读清单、范围和验收矩阵前，不得修改实现代码。

## 基本信息

- 日期：
- 当前版本：
- 当前Phase：
- Git迭代支线：`testN.N`

## Gate 0：GitHub基线

- 基线分支：
- 本地commit：
- 远程commit：
- 本地与远程一致：否 / 是
- 当前工作区已有改动及归属：
- 密钥、数据库、日志、固件和无关改动已排除：否 / 是

## Gate 1：施工依据已阅读

- [ ] `AGENTS.md`
- [ ] `ONBOARD.md`
- [ ] `CODEX_CONSTRUCTION_WORKFLOW.md`
- [ ] `CODEX_MASTER_REQUIREMENTS.md`
- [ ] `CODEX_ARCHITECTURE.md`
- [ ] `docs/DEV_PROGRESS.md`
- [ ] 当前Phase的 `docs/CONSTRUCTION_PLAN.md`
- [ ] 本轮相关消息或协议合同
- [ ] `CODEX_RULES_TESTING.md`
- [ ] `CODEX_RULES_GIT.md`

## 本轮目标

-

## 涉及模块

-

## 允许修改的路径

-

## 输入与外部依赖

- 是否需要ESP32：否 / 是
- 是否需要真实云API：否 / 是
- 是否需要局域网：否 / 是
- 是否需要Windows验证：否 / 是
- 是否需要MQTT Broker：否 / fake / 内嵌真Broker
- 是否需要EVA真机：否 / EVA1 / EVA2 / EVA1+EVA2
- 固件版本：
- 预期传输：mqtt / tcp / websocket
- 安全清理：结束时是否stop并确认idle

## 验收标准

- [ ]

## 验收矩阵

| 编号 | 二元验收标准 | 验证命令或人工步骤 | 必需 | 最终状态 | 证据 |
|---|---|---|---|---|---|
| A1 |  |  | 是 | NOT RUN |  |

## 非目标

-

## 风险

-

## 回滚点

-

## 计划验证

```text
命令或人工验证步骤
```

## 测试—返工记录

| 轮次 | 测试 | 初始状态 | 失败摘要或阻塞证据 | 修复 | 重测结果 |
|---|---|---|---|---|---|
| 1 |  | NOT RUN |  |  | NOT RUN |

## 实际结果

- 类型检查：未运行 / 通过 / 失败
- 单元测试：未运行 / 通过 / 失败
- 集成测试：未运行 / 通过 / 失败
- 硬件测试：未运行 / 通过 / 失败
- 实际设备与传输：
- stop与idle清理：未运行 / 通过 / 失败

## Gate 5：文档收尾

- [ ] `docs/DEV_PROGRESS.md` 已更新
- [ ] `docs/MODULE_STATUS.md` 已更新
- [ ] `docs/LOG.md` 已更新
- [ ] 架构和协议合同与实现一致
- [ ] 本轮未运行项及原因已如实记录

## Gate 6：测试支线上传

- 测试支线：
- 验收提交：
- push结果：未执行 / 通过 / 失败
- 远程commit：
- 本地HEAD与远程一致：否 / 是
- 本轮是否获单独授权合并main：否 / 是
- 本轮是否获单独授权tag或正式发布：否 / 是

说明：测试支线的创建、验收commit和push由施工请求持续授权；合并main、tag、正式发布、force-push和删除远程分支必须另行授权。
