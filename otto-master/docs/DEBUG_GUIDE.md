# 调试指南

## Phase 0：脚手架

- 检查目录是否与README一致。
- 检查TOML/YAML语法。
- 检查所有模块文件为空或仅为占位说明。
- 检查没有真实API Key、数据库、日志和固件被提交。

## Phase 1：Runtime与消息总线

- Message Bus启动和停止是否可重复。
- 多订阅者是否都能收到事件。
- 某个订阅者异常是否被隔离。
- Runtime关闭后是否残留asyncio Task。

## Phase 2：SQLite

- 新数据库是否执行全部迁移。
- 重启是否保持设备身份和配置。
- 并发写入是否通过单写者或事务收口。
- 数据库损坏和版本过高时是否给出明确错误。

## Phase 3：Web、OTA、mDNS与MQTT Broker

- macOS与Windows能否访问WebUI。
- `master.local` 是否解析到正确局域网地址。
- OTA不存在固件时是否返回明确404而不是空文件。
- 服务器关闭时mDNS记录是否注销。
- 内嵌Broker是否监听配置端口并在Runtime关闭时释放端口。
- 匿名连接、错误密码和跨设备Topic访问是否被拒绝。
- OTA是否按MAC返回唯一client_id和up/down Topic。

## Phase 4：MQTT集群控制与兼容传输

- 检查设备实际使用MQTT、TCP还是WebSocket，不根据配置猜测。
- Topic中的device_id是否与认证设备和payload MAC一致。
- `otto_action_ack`是否只标为accepted，idle前不得标为completed。
- `stop`、动作目录、状态和重复命令ID是否按合同处理。
- Broker重启后设备是否重连；断线期间WebUI不得显示在线。
- EVA1命令不能改变EVA2状态，反之亦然。

- 核对握手Header、hello版本和音频参数。
- 文本帧和二进制帧是否严格分流。
- 设备重连是否替换旧连接并保留设备身份。
- 单台设备异常是否不影响其他设备。

## Phase 5：云端语音

- 分别测试ASR、TTS、LLM适配器。
- 检查超时、限流和无key错误。
- 检查Opus/PCM采样率、声道和帧长。
- 禁止播放TTS期间把回声当作用户命令。

## Phase 6：WakeGate

- 未唤醒文本必须丢弃。
- “你好 EVA1”只能唤醒EVA1。
- 回应播放完成后才开启命令窗口。
- 命令窗口超时后回到sleeping。
- 云API失败时不能绕过唤醒。

## Phase 7：Dispatcher

- 单设备动作按顺序执行。
- EVA1失败不应阻塞EVA2。
- 空目标不能变成集群广播。
- 广播结果要保留每台设备的成功或失败。

## Windows专项

- PowerShell启动与退出。
- Windows Defender Firewall端口规则提示。
- `zeroconf`发现。
- PyInstaller收集静态Web资源和Opus动态库。
- 路径中包含空格与中文时仍可运行。
