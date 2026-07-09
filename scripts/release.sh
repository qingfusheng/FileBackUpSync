#!/usr/bin/env bash
set -euo pipefail

IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
DIST_DIR="${DIST_DIR:-dist}"

confirm() {
  local prompt="$1"
  local reply

  printf '%s [y/N] ' "$prompt"
  read -r reply
  case "${reply:-}" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      printf '已取消。\n' >&2
      exit 1
      ;;
  esac
}

cleanup_dist() {
  rm -rf "$DIST_DIR"
}

run_tests() {
  printf '运行测试（conda 环境内的 Python 由当前 shell 决定）...\n'
  "$PYTHON_BIN" -m unittest tests.test_package tests.test_cli tests.test_analyzers
}

build_packages() {
  printf '构建 wheel 和 sdist...\n'
  cleanup_dist
  "$PYTHON_BIN" -m build
  "$PYTHON_BIN" -m twine check "$DIST_DIR"/*
}

upload_to_testpypi() {
  if [[ -z "${TEST_PYPI_TOKEN:-}" ]]; then
    printf '缺少环境变量 TEST_PYPI_TOKEN。\n' >&2
    exit 1
  fi

  confirm "确认上传到 TestPyPI?"
  TWINE_USERNAME="__token__" TWINE_PASSWORD="$TEST_PYPI_TOKEN" \
    "$PYTHON_BIN" -m twine upload --repository testpypi "$DIST_DIR"/*
}
# python -m twine upload --repository testpypi dist/*

upload_to_pypi() {
  if [[ -z "${PYPI_TOKEN:-}" ]]; then
    printf '缺少环境变量 PYPI_TOKEN。\n' >&2
    exit 1
  fi

  confirm "确认上传到 PyPI?"
  TWINE_USERNAME="__token__" TWINE_PASSWORD="$PYPI_TOKEN" \
    "$PYTHON_BIN" -m twine upload "$DIST_DIR"/*
}

# python -m build
# python -m twine check dist/*
# python -m twine upload --repository pypi dist/* --verbose

printf '发布仓库: %s\n' "$ROOT_DIR"
printf 'Python: %s\n' "$PYTHON_BIN"
printf '输出目录: %s\n' "$DIST_DIR"

confirm "先运行测试?"
run_tests

confirm "继续构建发布产物?"
build_packages

confirm "先上传到 TestPyPI?"
upload_to_testpypi

confirm "继续上传到 PyPI?"
upload_to_pypi

printf '发布完成。\n'
