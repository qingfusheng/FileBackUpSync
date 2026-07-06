# FileBackUpSync

将源目录单向、增量地镜像到备份目录。目标目录中被替换或删除的文件会先移入独立回收目录；默认仅生成预览，不直接修改磁盘。

## 主要能力

- 正确识别新增、修改、删除和未变化文件。
- 根据文件大小与 SHA-256 内容指纹识别 rename/移动，直接在备份盘内移动，避免重新复制。
- 同步空目录，并自底向上清理源目录中已经不存在的旧空目录。
- TOML 配置源路径、目标路径、回收目录、忽略规则和扫描阈值，无代码硬编码。
- 扫描阶段按目录报告大量小文件热点，便于决定是否加入 ignore。
- 保留文件时间等元数据（`copy2`）；符号链接默认跳过，避免越界遍历。

## 使用

要求 Python 3.11+。

```bash
cp backup.example.toml backup.toml
# 编辑 backup.toml 后先预览
python3 main.py
# 检查计划无误后执行
python3 main.py --apply
```

也可以安装为命令：

```bash
python3 -m pip install -e .
backup-sync --config backup.toml
backup-sync --config backup.toml --apply
```

`ignore.patterns` 使用 glob，路径相对于源目录。例如 `*.tmp`、`node_modules`、`cache/**`。忽略规则只作用于源目录；目标中对应的旧内容仍会按镜像语义删除并进入回收目录。

## 安全语义

- 不带 `--apply` 永远只预览。
- 修改和删除的旧文件存放在 `recycle/YYYY-MM-DD_HHMMSS/`。
- rename 只有在 SHA-256 完全一致时才配对；相同内容存在多份时按路径稳定配对，结果内容不变。
- 回收目录默认位于目标目录旁边的 `.backup-sync-trash/<目标目录名>`，不会被目标扫描包含。
- 目标中的非空异常目录不会被强制删除，会记录 warning 并保留。

## 测试

```bash
python3 -m unittest discover -v
```
