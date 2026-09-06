#!/usr/bin/env bash
# Install dunders globally from the working tree (editable).
set -euo pipefail
uv sync --extra dev          # uv downloads Python 3.12
pipx install --force  .
pipx install --force -e .
