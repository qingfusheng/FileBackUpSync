#!/usr/bin/env bash
set -euo pipefail

IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
INSTALL_DEPS="${INSTALL_DEPS:-1}"

log() {
  printf '[quality] %s\n' "$1"
}

run() {
  log "$1"
  shift
  "$@"
}

if [[ "$INSTALL_DEPS" != "0" ]]; then
  run '升级 pip' "$PYTHON_BIN" -m pip install --upgrade pip
  run '安装开发依赖' "$PYTHON_BIN" -m pip install -e '.[dev]'
else
  log '跳过依赖安装（INSTALL_DEPS=0）'
fi

run 'ruff check' ruff check .
run 'ruff format --check' ruff format --check .
run 'mypy' mypy
run 'unittest + coverage' "$PYTHON_BIN" -m coverage run -m unittest discover -v
run 'coverage report' "$PYTHON_BIN" -m coverage report
run 'build' "$PYTHON_BIN" -m build

log '质量检查完成'

