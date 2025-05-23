#!/bin/bash

# Catch any errors and exit immediately
set -e

# Activate your virtual environment if needed
source .venv/bin/activate

# Export the Flask app name and Gunicorn bind address
export FLASK_APP=dashy_web:app
export GUNICORN_BIND_ADDRESS=0.0.0.0:5000

echo Running in $(pwd)

python configure.py

# Start Nginx server
echo Starting Nginx server
nginx

# start the camera uploader, capture PID for tracking 
echo Starting Camera Uploader...
su dashy -c "source .venv/bin/activate && python /dashy/dashy.py" &
CAM_UPLOADER_PID=$!

# Start webserver, capture PID for tracking 
echo Starting Web Server
su dashy -c "source .venv/bin/activate && python -m gunicorn -b $GUNICORN_BIND_ADDRESS $FLASK_APP -w 2" & 
WEB_SERVER_PID=$!

# Print PIDs of running processes
echo Camera Uploader PID: $CAM_UPLOADER_PID
echo Web Server PID: $WEB_SERVER_PID

# Check PIDs to enusure they are running 
while true; do
    if ! ps -p $CAM_UPLOADER_PID > /dev/null 2>&1; then
        echo "Camera Uploader process died."
        exit 1
    fi
    if ! ps -p $WEB_SERVER_PID > /dev/null 2>&1; then
        echo "Web Server process died."
        exit 1
    fi
    sleep 30
done