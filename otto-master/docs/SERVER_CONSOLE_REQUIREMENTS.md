# Otto Master Server 控制台需求

本文档定义 Otto Master 在打包前必须具备的 Server Web 控制台、MQTT设备接入、动作控制和连接验证能力。它描述产品行为和验收标准；实际完成状态以`DEV_PROGRESS.md`和当轮Session Contract为准。

`test1.1`起，控制台源码位于仓库根`webui/`，以Forge电台3D工作台为视觉主版本；构建快照由同一个Python Web Gateway离线提供。Otto既有API、稳定设备身份、访问策略、Dispatcher和命令生命周期仍是后端唯一事实源，参考工程的假设备后端不进入生产。`test1.2`按用户决策把可信家庭局域网默认改为免令牌直接控制；可选安全模式仍保留。

本文中的P0是打包前必需项。任一P0验收未通过，不得进入Windows正式打包或标记MVP完成。

## 1. 控制台定位

控制台是Otto Master的本地管理面，不是直接连接ESP32的MQTT客户端。

```text
浏览器
  → HTTP / WebSocket
Python Web Gateway
  → 输入校验
Message Bus
  → Dispatcher
Device Manager / Session
  → MQTT Gateway
Embedded MQTT Broker
  → ESP32

ESP32的ACK、状态、心跳和结果按相反方向回到浏览器。
```

强制边界：

- 浏览器不得持有MQTT设备用户名、密码或直接向Broker发布动作。
- WebUI不得使用IP、显示名称或列表位置作为设备主键，只使用MAC派生的稳定 `device_id`。
- 动态IP只用于诊断显示，不能写入Topic、收藏目标或永久控制规则。
- WebUI不得绕过Message Bus、Dispatcher和Device Session直接访问设备连接。
- 页面关闭、刷新或浏览器断开不得停止Server、Broker和设备会话。
- 前端开发使用Vite/TypeScript并提交可重现构建快照；生产运行和Windows交付不得依赖Node.js、npm、CDN或第二个Web服务。

## 2. P0页面与信息架构

### 2.1 总览

必须显示：

- Server状态：启动中、正常、降级、停止中、错误。
- Otto Master版本、运行时间、当前时间和主机平台。
- Web地址、`master.local`、HTTP端口和MQTT端口。
- Embedded MQTT Broker状态、连接设备数、认证状态和最后错误。
- 在线、连接中、状态过期、离线和禁用设备数量。
- 正在执行、失败、超时和最近完成的命令数量。
- SQLite、mDNS、OTA目录和日志组件的健康状态。
- EVA1、EVA2等设备的简要状态卡。
- 全局紧急停止按钮；点击后必须二次确认并显示每台设备的独立结果。

总览不得：

- 只用一个绿色圆点代表整个系统健康。
- Broker失败时仍显示“系统全部正常”。
- 只显示设备名称而隐藏稳定device_id和实际传输。
- 把历史在线记录当作当前在线设备数量。

### 2.2 设备列表

每台设备至少显示：

| 字段 | 要求 |
|---|---|
| 设备名称 | 可读名称，例如EVA1；重名必须提示 |
| device_id | MAC派生稳定ID，可复制，不可随意修改 |
| MAC | 规范化显示，默认可部分折叠 |
| 在线状态 | connecting、online、stale、offline、disabled或error |
| 实际传输 | mqtt、tcp或websocket，不能只显示期望传输 |
| 固件版本 | 例如2.0.5；未知时明确显示未知 |
| IP地址 | 只读诊断信息，不作为身份 |
| 最近心跳 | 相对时间和精确时间 |
| 当前动作 | idle、moving、动作名称或unknown |
| 命令队列 | 等待数量和当前命令 |
| 动作目录 | 已加载数量和更新时间，例如14个 |
| 延迟 | 最近查询或ACK往返耗时 |
| 能力 | action、state、stop、mcp等设备声明能力 |

列表必须支持：

- 按名称、device_id、固件和状态搜索或过滤。
- 在线状态和名称变化实时更新，不要求刷新页面。
- 设备重连只更新同一行，不产生重复设备。
- 选中一台、多台或一个设备组，但任何动作发送前再次显示解析后的实际目标。
- 明确区分“设备未注册”“已注册但离线”“心跳过期”和“认证失败”。

