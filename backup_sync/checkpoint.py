from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .core import ActionResult
from .reporting import write_json_atomic


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    status: str
    started_at: str
    updated_at: str
    source: str
    target: str
    succeeded: int
    failed: int
    path: Path
    error: str | None = None


def list_runs(state_dir: Path) -> list[RunRecord]:
    if not state_dir.exists():
        return []
    records = [_read_run(path) for path in state_dir.glob("*.json")]
    return sorted(records, key=lambda record: record.started_at, reverse=True)


def _read_run(path: Path) -> RunRecord:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        results = payload.get("results", [])
        status = str(payload.get("status", "invalid"))
        updated_at = str(payload.get("updated_at", ""))
        if status == "running" and updated_at:
            updated = datetime.fromisoformat(updated_at)
            if datetime.now().astimezone() - updated > timedelta(minutes=10):
                status = "interrupted"
        return RunRecord(
            run_id=str(payload.get("run_id", path.stem)),
            status=status,
            started_at=str(payload.get("started_at", "")),
            updated_at=updated_at,
            source=str(payload.get("source", "")),
            target=str(payload.get("target", "")),
            succeeded=sum(bool(result.get("success")) for result in results),
            failed=sum(not bool(result.get("success")) for result in results),
            path=path,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return RunRecord(path.stem, "invalid", "", "", "", "", 0, 0, path, str(exc))


class Checkpoint:
    def __init__(self, path: Path, payload: dict[str, Any]):
        self.path = path
        self.payload = payload

    @classmethod
    def create(
        cls,
        state_dir: Path,
        run_id: str,
        source: Path,
        target: Path,
        recycle: Path,
        started_at: datetime,
    ) -> Checkpoint:
        checkpoint = cls(
            state_dir / f"{run_id}.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "running",
                "source": str(source),
                "target": str(target),
                "recycle": str(recycle),
                "started_at": started_at.isoformat(),
                "updated_at": started_at.isoformat(),
                "results": [],
            },
        )
        checkpoint.save()
        return checkpoint

    @classmethod
    def load(cls, state_dir: Path, run_id: str) -> Checkpoint:
        path = state_dir / f"{run_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"找不到 checkpoint: {run_id}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"checkpoint 已损坏: {path}") from exc
        if payload.get("schema_version") != 1 or payload.get("run_id") != run_id:
            raise ValueError(f"checkpoint 格式无效: {path}")
        return cls(path, payload)

    def validate_paths(self, source: Path, target: Path) -> None:
        if Path(self.payload["source"]) != source or Path(self.payload["target"]) != target:
            raise ValueError("checkpoint 的源/目标目录与当前配置不一致")
        if self.payload.get("status") == "success":
            raise ValueError("该运行已经成功完成，无需恢复")

    @property
    def recycle(self) -> Path:
        return Path(self.payload["recycle"])

    @property
    def started_at(self) -> datetime:
        return datetime.fromisoformat(self.payload["started_at"])

    def record(self, result: ActionResult) -> None:
        self.payload["results"].append(
            {
                "kind": result.action.kind.value,
                "path": result.action.path.as_posix(),
                "source": result.action.source.as_posix() if result.action.source else None,
                "success": result.success,
                "attempts": result.attempts,
                "error": result.error,
            }
        )
        self.payload["updated_at"] = datetime.now().astimezone().isoformat()
        self.save()

    def finish(self, status: str, report: Path) -> None:
        self.payload["status"] = status
        self.payload["report"] = str(report)
        self.payload["updated_at"] = datetime.now().astimezone().isoformat()
        self.save()

    def save(self) -> None:
        write_json_atomic(self.path, self.payload)
