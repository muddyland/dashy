#!/bin/bash

# Activate your virtual environment if needed
# source /path/to/your/virtualenv/bin/activate

# Export the Flask app name and Gunicorn bind address
export FLASK_APP=dashy_web:app
export GUNICORN_BIND_ADDRESS=0.0.0.0:5000

echo Running in $(pwd)
echo Starting Web Server
/usr/bin/python3 -m gunicorn -b $GUNICORN_BIND_ADDRESS $FLASK_APP > /opt/dashy/dashy_web.log & 
echo Starting Camera Uploader...
/usr/bin/python3 /opt/dashy/dashy.py