### 2.3 设备详情与控制

设备详情至少包含：

- 身份、网络、固件、能力、Topic和Session信息。
- 最近上线、断线、重连和心跳时间线。
- 动作目录以及每个动作支持的参数和范围。
- 当前状态、最近命令、最后错误和实际传输。
- 只读连接验证、动作验证、停止、重命名、分组、禁用和凭据轮换入口。
- 经过脱敏的协议事件；不得显示密码、Token或完整云API密钥。

### 2.4 命令与事件

必须提供可过滤的命令记录和实时事件流：

- 按device_id、设备名称、命令ID、状态、时间和传输过滤。
- 每个命令展示 `requested → published → accepted → moving → completed` 完整状态。
- rejected、timeout、disconnected和failed必须显示明确原因。
- 可复制命令ID和correlation_id，用于查日志和SQLite记录。
- 页面重连后先获取状态快照，再继续事件增量，不能漏掉断线期间的最终结果。
- 事件必须带服务端序号或游标；发现序号缺口时重新同步，不盲目追加。
- 原始JSON只放在开发详情区，并完成HTML转义和敏感字段脱敏。

### 2.5 多设备对话泳道

- 每台已注册设备独立显示当前或最近对话状态，不把EVA1和EVA2文字合并到同一个日志框。
- 每条泳道至少包含稳定device_id、session、utterance、状态、用户最终转写、助手已提交句子、工具状态、动作和更新时间。
- 新utterance开始时可以暂时保留上一轮完整文字，直到首个新partial或新句子到达，避免界面闪空；旧session迟到事件不得覆盖新session。
- 页面刷新或事件流重连后从Server有界快照恢复，不依赖浏览器本地历史。
- 对话投影只读取脱敏领域事件，不得包含PCM、Opus、Base64音频、API Key、MQTT密码或Authorization头。
- 对话文字属于私密用户内容；快照API与实时事件流都必须经过控制台授权，不能因设备/健康读接口可公开而公开转写和回答。

### 2.6 设置

设置页至少分为：

1. Server：监听地址、Web端口、数据目录、日志级别和是否自动打开浏览器。
2. MQTT：Broker启用状态、监听地址、端口、认证、ACL、心跳阈值、QoS策略和重连参数。
3. 发现：mDNS名称、服务记录和OTA启动配置地址。
4. 设备：名称、分组、启用状态、首选传输和凭据轮换。
5. 动作安全：允许动作、参数上限、单动作超时和广播确认规则。
6. 数据：日志与命令历史保留期、数据库路径和导出诊断信息。
7. 固件：当前可发布固件的版本、大小、SHA-256和兼容硬件信息。

设置要求：

- 修改前做Schema校验、端口占用检查和跨字段冲突检查。
- 明确标出“立即生效”“设备重连后生效”或“重启Server后生效”。
- 保存失败不能只弹“错误”，必须指出字段和原因。
- 密码只允许设置、轮换或清除，API和WebUI不回显原值。
- 危险设置提供确认和回滚值；不能因错误设置导致控制台永久无法进入。

## 3. 设备接入与管理逻辑

### 3.1 标准接入流程

```text
ESP32配网并通过DHCP获得IP
  → 解析master.local
  → 请求OTA/启动配置
  → Server按MAC得到device_id
  → Server生成独立MQTT client_id、凭据和Topic
  → ESP32连接Broker并订阅自己的down Topic
  → ESP32发送hello
  → Server校验Topic、认证身份和hello中的MAC一致
  → ESP32发送heartbeat、state和actions
  → WebUI显示设备online及实际transport=mqtt
```

P0规则：

- 不要求用户输入或烧录电脑静态IP。
- 新设备以device_id注册；显示名称可修改但必须在当前控制台内唯一。
- 每台设备使用独立凭据和最小ACL，只能发布自己的up、订阅自己的down。
- payload中的名称、MAC和IP不能单独决定身份，必须与已认证连接和Topic交叉验证。
- 同一device_id重连替换旧Session，不能生成两台逻辑设备。
- 新MAC、凭据错误、ACL拒绝、固件不兼容和名称冲突必须是不同错误状态。
- 禁用设备后拒绝其新连接，但保留历史记录；删除历史属于独立危险操作。

### 3.2 在线状态判定

推荐状态机：

