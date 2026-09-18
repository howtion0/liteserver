# MQTT集群控制合同

本文档定义 Otto Master 使用 MQTT 控制 EVA 机器人集群时的协议边界、迁移顺序和真机验收方法。它以固件 `2.0.5` 已验证的 `master.local:8765` 控制能力为行为基线，但不把 TCP 测试结果误写成 MQTT 已通过。

## 1. 架构决定

- Otto Master 默认在同一个 Python 进程中运行嵌入式 MQTT 3.1.1 Broker 和 MQTT Gateway，不要求用户另外安装 Broker。
- MQTT 是集群控制的目标主通道，传递设备上线、心跳、动作、状态、动作目录和结果。
- Xiaozhi WebSocket继续作为兼容传输；当前固件一次只选择 MQTT 或 WebSocket 作为主协议，不能假设两者同时在线。
- 当固件选择 MQTT 时，语音JSON走MQTT，Opus音频沿用固件现有的加密UDP通道；禁止把连续Opus音频塞入MQTT控制Topic。
- `master.local:8765` 原生TCP在迁移期作为诊断与安全回退通道，等MQTT真机验收全部通过后再决定是否默认关闭。
- 内部 `MessageBus` 与 MQTT Broker 是两层不同的总线：前者负责进程内领域消息，后者只负责设备网络传输。

Phase 5语音链继续遵守这条边界：MQTT只承载TTS/listen等JSON信令，连续Opus仍走加密UDP；内部Message Bus只发布音频元数据和短期 `frame_ref`。三传输共同的身份、选择和命令路由见 `docs/DEVICE_TRANSPORT_CONTRACT.md`；火山ASR/TTS、PCM/Opus转换和完整数据流见 `docs/VOLCENGINE_SPEECH_INTEGRATION.md`。

## 2. 固件2.0.5已知能力与缺口

### 已有能力

- OTA响应可以下发 `endpoint`、`client_id`、`username`、`password`、`publish_topic` 和 `subscribe_topic`。
- MQTT下行已能处理 `otto_action`、`otto_query`、`otto_actions`、MCP和小智语音JSON。
- MQTT上行已能返回 `otto_action_ack`、`otto_state` 和 `otto_actions`。
- 固件底层支持MQTT QoS参数，但当前 `MqttProtocol` 使用默认QoS 0。
- TCP链路已经验证两台设备上线、14个动作、`swing`、`stop` 和 `moving → idle` 状态变化。

### MQTT迁移前必须补齐

- MQTT JSON入口增加 `stop` / `otto_stop`，返回带相同命令ID的 `otto_stop_ack`。
- MQTT连接成功后主动发布包含MAC、设备名、固件版本和能力的 `hello`。
- 每5秒发布 `heartbeat`；Master超过15秒未收到时标记离线。
- 增加命令ID去重缓存。在启用QoS 1或应用层重试后，同一ID不得重复执行动作。
- 动作立即ACK只代表“已接收”，不代表动作完成；完成状态必须通过状态事件或查询确认。

在这些缺口补齐前，TCP继续承担 `stop` 和可靠在线心跳，MQTT测试只标记为部分通过。

## 3. 启动与发现流程

```text
ESP32连接家庭Wi-Fi
  → DHCP获得动态IP
  → 解析master.local
  → POST OTA/启动配置接口
  → Master按MAC注册设备并返回MQTT配置
  → ESP32连接master.local:1883
  → 订阅自己的down Topic
  → 发布hello
  → 发布heartbeat / state / result
  → WebUI按device_id和名称显示设备
```

IP只用于诊断，不能作为设备主键，也不能写入动作Topic。稳定设备ID使用规范化MAC；`EVA1`、`EVA2`是可修改且必须唯一的显示名称。

## 4. Topic合同

`device_id` 使用小写、无冒号MAC，例如 `aabbccddeeff`。

```text
设备 → Master: otto/v1/devices/{device_id}/up
Master → 设备: otto/v1/devices/{device_id}/down
```

Otto Master订阅：

```text
otto/v1/devices/+/up
```

规则：

