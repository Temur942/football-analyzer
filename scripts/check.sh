#!/usr/bin/env bash
# =============================================================================
# Проверки перед деплоем: стиль, типы, тесты.
#
# Запуск: bash scripts/check.sh
# Все проверки бесплатные и не трогают прод. Если хоть одна падает — не деплоим.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== ruff ==="
.venv/bin/ruff check .
.venv/bin/ruff format --check .

echo "=== mypy ==="
.venv/bin/mypy src

echo "=== pytest ==="
.venv/bin/python -m pytest -q

echo "ВСЁ ЧИСТО"
