#!/bin/bash
set -e

export FLASK_APP=dashy_web:app
export GUNICORN_BIND_ADDRESS=0.0.0.0:5000

echo "Running in $(pwd)"

if [ -d ".git" ]; then
    if command -v git &> /dev/null; then
        echo "Git repository found. Performing 'git pull'..."
        git pull --no-edit || { echo "Error: Failed to perform 'git pull'."; exit 1; }
    fi
fi

echo "Starting Dashy..."
# One worker only: the camera poller, downloader thread and camera status are
# process-local state, so a second worker would double every camera request.
# Threads are essential though -- the sync worker handles a single request at a
# time, so an open MJPEG stream (an endless response) would block the whole UI
# and make every camera command time out.
exec /usr/bin/python3 -m gunicorn \
    -w 1 -k gthread --threads "${DASHY_THREADS:-8}" \
    --timeout 120 --graceful-timeout 30 \
    -b $GUNICORN_BIND_ADDRESS $FLASK_APP
