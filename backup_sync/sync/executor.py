from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from ..storage.fileops import safe_rmdir
from .models import Action, ActionKind, ActionResult, ExecutionResult, Plan, Snapshot, VerifyMode
from .operations import execute_action

# 模块日志对象
LOGGER = logging.getLogger(__name__)


def execute(
    plan: Plan,
    source: Snapshot,
    target: Snapshot,
    recycle_root: Path,
    verify: VerifyMode = VerifyMode.HASH,
    retry_max: int = 3,
    retry_delay: float = 0.5,
    progress_callback: Callable[[ActionResult], None] | None = None,
    action_started_callback: Callable[[Action], None] | None = None,
) -> ExecutionResult:
    """
    批量执行同步计划内所有动作，支持失败指数退避重试
    执行流程：提前清理待删除目录 → 遍历执行全部同步动作 → 记录每条动作执行结果
    :param plan: 预生成的同步执行计划
    :param source: 源目录快照
    :param target: 目标目录快照
    :param recycle_root: 文件归档回收站根目录
    :param verify: 文件复制校验模式（尺寸/哈希）
    :param retry_max: 单条动作最大重试次数
    :param retry_delay: 初始重试等待秒数，采用指数退避
    :param progress_callback: 单动作完成后回调，传入执行结果
    :param action_started_callback: 单动作开始执行前回调，传入动作对象
    :return: 包含所有动作执行记录的汇总结果
    """
    # 确保归档根目录存在
    recycle_root.mkdir(parents=True, exist_ok=True)

    # 预处理：优先删除待清理目录，避免文件动作后目录无法删除
    # 屏蔽目录不存在、目录非空等无害异常
    for action in plan.actions:
        if action.kind != ActionKind.RMDIR:
            continue
        with suppress(FileNotFoundError, OSError):
            safe_rmdir(target.root / action.path)

    results: list[ActionResult] = []

    # 遍历执行计划中全部同步动作
    for action in plan.actions:
        # 拼接日志显示路径，重命名动作展示 old -> new
        detail = f"{action.source} -> {action.path}" if action.source else str(action.path)
        LOGGER.debug("%s %s", action.kind.value, detail)

        # 触发动作开始回调
        if action_started_callback:
            action_started_callback(action)

        action_result: ActionResult

        # 指数退避重试循环，attempt从1开始计数
        for attempt in range(1, retry_max + 2):
            try:
                # 执行单条同步动作
                execute_action(action, source, target, recycle_root, verify)
                # 执行成功，记录成功结果并跳出重试
                action_result = ActionResult(action, True, attempt)
                break

            except OSError as exc:
                # 达到最大重试次数，标记永久失败
                if attempt > retry_max:
                    LOGGER.error("动作失败 %s: %s", detail, exc)
                    action_result = ActionResult(action, False, attempt, str(exc))
                    break

                # 计算指数退避延迟：delay = base * 2^(attempt-1)
                delay = retry_delay * (2 ** (attempt - 1))
                LOGGER.warning(
                    "动作失败 %.1fs 后重试 (%d/%d) %s: %s",
                    delay,
                    attempt,
                    retry_max,
                    detail,
                    exc,
                )
                time.sleep(delay)

        # 保存当前动作执行结果
        results.append(action_result)
        # 触发进度回调
        if progress_callback:
            progress_callback(action_result)

    # 转为不可变元组存入汇总执行结果返回
    return ExecutionResult(tuple(results))
