# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)；当前仍处于开发阶段。

## 0.6.2 - 2026-07-08

### Changed

- `analyze large-files`、`analyze duplicates` 和 `analyze symlinks` 改为 `--scope` 与 `--path` 二选一，避免默认混扫配置源目录。
- `analyze small-files` 和 `analyze ignored` 支持直接通过 `--path` 指定分析目录。
- `config` 子命令改为只接受真实存在的配置文件，不再对缺失的默认路径做假解析。

### Fixed

- 修正 `config get/path/list/set/validate` 在配置文件不存在时仍返回路径或后续崩溃的问题。
- 更新 CLI、README 和分析器测试，覆盖显式路径分析和严格配置解析行为。

## 0.6.1 - 2026-07-08

### Fixed

- 修复 `analyze large-files`、`analyze duplicates` 和 `analyze symlinks` 的 `--path` 参数在 `argparse` 中使用 `action="append"` 时默认值为元组导致的解析崩溃。

## 0.6.0 - 2026-07-08

### Added

- 新增 `analyze large-files` 分析器，按大小列出空间占用最高的文件。
- 新增 `analyze duplicates` 分析器，支持按内容 hash 检测重复文件（含估算模式）。
- 新增 `analyze integrity` 分析器，支持按同路径文件进行源/目标内容一致性校验（含估算模式）。

### Changed

- `analyze` 子命令切换为显式注册机制，扩展新分析器时的接入路径更稳定。
- `analyze ignored` 增强为可输出命中文件与目录明细，并补充对应 CLI/测试覆盖。

## 0.5.0 - 2026-07-08

### Changed

- 按 `sync`、`storage`、`runs` 领域重组内部模块和对应测试。
- 文件写入、替换、移动和删除统一通过平台安全文件操作层执行。

### Added

- 支持在 macOS 修改目标文件前清除用户 immutable (`uchg`) 标记。

### Removed

- 删除完成拆分后不再承担职责的 `core.py` 兼容模块。
- Python API 调用方应改用 `backup_sync.sync` 中的稳定公共入口。

## 0.3.0 - 2026-07-07

### Added

- SQLite 持久化 quick/strong 指纹缓存，元数据变化自动失效。
- BLAKE3 分层 rename 检测：大小分组、三段采样、完整内容确认。
- 指纹缓存命中、计算次数和实际读取量报告。

### Changed

- 内容校验由 SHA-256 切换为 BLAKE3。
- `--compare hash` 强制重新读取完整内容，不复用持久化指纹。
- `plan` 仍不修改源/目标，但会更新本地性能缓存。

## 0.2.0 - 2026-07-07

### Added

- `plan`、`sync`、`resume`、`runs`、`analyze` 和 `config` 子命令。
- 内容级 rename 检测、空目录对齐和小文件热点分析。
- 原子复制、复制后校验、失败重试、checkpoint 与 JSON 报告。
- `small-files`、`health` 分析器及可扩展 Analyzer registry。
- 原子 TOML 配置更新与路径、权限校验。
- 全流程终端/PyCharm 进度显示。
- Ruff、Mypy、分支覆盖率和多版本 CI。

### Changed

- 默认日常比较使用 `smart` 元数据快速路径，可通过 `--compare hash` 完整审计。
- CLI 从组合 flags 改为职责明确的子命令，不兼容早期开发版参数。

### Removed

- 硬编码路径、旧 `.ignore_file/.ignore_dir` 配置和文本日志。
