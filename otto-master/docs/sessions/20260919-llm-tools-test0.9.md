# test0.9 DeepSeek工具桥与EVA1动作闭环

## 范围

- 日期：2026-09-19
- 工作支线：`test0.9`
- 目标设备：EVA1 `e072a1f71184`
- 排除设备：EVA2 `aca704ed89a8`由用户关机，本轮不触碰、不计双机通过
- 前序记录：`docs/sessions/20260919-voice-loop-test0.9.md`
- 本轮目标：让ASR final后的DeepSeek流既能返回可朗读文本，也能返回一个受控机器人动作；动作必须复用现有Dispatcher并以真实终态为准

## 与原小智后端的对应关系

原小智链路由设备声明MCP工具，后端把工具转换成LLM function；模型返回tool call后，后端再发MCP `tools/call`给设备。Otto Master已经有更严格的动作目录、设备Session、持久命令仓库和Dispatcher，因此本轮保留同样的“模型只产生结构化意图”原则，但不新增第二套MCP设备控制面：

```text
EVA动作目录/能力
  → RobotToolBridge收窄Schema
  → DeepSeek tools + tool_choice=auto
  → 流式tool_calls增量组装
  → 当前语音device_id锁定 + 参数二次校验
  → 既有Dispatcher
  → MQTT精确down Topic
  → accepted/moving/idle
  → 持久命令completed
```

固件原生MCP名如`self.otto.walk_forward`包含点号；DeepSeek函数名使用兼容字符集，因此云端名规范化为`self_otto_walk_forward`，只在Server内部映射回动作`walk`。固件的`walk_forward`本身使用`direction=1/-1`表达前进/后退，`turn_left`同理表达左转/右转，本轮保持该语义。

## 实现

### Cloud与LLM流

- `ChatToolDefinition`验证名称、描述和object JSON Schema，单请求最多32个且名称唯一。
- DeepSeek SSE同时规范化文本、finish reason和按index编号的tool call delta。
- LLM Service继续使用有界生产者/消费者队列，文本按句输出；工具模式只允许一个调用。
- 混合文本/工具、多个调用、变化的call ID、未提供工具、4 KiB以上参数、重复JSON键、NaN/Infinity、过深或过多节点全部以稳定协议错误拒绝。
- 成功文本轮继续保存短历史；工具轮暂不进入普通`ChatMessage`历史，因为正确历史需要结构化assistant tool_calls和tool result，不能用“已执行”文本伪造。

### RobotToolBridge

- 工具只来自当前设备的online/enabled快照、能力和动作目录。
- 暴露`walk/turn/jump/swing/moonwalk/bend/shake_leg/updown/tiptoe_swing/jitter/ascending_turn/crusaito/flapping/laugh/stop`中设备实际支持的项目。
- 明确排除`home`、舵机校准和配置写入。
- 普通动作步数最多10，jump最多3；速度不低于700 ms；幅度最多80度；方向仅允许`-1/1`。
- `device_id`、transport、confirmation、Topic和command ID均由Server生成；模型参数中不存在这些字段。
- command ID由设备、utterance、tool call ID和名称确定性生成，同一调用重复进入时由持久仓库幂等处理。
- 只有Dispatcher记录`completed`才返回成功；失败、取消或完成超时对动作请求安全stop。

### WakeGate

- 先把用户final文字发到同一设备屏幕，再启动LLM。
- 首个流项目是文本时继续走流式TTS；首个流项目是工具时必须完整耗尽并确认没有第二个调用。
- 工具成功不追加“好的/已完成”等TTS，也不允许模型文本冒充动作结果。
- 工具失败记录稳定错误并只播放固定失败提示。
- 显式`laugh`真实完成后直接复用为下一条问题的笑声门禁，不连续提交第二次笑声。
- 发布`voice.tool.requested|completed|failed`，参数权威记录仍在Dispatcher命令payload。

## 自动验证

新增或扩展覆盖：

- DeepSeek请求中的tool schema、tool choice及SSE碎片解析。
- 单个碎片化调用组装、严格JSON、混合模式和多调用拒绝。
- 动作目录收窄、参数上限、目标设备锁定、完成等待、失败与取消安全stop。
- WakeGate文本流、工具成功无TTS、失败固定提示和显式笑声不重复。
- 连续工具结果不污染下一轮普通文本历史。

最终门禁：

```text
uv lock --check                 PASS
uv run ruff check src tests     PASS
uv run mypy src                 PASS（37个源码文件）
uv run pytest -q                PASS（144项）
```

## 真DeepSeek与EVA1证据

1. 不连接设备的真实API烟测要求“大笑”，DeepSeek返回唯一`self_otto_laugh {}`，证明生产SSE格式与本地增量解析一致。
2. EVA1只读verify通过且idle后，真实DeepSeek再次选择`self_otto_laugh`；RobotToolBridge映射为`laugh`，Dispatcher等待设备真实`completed`。
3. 首版连续动作验收中，两次独立脚本都完成了`walk {steps:1,direction:1}`，随后第二轮后退在下发前被“必须唯一工具调用”门禁拦截。持久命令记录确认只有两次前进，没有未确认后退。
4. 根因是首版把上一工具结果写成普通assistant文本。修复并通过回归后，真实连续序列先完成`self_otto_laugh`，第二轮再返回`self_otto_walk_forward {steps:2,direction:-1}`并完成；该后退两步用于补偿前两次前进一步。
5. 最终Device Verifier确认EVA1为idle。整个工具成功判定来自持久命令生命周期，不来自模型措辞或单个ACK。

## 安全结论

- 模型不能改目标设备，EVA2未上线也未收到命令。
- 模型不能绕过动作目录、参数边界、confirmation或Dispatcher。
- 一轮最多执行一个工具；“先前进再后退”不会拆成两个未经确认的并行动作。
- 工具失败不会口头声称完成，取消中的动作会请求stop。
- API Key、MQTT凭据和Authorization头未写入文档、事件或Git差异。

## 当前暂停点

- 常驻Otto Master：HTTP `8081`、MQTT `1883`、UDP `8884`，健康。
- 诊断/OTA服务：HTTP `8080`，保留运行。
- EVA1：固件2.0.11、MQTT online；最近一次主动只读验证为idle。常驻Runtime重启后的内存动作快照会保持unknown，直到下一次状态查询，这是安全默认值。
- EVA2：关机，本轮未触碰。
- Git：配套固件已推送`howtion0/otto`的`codex/otto-portable@abb769f1d0f3d1c03fb7a6106bd7288f31c66a98`；Otto Master在本记录完成后提交并推送`test0.9`，正式CI结果以GitHub分支检查为准。根目录用户`README.md`保持不动。

## 下一轮

1. 用户在EVA1按键进入循环对话，真实口述“大笑一下”“向前走一步”“向后退一步”。
2. 同时确认用户ASR文字上屏、成功工具无额外TTS、动作completed后下一轮笑声门禁完整且按钮可退出。
3. EVA2开机后，分别从两台设备发起工具回合，验证语音session锁定与音频/动作不串设备。
4. 再完成WebSocket真机Profile、`test0.9`正式CI和Windows/PyInstaller Opus门禁。
