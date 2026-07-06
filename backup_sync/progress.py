from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .core import Action, ActionResult


class ProgressDisplay:
    """Terminal progress display that stays silent when stderr is not interactive."""

    def __init__(self, mode: str = "auto") -> None:
        if mode not in {"auto", "always", "never"}:
            raise ValueError(f"无效的进度显示模式: {mode}")
        is_pycharm = os.environ.get("PYCHARM_HOSTED") == "1"
        self.enabled = mode == "always" or (mode == "auto" and (sys.stderr.isatty() or is_pycharm))
        self._plan_bar: tqdm[Any] | None = None
        self._plan_stage: str | None = None
        self._execution_bar: tqdm[Any] | None = None

    def scan(self, label: str) -> tqdm[Any]:
        return tqdm(
            desc=label,
            unit=" 文件",
            dynamic_ncols=True,
            disable=not self.enabled,
        )

    def plan(self, stage: str, completed: int, total: int, path: Path | None) -> None:
        if not self.enabled:
            return
        if stage != self._plan_stage:
            self.close_plan()
            self._plan_stage = stage
            self._plan_bar = tqdm(total=total, desc=stage, unit=" 文件", dynamic_ncols=True)
        if self._plan_bar is None:
            return
        if path is not None:
            self._plan_bar.set_postfix_str(_short_path(path), refresh=False)
        self._plan_bar.update(completed - self._plan_bar.n)

    def close_plan(self) -> None:
        if self._plan_bar is not None:
            self._plan_bar.close()
        self._plan_bar = None
        self._plan_stage = None

    def start_execution(self, total: int) -> None:
        self._execution_bar = tqdm(
            total=total,
            desc="执行同步",
            unit=" 动作",
            dynamic_ncols=True,
            disable=not self.enabled,
        )

    def action_started(self, action: Action) -> None:
        if self._execution_bar is not None:
            detail = f"{action.kind.value}: {_short_path(action.path)}"
            self._execution_bar.set_postfix_str(detail, refresh=True)

    def action_finished(self, result: ActionResult) -> None:
        if self._execution_bar is not None:
            self._execution_bar.update(1)
            if not result.success:
                self._execution_bar.set_postfix_str(f"失败: {_short_path(result.action.path)}")

    def close_execution(self) -> None:
        if self._execution_bar is not None:
            self._execution_bar.close()
        self._execution_bar = None


def _short_path(path: Path, limit: int = 48) -> str:
    value = path.as_posix()
    if len(value) <= limit:
        return value
    return f"…{value[-(limit - 1) :]}"