```text
unknown
  → provisioning
  → connecting
  → online
      ├─ heartbeat超过阈值 → stale
      ├─ 连接断开 → offline
      ├─ 认证或ACL失败 → error
      └─ 管理员禁用 → disabled
```

判定要求：

- 收到MQTT TCP连接不等于设备业务在线。
- 只有认证成功、Topic合法、hello有效且心跳开始后才标记 `online`。
- 默认5秒心跳，超过15秒没有心跳进入 `stale`；连接关闭后进入 `offline`。
- `stale` 设备禁止普通动作，只允许查询、停止或等待恢复。
- 在线标记必须包含来源和时间，不能由浏览器本地计时单独决定。
- Broker或Gateway不可用时，所有相关设备显示“通道不可用”或“状态未知”，不能继续显示绿色在线。

## 4. 连接验证逻辑

控制台必须把连接验证分成“只读验证”和“安全动作验证”，不得默认让机器人移动。

### 4.1 只读连接验证

点击“验证连接”后依次执行：

1. 检查Server、Message Bus、Dispatcher、MQTT Gateway和Broker健康状态。
2. 按device_id查找唯一Session，确认实际传输为mqtt。
3. 检查最近心跳是否在阈值内。
4. 检查hello能力，至少需要state和actions；动作验证还需要action和stop。
5. 生成新的查询ID，向该设备精确down Topic发送 `otto_query`，`retain=false`。
6. 在超时内收到相同ID的状态响应，并确认响应来自同一已认证device_id。
7. 发送 `otto_actions` 查询，校验动作目录非空、结构合法并记录数量。
8. 展示每一步的PASS、FAIL、耗时、命令ID和失败原因。

只有全部步骤通过，才能显示“MQTT连接验证通过”。单纯看到设备IP、Broker连接或publish成功都不算通过。

### 4.2 安全动作验证

动作验证必须由用户单独发起，并先提示确认机器人周围安全。默认使用低风险原地动作，例如一次短 `swing`，不得默认使用walk或jump。

```text
只读验证PASS
  → 确认设备当前idle
  → 校验动作在最新目录中且参数合法
  → 生成唯一command_id并记录requested
  → 精确Topic发布，retain=false
  → 收到同ID ACK，状态accepted
  → 收到moving状态
  → 收到idle，状态completed
  → 展示完整耗时和结果
```

异常路径：

- ACK超时：标记timeout，查询状态，不得自动重复动作。
- ACK拒绝：显示设备错误，不进入moving。
- accepted后无moving：查询状态，超过阈值后发送stop。
- moving后无idle：查询状态并发送高优先级stop，最终状态不能伪造completed。
- 页面断开：Server继续跟踪命令；页面重连后恢复显示真实结果。
- 测试结束：无论成功、失败或取消，都在 `finally` 发送stop并确认idle。

通过条件必须同时包括accepted、moving和最终idle。只收到MQTT PUBACK或设备ACK不能写成动作完成。

## 5. 动作控制需求

### 5.1 单设备控制

- 必须先选择稳定device_id，名称只用于辅助显示。
- 动作下拉框来自该设备最新动作目录，不硬编码14个动作到前端。
- 参数输入根据动作Schema生成，并校验类型、范围和必填项。
- 设备offline、stale、disabled、能力不满足或状态未知时禁用普通动作按钮。
- `stop` 按钮始终独立可见，并使用高优先级命令。
- 点击发送后立即显示command_id和 `requested`，不显示“成功”。
- 相同command_id重复到达设备时不得执行两次。
- 当前命令未完成时，同设备后续动作进入有序队列或被明确拒绝，不允许无序并发。
- WebUI可为常用动作提供快捷按钮，但可用性仍取自所选在线设备动作目录的交集；步数、速度、方向和幅度仍由Server二次校验，快捷按钮不是协议旁路。
- 动作带本地音效时，`action_state=idle`但`sound_busy=true`仍属于执行中；只有本地音效排空后才能显示completed并放行下一动作。
- 默认直控模式下，同源WebUI打开即能控制，不得再因缺少控制令牌锁定动作、stop或对话控件；Origin仍只接受显式白名单、环回/私网IP和`.local`同主机来源。显式启用安全模式后，无令牌或收到401时控件必须锁定并常驻显示“命令未进入服务端”及原因，不能伪装成设备无响应。
- 在线设备动作目录为空时，命令页自动调用既有只读verify并重新读取目录；该恢复流程不得提交动作。刷新后仍为空时必须显示具体设备和失败原因，不能让按钮无解释地永久置灰。
- 正式控制台端口必须唯一可辨；当前Runtime为8081，遗留8080诊断服务不得与正式控制台同时驻留。

