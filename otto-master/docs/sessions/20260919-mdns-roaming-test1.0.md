# test1.0 mDNS换网恢复施工记录

## 目标与边界

- 修复Server在同名Wi-Fi、热点或网段切换后仍把`master.local`解析到启动时IP的问题。
- 保持设备端MQTT端点为`mqtt://master.local:1883`；不写死开发机IP，不重新烧录或重新配网来掩盖Server发现缺陷。
- 先以EVA1通过正式WebUI/Message Bus/MQTT链完成一步前进并回到idle作为换网物理验收；随后按现场设备扩展到EVA2/EVA3，验证三台独立身份和同批动作。
- 根目录用户README、贴图源、ZIP、`.env`、数据库、日志和构建产物不进入提交。

## 现场证据与根因

2026-09-19更换为另一个同名Wi-Fi后：

- 开发机`en0`地址从`192.168.172.225`变为`192.168.122.225`。
- EVA1可在`192.168.122.127` ping通，MAC为`e0:72:a1:f7:11:84`；EVA2可在`192.168.122.117` ping通，MAC为`ac:a7:04:ed:89:a8`。
- 旧Runtime健康状态仍显示`master.local → 192.168.172.225`。因此两台设备已经完成Wi-Fi入网，失败边界在Server mDNS记录和MQTT重连，不是ESP32配网。
- `MdnsGateway`原先在构造时只调用一次`local_ipv4_addresses()`，之后直到进程退出都不更新。Server重启后记录立刻变为`192.168.122.225`，EVA2随后自动MQTT回连，证明Server侧诊断成立。
- EVA1继续offline后，用临时只读8765监听同时收到两台设备的`otto-master/1` hello：EVA1明确通过mDNS找到当前Server并上报`EVA1 / 192.168.122.127`。继续审查发现第二层缺口：`OttoMasterLink`每次重连会手写mDNS查询，但正式`MqttProtocol`没有复用结果，只把字符串`master.local`交给ESP MQTT/路由器DNS。
- 固件2.0.16修复该接线后完整构建并OTA到EVA1。设备无需写死IP或重新配网，随后以`firmware_version=2.0.16`、`ip_address=192.168.122.127`发出正式MQTT hello并保持heartbeat；幂等认证核对也确认设备保存的首选transport为mqtt、凭据与Server一致。

同名SSID不是网络身份；路由器、子网、网关和DHCP租约都可以不同。mDNS是动态主机名发现，不是固定DNS或固定IP。

## 三台设备扩展记录

| 名称 | 稳定device_id / MAC | 当前IP | 固件与接入证据 |
|---|---|---|---|
| EVA1 | `e072a1f71184` / `e0:72:a1:f7:11:84` | `192.168.122.127` | 2.0.16；OTA升级后经显式mDNS自动MQTT回连 |
| EVA2 | `aca704ed89a8` / `ac:a7:04:ed:89:a8` | `192.168.122.117` | 2.0.16；串口完整烧录后屏幕显示`EVA2/2.0.16`，保留原Wi-Fi、名称、凭据与校准 |
| EVA3 | `288485478f34` / `28:84:85:47:8f:34` | `192.168.122.59` | 2.0.16；串口启动确认屏幕显示`EVA3/2.0.16`、资源分区校验和模型加载通过，随后受保护发放独立MQTT身份 |

- EVA2第一次OTA已完成下载并返回`upgrade_started`，但重启后版本探针仍为2.0.9，因此不计成功；改用串口完整烧录，应用镜像3,831,136字节，SHA256为`b8d4e7323b5a0d4d3486373bb9c2a0a0fd345a1d4bf9bc88565595d387b1cc1a`。启动后正式verify耗时98.511 ms，状态idle、动作15个。
- EVA2按钮会话`faa4eea4-b809-4e34-b835-0b221fcbbd5c`经MQTT+UDP进入listening，完成本地笑声、识别“你好，你是谁？”、奶龙文本/TTS和再次监听后退出；串口与Server投影均无最终错误。该结果是EVA2单设备链路烟测，不替代多设备并发语音隔离和用户听感验收。
- EVA3应用镜像3,831,136字节，SHA256为`50f159e5646f47045027b94878527a728e2fab03b0333761710a0a46472652a8`。整片写入末尾因USB重枚举使烧录工具返回非零码，因此只在重新枚举端口后用MAC复核、完整启动、资源校验、版本/名称屏显和正式MQTT上线共同判定成功。
- EVA3原NVS指向旧云端，随后仅向目标MAC发送受保护`otto_provision`，写入`mqtt://master.local:1883`及按`288485478f34`隔离的client、用户名和上下行Topic；临时含密码文件已删除，密钥未进入日志或Git。正式verify耗时171.521 ms，状态idle、动作15个。
- EVA2冷启动时出现过显式mDNS查询瞬时失败，后续重试自行解析并连上`.225`；本轮没有用固定IP兜底。它是后续冷启动稳定性观察项，不影响本次最终三台online结果。

## 实现合同

