#!/bin/bash
set -e

source .venv/bin/activate

export FLASK_APP=dashy_web:app
export GUNICORN_BIND_ADDRESS=0.0.0.0:5000

echo "Running in $(pwd)"

python configure.py

echo "Starting Nginx..."
nginx

echo "Starting Dashy..."
exec su dashy -c "source .venv/bin/activate && python -m gunicorn -w 1 -b $GUNICORN_BIND_ADDRESS $FLASK_APP"
