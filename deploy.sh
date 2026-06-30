#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="/var/www/readysettourney.com/source"
VENV="/var/www/readysettourney.com/venv/bin"

echo "==> Pulling latest code..."
git -C "$SOURCE_DIR" pull

echo "==> Installing dependencies..."
"$VENV/pip" install -r "$SOURCE_DIR/requirements.txt" --quiet

echo "==> Running migrations..."
"$VENV/python" "$SOURCE_DIR/manage.py" migrate --noinput

echo "==> Collecting static files..."
"$VENV/python" "$SOURCE_DIR/manage.py" collectstatic --noinput

echo "==> Restarting gunicorn..."
sudo systemctl restart gunicorn

echo "==> Done."
