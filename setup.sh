#!/usr/bin/env bash
#
# Cross-platform setup for Linux / macOS (Windows users: run setup.bat).
# Creates a venv, installs pinned dependencies, then opens the guided menu
# (python -m doppelganger) to turn your chat exports into a training-ready
# dataset.
#
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

PYTHON="${PYTHON:-python3}"

echo "[1/4] Creating virtual environment (venv)..."
"$PYTHON" -m venv venv

echo "[2/4] Installing dependencies (this can take a while)..."
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt

echo "[3/4] Preparing .env..."
if [ ! -f .env ]; then
  cp example.env .env
  echo "      Created .env from example.env — edit it to enable optional LLM features."
fi

echo "[4/4] Preparing your dataset..."
mkdir -p data
if [ -t 0 ] && [ -t 1 ]; then
  # Guided menu: finds your exports in data/, asks who you are, builds the dataset.
  ./venv/bin/python -m doppelganger
elif [ -f data/result.json ]; then
  ./venv/bin/python -m ingest --source telegram
else
  echo "      No terminal for the guided setup and no data/result.json found."
  echo "      Put your chat export(s) in data/ (see docs/sources/README.md), then run:"
  echo "        ./venv/bin/python -m doppelganger"
fi

cat <<'EOF'

Setup complete.

Run the menu any time (parse, audit, train, merge, chat):

  source venv/bin/activate
  python -m doppelganger

See README.md for training, merging and inference.
EOF
