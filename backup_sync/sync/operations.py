from __future__ import annotations

from pathlib import Path

from ..storage.archive import archive_remove, backup_existing
from ..storage.fileops import (
    commit_temp_file,
    safe_atomic_copy,
    safe_mkdir,
    safe_move,
    safe_rmdir,
)
from ..storage.fingerprint import file_digest
from .models import Action, ActionKind, Snapshot, VerifyMode


def verify_copy(source: Path, destination: Path, mode: VerifyMode) -> None:
    """
    复制完成后校验文件一致性
    1. 基础校验：文件大小必须相等
    2. HASH 模式额外校验 BLAKE3 摘要
    """
    # 先比对文件尺寸，尺寸不一致直接判定损坏
    if source.stat().st_size != destination.stat().st_size:
        raise OSError(f"复制校验失败（大小不一致）: {destination}")

    # 哈希校验模式，比对完整文件摘要
    if mode == VerifyMode.HASH and file_digest(source) != file_digest(destination):
        raise OSError(f"复制校验失败（SHA-256 不一致）: {destination}")


def atomic_copy(source: Path, destination: Path, mode: VerifyMode) -> Path:
    """
    原子化安全复制文件
    流程：先写入临时文件 → 校验完整性 → 检查源文件中途未修改 → 返回临时文件路径
    上层通过 os.replace 完成无覆盖风险的原子替换
    """
    # 自动创建目标父目录
    return safe_atomic_copy(source, destination, lambda src, dst: verify_copy(src, dst, mode))


def execute_action(
    action: Action,
    source: Snapshot,
    target: Snapshot,
    recycle_root: Path,
    verify: VerifyMode,
) -> None:
    """
    执行单条同步动作，统一处理全部ActionKind逻辑
    recycle_root：归档回收站根目录，删除/更新前会备份旧文件
    """
    # 计算目标真实绝对路径
    destination = target.root / action.path

    if action.kind == ActionKind.REMOVE:
        # 删除文件：移动至归档目录备份，而非直接删除
        archive_remove(destination, action.path, recycle_root)

    elif action.kind == ActionKind.UPDATE:
        # 更新已有文件流程：原子复制临时文件 → 备份原文件 → 原子替换
        temporary = atomic_copy(source.root / action.path, destination, verify)
        # 将原有文件归档备份
        backup_existing(destination, action.path, recycle_root)
        # 原子覆盖目标文件，无中间损坏状态
        commit_temp_file(temporary, destination)

    elif action.kind == ActionKind.COPY:
        # 全新新增文件，无旧文件无需备份，直接原子替换
        temporary = atomic_copy(source.root / action.path, destination, verify)
        commit_temp_file(temporary, destination)

    elif action.kind == ActionKind.RENAME:
        # 文件重命名：校验新旧文件内容一致后执行移动
        if action.source is None:
            raise ValueError("rename 动作缺少原路径")

        old = target.root / action.source
        if not old.is_file():
            raise FileNotFoundError(f"rename 源文件不存在: {old}")

        # 重命名前校验文件哈希，确保内容完全一致再移动
        if file_digest(old) != file_digest(source.root / action.path):
            raise OSError(f"源文件变化，拒绝 rename: {action.path}")

        safe_move(old, destination)

    elif action.kind == ActionKind.MKDIR:
        # 创建目录，存在则忽略
        safe_mkdir(destination)

    elif action.kind == ActionKind.RMDIR:
        # 删除空目录，不存在直接跳过；非空目录抛出异常
        try:
            safe_rmdir(destination)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if destination.is_dir():
                raise OSError(f"目录非空，无法清理: {destination}") from exc