1. `discovery.refresh_interval_seconds`生产值为5秒，配置下限为1秒。
2. Gateway启动时读取当前IPv4并注册；后台任务周期重新读取，集合变化时调用同一个Zeroconf后端更新服务。
3. 切网瞬间若只得到回环地址，不得用`127.0.0.1`覆盖最后一个有效LAN记录。
4. 地址提供器或Zeroconf更新失败时，保留旧记录，健康状态降级并在下一周期重试；成功后清除错误。
5. 关闭时先取消监视任务，再注销最新的`ServiceInfo`，避免退出时重新发布或注销旧记录。
6. 健康接口公开`addresses`、`refresh_interval_seconds`、`refresh_count`、`refresh_failures`、`monitor_running`和`last_error`；WebUI组件表自动显示这些字段。
7. 固件NVS只保存`mqtt://master.local:1883`。2.0.16在首次连接和每次重连前显式查询mDNS，把当次IPv4交给MQTT Client，不持久化DHCP地址。
8. MQTT连接成功后的fleet hello和heartbeat继续发送MAC、设备名和当前DHCP IP；Server以MAC派生的稳定device_id更新动态表，IP和名字不是控制目标。
9. 更换承载Server的主机时，新主机注册同一个`master.local`即可接管定位；还必须迁移`.local-secrets/mqtt-credentials.json`或执行受认证配对。完全空白Server不能仅凭mDNS绕过Broker认证。

## 验收矩阵

| 编号 | 标准 | 状态 | 证据 |
|---|---|---|---|
| M1 | 配置加载生产周期并拒绝小于1秒 | PASS | `tests/test_config.py` |
| M2 | IPv4变化后监视任务自动更新同一服务记录 | PASS | `tests/test_mdns.py`自动轮询用例 |
| M3 | 瞬时只剩回环地址时保留最后LAN记录 | PASS | `tests/test_mdns.py`回环保护用例 |
| M4 | 更新失败不丢旧记录，下一次成功可恢复 | PASS | `tests/test_mdns.py`失败/重试用例 |
| M5 | 全量软件门禁 | PASS | 187项pytest、Ruff、mypy strict、Node语法、锁文件和diff检查通过 |
| M6 | 当前Runtime广播`192.168.122.225`且监视任务健康 | PASS | 健康接口显示`healthy=true`、`monitor_running=true`、无刷新失败 |
| M7 | EVA1 2.0.16不写死IP、不重新配网即可在新网段自动MQTT回连 | PASS | `hello`报告2.0.16、`192.168.122.127`、transport mqtt；后续heartbeat持续 |
| M8 | Server重启后EVA1/EVA2只靠hostname自动回连 | PASS | Runtime 23:35:14.741Z启动；EVA1 23:35:15.111Z hello，EVA2 23:35:24.534Z hello；机器人均未重启/重配 |
| M9 | EVA1在新网段经正式控制链前进一步并回到idle | PASS | 命令`df274527-df9e-4e40-b1b3-23ccaf6b790c`：requested→published→accepted→moving→completed，5,238.102 ms；最终idle、sound false、base_emotion，EVA2未选中 |
| M10 | EVA1/EVA2经正式批量API同时前进且独立完成 | PASS | batch `array-eva1-eva2-20260919-01`，requested=2、accepted=2、failed=0；命令`83d9d2d0-3bc9-4553-b6ea-a0016c34c3ae`和`3c2cafcb-d4cb-4ad9-9a38-cfbc27172f01`分别在5,154.350 / 5,216.091 ms完成 |
| M11 | EVA3以正确名称、MAC和独立凭据接入 | PASS | 屏幕`EVA3/2.0.16`，hello为`288485478f34 / 192.168.122.59`，verify 171.521 ms且15个动作 |
| M12 | EVA1/EVA2/EVA3同批动作不串设备 | PASS | batch `array-eva1-eva2-eva3-20260919-01`请求3、接受3、失败0；三条命令均完整经过requested→published→accepted→moving→completed，最终三台online/idle |
| M13 | 本轮提交push后macOS/Windows CI | PENDING | 等待提交 |

## 回滚与风险

- Server回滚点为`test1.0@1307c3a`；回滚只用于实现故障，不应恢复写死IP。
- 尚未在实体Windows上执行真实网卡切换；跨平台单元/集成门禁不能替代Phase 9实体网络测试。
- 固件2.0.15及更早版本的正式MQTT仍可能依赖底层`.local`解析；换网鲁棒性以2.0.16为最低版本。不得把临时诊断IP写入NVS。
- EVA2观察到一次冷启动mDNS瞬时失败后重试恢复；需要后续断电循环统计才能形成冷启动可靠性结论，不能从一次成功外推长期SLA。
- 全新Server如果没有原凭据文件会正确拒绝旧设备；后续如需真正“空主机零接触接管”，必须设计带物理确认或一次性令牌的安全配对，不能开放匿名自动注册。
- 固件回滚点为`codex/otto-portable@c6addc6`（2.0.15）；2.0.16已推送`codex/otto-portable@c4ad28e45adb5f565469d4c14b52aedcb74c1ffb`，本地与远端SHA一致。

## 提交状态

- 分支：`test1.0`
- 本轮commit：待验收后回填
- 远端SHA与CI：待push后回填
- 配套固件：`howtion0/otto codex/otto-portable@c4ad28e45adb5f565469d4c14b52aedcb74c1ffb`，已推送并核对远端SHA