### 5.2 多设备、分组与广播

- 发送前列出解析后的每个device_id、名称、在线状态和动作兼容性。
- Web Gateway必须把显式选择拆成多个单设备Dispatcher调用，每台设备独立command_id和结果；不同设备可并发，同一设备仍保持串行。
- 部分设备失败时显示部分成功，不能把整个广播显示为成功。
- 不支持该动作、离线或心跳过期的设备必须在发送前明确列出。
- 广播动作必须二次确认；移动类动作建议要求输入目标设备数量或确认短语。
- 普通批量动作与批量stop必须提供1至16个唯一device_id；空集合、通配符、设备名称、IP或隐式“所有设备”在进入Dispatcher前拒绝。
- 多设备对话测试提供“进入对话/退出对话”批量控制，沿正式Message Bus与设备Gateway逐台等待关联ACK；控件只对声明`conversation_control`能力且在线的显式目标开放。ACK后每台设备是否真正进入laughing/listening必须继续由对话泳道显示，不能用按钮成功提示替代真实session状态。
- EVA1和EVA2可以同时执行不同动作，结果不得串设备或串correlation_id。
- 集群停止必须显示每台设备的stop ACK和最终idle结果。

### 5.3 命令状态展示

统一颜色和文字，不只依靠颜色表达：

| 状态 | UI文字 | 含义 |
|---|---|---|
| requested | 待发送 | Server已接受请求 |
| published | 已发布 | 已发送到设备Topic |
| accepted | 设备已接收 | 设备已排队，不代表完成 |
| moving | 执行中 | 设备状态确认动作中 |
| completed | 已完成 | 对应动作最终回到idle，且已上报的本地音效不再busy |
| rejected | 已拒绝 | 参数、能力或设备拒绝 |
| timeout | 已超时 | 在约定时间内缺少响应 |
| disconnected | 连接中断 | 执行期间设备离线 |
| failed | 执行失败 | 已收到明确失败或无法安全收尾 |

## 6. Browser API最低合同

具体框架可调整，但P0至少需要等价能力：

```text
GET    /api/v1/health
GET    /api/v1/system/status
GET    /api/v1/mqtt/status
GET    /api/v1/devices
GET    /api/v1/devices/{device_id}
PATCH  /api/v1/devices/{device_id}
GET    /api/v1/devices/{device_id}/actions
GET    /api/v1/conversations
POST   /api/v1/devices/{device_id}/verify
POST   /api/v1/commands/action
POST   /api/v1/commands/actions/batch
GET    /api/v1/commands/{command_id}
POST   /api/v1/devices/{device_id}/stop
POST   /api/v1/commands/stops/batch
POST   /api/v1/commands/conversations/batch
POST   /api/v1/cluster/stop
GET    /api/v1/events
GET    /api/v1/settings
PUT    /api/v1/settings
GET    /api/v1/firmware
WS     /api/v1/events/stream
```

API要求：

- 状态变更接口使用经过验证的JSON Schema。
- 错误返回稳定错误码、可读说明和correlation_id。
- 所有时间使用UTC ISO 8601，前端按本地时区显示。
- 列表支持分页或明确上限，事件流有背压和断线重连。
- WebSocket连接先发送快照版本或游标，避免快照与增量竞态。
- 前端不能通过任意Topic、任意JSON或任意设备IP接口绕过领域命令。

设备启动配置使用独立的受保护接口 `POST /api/v1/ota/provision`。它不属于Browser API，必须验证provisioning token且只向当前MAC返回该设备自己的MQTT凭据；控制台页面、普通REST、事件流和日志仍不得返回该密码。

## 7. 安全要求

