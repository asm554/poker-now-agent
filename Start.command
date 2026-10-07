#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Bitte zuerst die Einrichtung aus README.md ausführen.'
  read '?Enter zum Schließen'
  exit 1
fi
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.browser-cache"
exec .venv/bin/python -m practice.app
