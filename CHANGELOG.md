# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)；当前仍处于开发阶段。

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