- 不使用设备名称作为Topic身份，避免重命名和重名导致串话。
- 不使用共享的 `device-server` 上行Topic，因为普通MQTT订阅者无法可靠获得原发布者身份。
- 单设备命令只能发布到该设备的精确 `down` Topic。
- 集群广播必须由Dispatcher拆成多个单设备命令；禁止使用一个通配下行Topic让所有机器人直接执行。
- Topic版本 `v1` 与内部Message版本分别管理。

## 5. 外部JSON

### 上线

```json
{
  "type": "hello",
  "protocol": "otto-mqtt/1",
  "name": "EVA1",
  "mac": "aa:bb:cc:dd:ee:ff",
  "firmware_version": "2.0.5",
  "capabilities": {
    "actions": true,
    "state": true,
    "stop": true,
    "mcp": true
  }
}
```

### 动作

```json
{
  "type": "otto_action",
  "id": "cmd-uuid",
  "action": "swing",
  "steps": 3,
  "speed": 1000,
  "direction": 1,
  "amount": 30
}
```

立即接收结果：

```json
{
  "type": "otto_action_ack",
  "id": "cmd-uuid",
  "ok": true,
  "action": "swing",
  "runtime": {
    "otto": {
      "action": {"state": "moving", "name": "swing"}
    }
  }
}
```

### 停止

```json
{"type":"stop","id":"stop-uuid"}
```

目标响应：

```json
{
  "type": "otto_stop_ack",
  "id": "stop-uuid",
  "ok": true,
  "runtime": {
    "otto": {
      "action": {"state": "idle", "name": "idle"}
    }
  }
}
```

### 查询

```json
{"type":"otto_query","id":"query-uuid"}
```

```json
{"type":"otto_actions","id":"actions-uuid"}
```

每个响应必须原样返回 `id`。未知类型、无效参数和不支持动作必须返回显式错误，不能静默执行默认动作。

## 6. 命令生命周期

```text
requested
  → published
  → accepted        MQTT ACK，设备已排队
  → moving          状态确认
  → completed       状态回到idle

任一步也可能进入 rejected / timeout / disconnected / failed
```

- WebUI收到 `accepted` 时显示“已接收”，不能提前显示“执行完成”。
- Master使用命令ID关联ACK和后续状态。
- 在固件增加主动完成事件前，Master对已接收动作定时发送 `otto_query`，直到 `idle` 或超时。
- `stop` 使用高优先级队列，不能排在普通动作后面等待。

## 7. QoS、重复投递与retain

- 迁移第一步保持固件当前QoS 0，验证完整往返。
- 完成命令ID去重后，动作、停止和结果升级为QoS 1。
- QoS 1可能重复投递；设备必须缓存近期命令ID并重放原ACK，不能重复执行。
- 所有动作和停止命令必须 `retain=false`，防止设备重连后执行过期动作。
- 当前状态由Master保存到内存和SQLite，不依赖retained command。
- 连续Opus音频不通过这些Topic传输。

## 8. 鉴权与隔离

- 禁止匿名设备连接。
- OTA为每个MAC生成独立 `client_id`、用户名和短期或可轮换密码。
- Broker ACL只允许设备订阅自己的 `down`、发布自己的 `up`。
- Master凭据只允许订阅全部 `up` 和发布设备 `down`。
- WebUI不能直接发布MQTT；必须经过REST/WebSocket输入校验、MessageBus和Dispatcher。
- 广播在内部拆分，保留每台设备独立命令ID和结果。

## 9. 单进程实现边界

当前锁定 `amqtt>=0.12,<0.13`：Phase 3已在macOS和Windows完成Broker启动、鉴权、ACL、关闭和PyInstaller冒烟；Phase 4A已用同库Client完成Master订阅、两个fake设备真实Broker上行和本机关闭验证。设备真机重连仍需后续验收。

参考：