- MQTT禁止匿名连接并启用每设备ACL。
- 默认直控是用户对可信家庭局域网的显式取舍；8081不得直接映射到公网或访客网络。部署边界不再可信时，必须先开启`server.console_auth_required`并配置高强度令牌。
- 状态修改接口始终验证请求来源；浏览器Origin仅接受配置白名单、环回/私网IP或`.local`同主机。可选安全模式开启时，HTTP与WebSocket还必须验证控制令牌。
- 可选控制台令牌不得写入HTML、日志或普通API响应。安全模式的本机开发页若使用URL fragment一次性引导，只允许`localhost/127.0.0.1/::1`，片段不得发送到HTTP且脚本读取后必须立即用`history.replaceState`清除。
- 安全模式未授权时，动作按钮必须明确提示认证失败，不能让用户误以为命令已经下发；默认直控模式不得出现这一无效门槛。
- 设备名称、错误文本和日志内容输出到HTML前必须转义。
- API响应、浏览器日志、事件流和导出诊断包不得包含MQTT密码、云API Key或完整Authorization头。
- action、speed、steps、direction和amount必须在Server再次校验，不能只依赖前端控件。
- 普通动作需要频率限制；stop不应被普通动作限流阻塞。
- 危险操作包括广播移动、轮换凭据、禁用设备、发布固件、清空历史和关闭Server，必须明确确认。
- 生产包默认不得开启框架调试器、详细Traceback页面或任意文件浏览。

## 8. 可观测性与恢复

控制台必须让故障可以定位到具体层：

```text
WebUI请求
  → Web Gateway
  → Message Bus
  → Dispatcher
  → Device Session
  → MQTT Gateway
  → Broker
  → ESP32 ACK / state
```

每条命令至少记录：

- command_id、correlation_id和目标device_id。
- 用户入口、动作、经过校验的参数和实际传输。
- requested、published、accepted、moving和终态时间。
- ACK耗时、完成耗时、重试或查询次数。
- 失败层、错误码和安全收尾结果。

恢复要求：

- 浏览器刷新后从Server恢复设备和命令状态。
- Server重启后从SQLite恢复设备名称、分组、设置和历史；活连接重新建立，不从数据库伪造在线。
- Broker重启后设备重连，WebUI经历offline/connecting/online真实状态变化。
- 端口占用、数据库不可写、mDNS失败和Broker启动失败必须在控制台显示可操作错误。
- 导出诊断信息时默认脱敏，并包含版本、平台、组件状态和最近相关correlation_id。

## 9. 固件与OTA控制台最低要求

- 显示固件文件是否存在、版本、目标硬件、大小和SHA-256。
- 不允许把未知硬件或校验失败固件标记为可发布。
- 发布前显示目标设备、当前版本、目标版本和回滚说明并要求确认。
- 显示每台设备的下载、校验、安装、重启和重新上线状态。
- OTA成功必须以目标固件版本重新hello为准，HTTP下载成功不等于升级成功。
- 固件二进制不进入普通Git阶段提交；正式制品发布仍需单独授权。

## 10. 跨平台与打包前要求

- macOS和Windows使用相同页面、API和数据合同。
- 静态资源随Python包或可执行文件提供，不依赖CDN和互联网。
- Forge的哈希JS/CSS、HTML、3D模型、图片和GIF必须递归进入wheel与PyInstaller；打包smoke必须在解包后的运行态实际读取嵌套资源，不能只检查源码目录存在。
- 使用可写用户数据目录保存SQLite、日志、配置和固件，不向只读安装目录写运行数据。
- PyInstaller包能找到HTML、CSS、JavaScript、证书和必要动态库。
- 首次启动明确提示Windows防火墙所需局域网权限和实际监听端口。
- 端口冲突时给出端口名、占用端口和处理建议，不直接退出且无解释。
- WebUI关闭后Server继续运行；重新打开或长时间挂着都能自动恢复事件流。
- 支持至少最新版Chrome和Edge；macOS开发阶段同时验证Safari或明确不支持原因。
- 页面不依赖固定屏幕尺寸，设备表和停止按钮在常见笔记本分辨率下可用。
- Server退出时停止接入新命令，安全处理在途命令，断开Gateway，关闭Broker并刷新数据库。

## 11. P0测试矩阵

### 11.1 纯Python与假设备

