#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "${script_dir}/.." && pwd)"
cd "${project_dir}"

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv。请先安装：https://docs.astral.sh/uv/"
  exit 1
fi

if [[ ! -f .env && ! -f .env.local ]]; then
  echo "缺少配置文件。请先复制 .env.example 并填写数据库与模型配置。"
  exit 1
fi

exec uv run python -m app.run