- [aMQTT Broker嵌入API](https://amqtt.readthedocs.io/en/latest/references/broker/)
- [aMQTT Client API](https://amqtt.readthedocs.io/en/latest/references/client/)
- [aMQTT PyPI平台与Python兼容信息](https://pypi.org/project/amqtt/)

具体实现封装在 `gateways/mqtt_broker.py`；协议翻译放在 `gateways/device_mqtt.py`。库类型不得出现在Device Session、Dispatcher或消息合同中。

Runtime启动顺序：

```text
Config / Logging / MessageBus / Storage
  → Embedded MQTT Broker
  → MQTT Gateway client and subscriptions
  → Device Manager / Dispatcher
  → HTTP / OTA / WebUI / mDNS
```

关闭顺序相反：先停止新命令，再断开Gateway客户端，最后关闭Broker。嵌入式Broker不可用时，Runtime必须明确失败或按配置退回TCP，不能显示成MQTT在线。

### Phase 4A已实现边界

- Master Client只订阅`otto/v1/devices/+/up`，QoS 0。
- hello必须携带`otto-mqtt/1`、MAC、名称和固件版本；MAC必须与Topic身份一致。
- heartbeat、`otto_state`、`otto_actions`、action/stop ACK和显式error均经过有界JSON验证后转换为内部Message。
- Device Manager维护唯一MAC Session及connecting/online/stale/offline/error连接状态；Gateway不可用时不会继续显示online。
- 本检查点不发布`down`消息，不代表动作、stop、命令ID去重或EVA真机MQTT通过。

### Phase 4B已实现边界

- 只读验证经Message Bus请求，Gateway仅接受状态查询和动作目录查询两个内部白名单topic。
- 每条请求使用内部Message ID作为外部`id`，发布到精确`otto/v1/devices/{device_id}/down`，QoS 0且`retain=false`。
- `otto_state`和`otto_actions`返回相同`id`后映射为内部`correlation_id`；ID、响应topic或device target任一不匹配均不能完成等待。
- 验证报告同时要求Broker/Gateway健康、Session online、实际传输为mqtt、存在心跳及state/actions能力，并记录查询命令ID、延迟与动作数量。
- 本检查点不允许Browser发送任意Topic/JSON，也不发布动作或stop；因此仍不代表动作闭环或EVA真机通过。

### Phase 4C已实现边界

- Web受保护API只提供结构化device_id、action、parameters和显式confirmation，不接受MQTT Topic或原始JSON。
- Dispatcher仅对online、mqtt、enabled、能力满足且动作目录/参数合法的设备创建命令；每设备普通动作有界串行，不同设备可并行。
- Gateway将`device.action.execute.requested`编码为`otto_action`，将`device.stop.execute.requested`编码为`stop`；两者均发往精确单设备down Topic，QoS 0、`retain=false`，外部`id`为持久command ID。
- SQLite记录`requested → published → accepted → moving → completed`及rejected/timeout/disconnected/failed终态；重启不重放未完成动作。
- stop中断当前动作、取消尚未执行的同设备动作并优先下发；集群stop拆为每设备独立命令与结果。
- ACK或完成超时不重发动作，而是标记timeout并排入安全stop；传输断开标记disconnected。
- 当前证据来自真实内嵌Broker与两个fake客户端；固件2.0.5的MQTT stop、hello/heartbeat和去重缺口未补齐，不声称EVA真机通过。

## 10. EVA1/EVA2真机验收

已知环境基线：

```text
Wi-Fi: EVA1、EVA2和开发机位于同一局域网
Master: master.local
Firmware: 2.0.5
EVA1当前IP: 192.168.172.127（仅诊断）
EVA2当前IP: 192.168.172.117（仅诊断）
动作目录: 14个动作
```

正式验收不得依赖上述IP固定不变。测试通过条件：

1. Master启动后两台设备按MAC和名称上线。
2. 两台设备分别返回14个动作，集合与固件目录一致。
3. 对EVA1发送 `swing`，EVA2不得改变状态；随后 `stop`，EVA1回到 `idle`。
4. 对EVA2执行相同步骤，EVA1不得改变状态。
5. 同时向两台设备发送不同命令，ACK和状态不能串设备或串命令ID。
6. 重复发送同一命令ID不得执行两次。
7. 重启Broker后，设备应在允许时间内重连并恢复查询能力。
8. MQTT不可用时，WebUI明确显示通道故障；如果启用TCP回退，应标注当前实际传输。
9. 测试结束在 `finally` 阶段向两台设备发送 `stop` 并确认 `idle`。

真机用例标记为 `hardware` 和 `mqtt`，不进入默认单元测试；每次阶段验收必须记录设备名称、MAC、固件版本、实际传输、命令ID和结果。
