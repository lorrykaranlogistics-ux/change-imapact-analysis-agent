#!/usr/bin/env bash
set -euo pipefail

if curl -fsS "http://localhost:8000/health" >/dev/null; then
  echo "OK   impact-agent -> http://localhost:8000/health"
else
  echo "FAIL impact-agent -> http://localhost:8000/health"
  exit 1
fi
