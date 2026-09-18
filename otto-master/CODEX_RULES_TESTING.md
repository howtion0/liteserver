# 测试规则

## 1. 总原则

- 测试强度与改动风险匹配。
- 先运行最小相关测试，再扩大范围。
- 不允许把未运行写成通过。
- 测试失败时记录命令、失败摘要和处理结果。
- 硬件测试与纯Python测试分开报告。

### 1.1 强制测试门禁

- 修改实现前，必须在本轮Session Contract中把每条验收标准映射到具体测试命令或人工观测步骤。
- 每项结果只能是 `PASS`、`FAIL`、`BLOCKED` 或 `NOT RUN`；只有全部必需项为 `PASS` 才能完成Phase、创建验收提交并push测试支线。
- 历史成功、其他协议成功、另一台设备成功和“代码看起来正确”都不能替代本轮验证。
- 任一必需测试为 `FAIL` 时，必须保留失败证据、定位根因、修复、重跑原失败测试，再运行受影响回归测试。
- 禁止通过删除断言、放宽验收值、改成skip、隐藏错误或降低测试范围来制造通过。
- 任一必需测试为 `BLOCKED` 或 `NOT RUN` 时，Phase保持未完成；除非用户明确要求保存阻塞现场，否则不得创建阶段验收提交。
- 测试结果、失败返工次数和最终证据必须同时写入Session Contract和 `docs/LOG.md`。

强制闭环：

```text
运行必需测试
  ├─ PASS：进入文档收尾
  ├─ FAIL：记录 → 定位 → 修复 → 重跑原测试 → 回归测试
  ├─ BLOCKED：记录证据，保持Phase未完成
  └─ NOT RUN：继续执行，不得收尾
```

## 2. Phase 0：文档脚手架

只验证：

- 请求的目录和文件存在。
- Python和Web占位文件没有业务实现。
- YAML和TOML能被解析。
- 文档名称和架构术语一致。
- `.env`、数据库、日志和固件规则与 `.gitignore` 一致。

Phase 0 不运行不存在的应用测试。

## 3. Phase 1以后默认顺序

```text
1. 配置解析或静态结构检查
2. ruff check src tests
3. mypy src
4. 受影响pytest
5. 跨模块集成测试
6. 必要时完整pytest
7. 必要时硬件冒烟测试
```

## 4. 测试层次

### 单元测试

- Message序列化和校验。
- Message Bus投递、多个订阅者、异常隔离。
- WakeGate状态和超时。
- Dispatcher目标解析和广播保护。
- SQLite迁移和Repository。

### 集成测试

- 内嵌MQTT Broker启动、鉴权、ACL和优雅关闭。
- MQTT Topic到内部Message的双向映射。
- 相同命令ID重复投递时只执行一次。
- MQTT断线、重连、超时和非retain动作。
- TCP首帧身份认证、帧上限、连接替换与断线降级。
- WebSocket hello、设备身份、断线重连与不支持协议版本拒绝。
- 二进制Opus帧与JSON帧分流，音频引用按device、utterance、sequence和frame_ref隔离。
- ASR → WakeGate → TTS回应。
- WebUI命令 → Dispatcher → 模拟设备。
- OTA清单和固件下载。

### 硬件测试

- EVA1/EVA2同时在线。
- MQTT分别查询两台设备的14个动作。
- EVA1与EVA2分别完成 `swing → stop → idle`，另一台状态不变。
- MQTT相同命令ID重复投递不能导致动作执行两次。
- Broker重启后两台设备恢复连接和查询能力。
- 只唤醒被点名设备。
- 播放“我在”时不回灌麦克风。
- 单设备前进和集群广播结果正确。
- Windows防火墙和mDNS环境下可发现服务。

### EVA1/EVA2局域网基线

```text
master: master.local
firmware: 2.0.5
EVA1当前IP: 192.168.172.127
EVA2当前IP: 192.168.172.117
```

这些IP只用于本轮诊断记录，不得写进程序、Topic或永久测试断言。设备选择必须使用MAC派生device_id，测试报告同时展示设备名称。

已验证的TCP行为基线包括：两台设备同时上线、WebUI选设备和动作、各返回14个动作、`swing`、`stop`以及 `moving → idle`。MQTT测试必须重新完成相同步骤，不能继承TCP的“通过”结果。

硬件测试规则：

- 使用 `@pytest.mark.hardware`；MQTT真机用例再加 `@pytest.mark.mqtt`。
- 默认 `pytest` 排除硬件和真实云API用例。
- 开始前确认机器人周围安全、设备电量和固件版本。
- 优先使用原地 `swing`，不在无人确认时执行walk、jump或大幅度动作。
- 测试清理必须向EVA1和EVA2发送stop，并查询到idle。
- 记录每条命令的device_id、名称、命令ID、传输、ACK、最终状态和耗时。
- 任一设备未上线时，测试应skip或明确失败原因，不得改用固定IP绕过发现流程。

## 5. 云API测试

- 默认单元测试使用fake provider，不消耗真实API额度。
- 真实API测试必须显式标记，例如 `@pytest.mark.external`。
- 无key、超时、限流和返回异常都必须有行为测试。
- 唤醒链路遇到API失败必须保持fail-closed。
- 火山ASR外部测试必须记录资源ID、请求ID、首个partial延迟、final延迟和最终文本，但不得记录API Key、认证头或原始音频。
- 火山TTS外部测试必须验证HTTP状态、服务事件码、首音频延迟、PCM参数、60 ms Opus帧数量和首帧可解码。
- ASR 2.0返回403时不得自动降级并伪报成功；只有配置明确允许时才使用已经验收的ASR 1.0资源。
- 真实云烟测成功不等于设备闭环通过；EVA1/EVA2隔离、实际播放和回到listening必须单独验收。
- ASR/TTS测试生成的临时密钥文件和音频必须使用受限权限，并在 `finally` 清理；仓库状态检查不得出现测试产物。
- Phase 5在macOS与Windows都必须运行Opus编码/解码和fake Provider测试；实体Windows/PyInstaller还需验证 `libopus` 动态库收集。

## 6. 并发测试

- 同一设备动作必须保持顺序。
- 不同设备允许并发。
- 慢订阅者不能永久阻塞消息总线。
- SQLite写入高峰不能产生未处理的database locked错误。
- 关闭时在途任务必须有明确完成或取消结果。

## 7. 当前测试文件

```text
tests/test_message_bus.py
tests/test_wake_gate.py
tests/test_dispatcher.py
```

Phase 0中这些文件为空。后续Phase按职责实现，不提前堆积占位断言。

计划新增：

```text
tests/test_mqtt_broker.py
tests/test_mqtt_gateway.py
tests/test_mqtt_contract.py
tests/hardware/test_eva_mqtt.py
```

前三个使用进程内Broker和fake device，可进入常规CI；`tests/hardware/test_eva_mqtt.py`只在EVA1/EVA2同网段且显式启用硬件标记时运行。
