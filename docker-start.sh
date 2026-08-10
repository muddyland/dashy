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
# See start.sh: one worker (process-local camera state) but multiple threads, so
# a live MJPEG stream can't block camera commands and queue polling.
exec su dashy -c "source .venv/bin/activate && python -m gunicorn \
    -w 1 -k gthread --threads ${DASHY_THREADS:-8} \
    --timeout 120 --graceful-timeout 30 \
    -b $GUNICORN_BIND_ADDRESS $FLASK_APP"
