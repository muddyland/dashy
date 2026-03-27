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
exec /usr/bin/python3 -m gunicorn -w 1 -b $GUNICORN_BIND_ADDRESS $FLASK_APP
