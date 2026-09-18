# 0.0.0施工日志归档

## 2026-09-18 / Phase 0 / 文档与空脚手架

### 目标

参考 `catnipthon-backup-20250524-phase0.zip` 的文档治理方式，为Otto机器人集群建立新的Python模块化单体脚手架。

### 范围

- 建立 `otto-master/` 标准Python包目录。
- 建立Runtime、Message Bus、Gateway、Device、Service、Dispatcher、Audio和Storage空模块。
- 建立SQLite、日志、OTA固件和WebUI目录。
- 编写Otto Master专用宪法、架构和施工文档。
- 把架构术语从Event Bus统一为Message Bus。

### 明确未做

- 未实现Python业务代码。
- 未实现HTML、JavaScript或CSS。
- 未创建假SQLite数据库。
- 未复制固件二进制。
- 未调用真实云API。
- 未运行ESP32硬件测试。

### 验证

- 目录结构检查：通过，请求的模块、文档和运行产物目录均存在。
- TOML解析：通过，Python 3.11 `tomllib` 读取版本 `0.0.0`。
- YAML解析：通过，Ruby Psych读取服务端口 `8080`。
- Python占位检查：通过，仅包含Phase 0注释，没有可执行实现。
- 运行产物检查：通过，没有伪造 `otto.db`、`xiaozhi.bin` 或JSONL日志。
- Git状态检查：通过，本地 `main` 跟踪 `origin/main`，变更尚未提交或推送。

### 风险

- `pyproject.toml` 中记录的是计划依赖，尚未安装和锁定。
- 具体云ASR、LLM和TTS Provider仍未选型。
- 固件侧VAD上传流程仍需后续设计和实现。