- [ ] API输入Schema和错误码测试。
- [ ] 未认证控制请求被拒绝。
- [ ] 浏览器API不能指定任意MQTT Topic。
- [ ] 设备名称或IP变化不改变device_id。
- [ ] 相同device_id重连不生成重复设备。
- [ ] 心跳超时按 `online → stale → offline` 更新。
- [ ] 动作参数越界和未知动作被Server拒绝。
- [ ] 命令状态只能按合法顺序迁移。
- [ ] 相同command_id重复投递不产生第二次执行。
- [ ] 广播拆成单设备命令并保留部分失败。
- [ ] 页面事件流断线重连后快照与增量一致。
- [ ] 日志、API和事件流完成密钥脱敏。
- [ ] Forge控制台只调用同源Otto真实API；无参考假设备、浏览器MQTT、Cloudflare Worker或浏览器小智音频回退。
- [ ] 知乎、画像和朗读接口全部要求控制台授权，Access Secret不会进入响应、日志、SQLite、前端产物或事件。

### 11.2 Embedded Broker集成

- [ ] Broker启动、健康检查和优雅关闭。
- [ ] 匿名连接拒绝。
- [ ] EVA1凭据不能访问EVA2 Topic。
- [ ] 动作和stop均为 `retain=false`。
- [ ] 查询ID、ACK ID和内部correlation链一致。
- [ ] Broker重启后假设备和控制台状态正确恢复。
- [ ] Broker故障时WebUI不继续显示MQTT在线。

### 11.3 EVA1/EVA2真机

- [ ] 两台设备通过名称和MAC派生device_id显示，IP只用于诊断。
- [ ] 两台设备只读连接验证全部PASS。
- [ ] 两台设备分别返回14个动作。
- [ ] EVA1执行 `swing → stop → idle`，EVA2状态不变。
- [ ] EVA2执行 `swing → stop → idle`，EVA1状态不变。
- [ ] 同时发送不同动作时命令ID、ACK和状态不串线。
- [ ] 重复command_id不重复执行。
- [ ] 拔电或断Wi-Fi后状态按阈值变为stale/offline。
- [ ] 设备恢复后回到同一记录并重新查询状态和动作目录。
- [ ] 所有测试的finally都发送stop并确认idle。

### 11.4 打包冒烟

- [ ] macOS开发环境完整通过控制台P0测试。
- [ ] Windows干净环境启动可执行文件并打开WebUI。
- [ ] Windows下Broker、mDNS、SQLite、OTA和事件流可用。
- [ ] 浏览器长时间打开、刷新和断网恢复后状态一致。
- [ ] 可执行文件路径含空格和中文时仍能启动并读写用户数据目录。
- [ ] 退出后无残留进程、占用端口和数据库锁。
- [ ] 可执行文件内可读取Forge首页、哈希JS/CSS、模型、贴图和表情资源，生产机不安装Node也能打开完整页面。

## 12. 打包准入定义

只有同时满足以下条件，Server控制台才算达到打包准入：

```text
P0页面与API完整
AND Broker健康、鉴权和ACL通过
AND 设备在线状态来自hello与heartbeat
AND 只读连接验证完整通过
AND 动作验证完成accepted → moving → idle闭环
AND stop和超时安全收尾通过
AND EVA1/EVA2互不串话
AND 页面刷新与重连不丢最终状态
AND 密钥与MQTT凭据不进入浏览器和日志
AND macOS集成测试通过
AND Windows打包冒烟通过
```

以下现象任一存在，都不能称为合格控制台：

- 只能发MQTT消息但看不到设备ACK和最终状态。
- 通过设备名称或动态IP定位控制目标。
- Broker失败后页面仍显示设备绿色在线。
- 页面刷新后丢失正在执行的命令。
- 广播只返回一个总成功状态，无法看到单设备失败。
- 连接测试会在没有安全确认时让机器人移动。
- 必须把MQTT密码交给浏览器才能控制设备。
- Windows可执行文件需要用户另外安装Node.js、Broker或数据库。

## 13. 打包后再考虑的P1能力

以下能力不阻塞首个合格打包：

- 复杂图表、主题切换和自定义仪表盘。
- 互联网远程控制、多租户和复杂角色权限。
- 动作编排时间线、舞蹈编辑器和批量脚本。
- 云端日志平台和长期指标系统。
- 移动端原生应用。
- 自动固件灰度发布和大规模回滚编排。

P1功能不得绕过P0已经建立的身份、权限、Dispatcher、命令生命周期和安全停止规则。
