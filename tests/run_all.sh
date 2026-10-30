#!/bin/zsh
# Run the test suite once per brand pack. Exit code != 0 blocks the deploy (install.py preflight step).
set -e
cd "$(dirname "$0")/.."
for b in brands/*/; do
  b=$(basename "$b")
  echo "== brand $b"
  BRAND=$b uv run -q --python 3.11 --with-requirements requirements-dev.txt python -I -m pytest tests -q -p no:cacheprovider
done
