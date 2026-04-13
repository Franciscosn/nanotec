#!/bin/zsh
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  exec python3 run.py "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python run.py "$@"
fi

echo "Python wurde nicht gefunden. Bitte Python installieren."
read -k1 -r "?Taste druecken zum Beenden..."
