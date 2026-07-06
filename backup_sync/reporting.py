from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .core import ExecutionResult, Plan, Snapshot, VerifyMode


def new_run_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def build_report(
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    source: Snapshot,
    target: Snapshot,
    plan: Plan,
    execution: ExecutionResult,
    verify: VerifyMode,
    recycle: Path,
    compare: str = "smart",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "success" if execution.failed == 0 else "partial_failure",
        "started_at": started_at.astimezone(UTC).isoformat(),
        "finished_at": finished_at.astimezone(UTC).isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "source": str(source.root),
        "target": str(target.root),
        "recycle": str(recycle),
        "verify": verify.value,
        "compare": compare,
        "scan": {"source_files": len(source.files), "target_files": len(target.files)},
        "summary": {
            "planned": len(plan.actions),
            "unchanged": plan.unchanged,
            "succeeded": execution.succeeded,
            "failed": execution.failed,
        },
        "actions": [
            {
                "kind": result.action.kind.value,
                "path": result.action.path.as_posix(),
                "source": result.action.source.as_posix() if result.action.source else None,
                "size": result.action.size,
                "success": result.success,
                "attempts": result.attempts,
                "error": result.error,
            }
            for result in execution.results
        ],
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
