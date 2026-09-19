# Windows 可移植源码包使用指南

本指南用于桌面交付物`otto-master-windows-portable-test1.2.zip`。ZIP只含源码、锁文件、施工文档、PowerShell脚本和已构建Forge WebUI，不含任何API Key、设备密码、数据库、日志、缓存、虚拟环境或固件二进制。

密钥与现有EVA的MQTT身份在单独的TXT中。该TXT是明文；其中的Base64只用于单文件搬运，不是加密。不要把它提交Git、发送到聊天或放进公开网盘。

## 1. Windows要求

- 64位Windows 10/11或Windows Server 2016以上。
- PowerShell。
- `uv`。官方安装命令：

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

- `vcpkg`与可用的C++构建工具，用于安装`opus:x64-windows`。把vcpkg根目录放入`VCPKG_ROOT`。
- 第一次启动时，Windows Defender Firewall只允许Otto Master访问“专用网络”；不要开放到公用网络。

官方参考：

- uv Windows安装：<https://docs.astral.sh/uv/getting-started/installation/>
- vcpkg安装命令：<https://learn.microsoft.com/en-us/vcpkg/commands/install>
- Opus端口：<https://vcpkg.io/en/package/opus.html>

## 2. 解压与恢复密钥

把ZIP解压到普通英文路径，例如`C:\Otto\liteserver-windows-portable-test1.2`，把单独的密钥TXT保留在桌面。不要把TXT移动进Git仓库。

在PowerShell中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\Otto\liteserver-windows-portable-test1.2\otto-master
$env:VCPKG_ROOT = "C:\vcpkg"
.\scripts\windows\bootstrap.ps1 `
  -SecretsFile "$env:USERPROFILE\Desktop\otto-master-windows-api-keys-test1.2.txt" `
  -VcpkgRoot $env:VCPKG_ROOT
```

脚本会：

1. 把TXT中的环境变量写入本地`.env`，但不打印值。
2. 把Base64迁移段还原为`.local-secrets\mqtt-credentials.json`，让EVA1/EVA2/EVA3继续使用原凭据连接新主机。
3. 安装Python 3.12、锁定依赖和`opus:x64-windows`。
4. 运行Ruff、mypy、pytest、WebUI静态资源和原生Opus验证。

默认拒绝覆盖已有`.env`或设备凭据；只有明确重做时才添加`-ForceSecrets`。

## 3. 启动

```powershell
cd C:\Otto\liteserver-windows-portable-test1.2\otto-master
$env:VCPKG_ROOT = "C:\vcpkg"
.\scripts\windows\run.ps1
```

打开`http://127.0.0.1:8081`。可信家庭局域网内默认直接控制，不需要控制令牌。若Windows主机换了IP，EVA 2.0.16会在每次MQTT重连前重新解析`master.local`；Windows必须允许mDNS和以下私网端口：

| 端口 | 协议 | 用途 |
|---|---|---|
| 5353 | UDP | mDNS发现 |
| 8081 | TCP | WebUI与REST |
| 1883 | TCP | 内嵌MQTT Broker |
| 8884 | UDP | MQTT Profile的加密Opus音频 |

先关闭Mac上的Otto Master再启动Windows版本，避免两个主机同时宣告`master.local`和抢占同一批设备。

## 4. 验收顺序

1. `GET http://127.0.0.1:8081/api/v1/health`返回`healthy`。
2. WebUI显示“直接控制”。
3. EVA设备发出hello/heartbeat并显示各自稳定`device_id`，不是按IP识别。
4. 先执行“只读验证”，确认状态和15项动作目录。
5. 只选一台设备做一步前进，等待`completed`和`idle`；再测第二台。
6. 最后测试按钮进入/退出循环对话、火山ASR/TTS和DeepSeek。

详细合同与剩余门禁见`ONBOARD.md`、`docs/CONSTRUCTION_PLAN.md`、`docs/DEBUG_GUIDE.md`和`docs/sessions/20260919-direct-console-test1.2.md`。

## 5. 当前交付边界

此包是已验证的Windows源码运行脚手架，不是已经在实体Windows完成验收的最终单文件EXE。GitHub Windows CI已覆盖锁定安装、测试、原生Opus和PyInstaller组件/静态资源smoke；实体Windows防火墙、mDNS、真实EVA和完整应用打包仍须按Phase 9现场验收，不能用macOS结果代替。

若以后把8081开放到访客网或公网，先在`config.yaml`把`server.console_auth_required`改为`true`，并使用TXT中的`OTTO_CONSOLE_TOKEN`；默认直控仅适用于可信家庭局域网。
