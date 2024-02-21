#!/bin/bash

# Activate your virtual environment if needed
# source /path/to/your/virtualenv/bin/activate

# Export the Flask app name and Gunicorn bind address
export FLASK_APP=dashy_web:app
export GUNICORN_BIND_ADDRESS=0.0.0.0:5000

echo Running in $(pwd)

echo Starting Camera Uploader...
/usr/bin/python3 /opt/dashy/dashy.py > /opt/dashy/dashy.log 2>&1 &

echo Starting Web Server
/usr/bin/python3 -m gunicorn -b $GUNICORN_BIND_ADDRESS $FLASK_APP > /opt/dashy/dashy_web.log 2>&1 & 

while true; do

   sleep 5

   # Check if Gunicorn is running by looking for the process
   if pgrep -f gunicorn &> /dev/null; then
      echo "Gunicorn server is running."
   else
      echo "Gunicorn server failed"
      exit 101
   fi

   # Check if Camera Uploader is running by looking for the process
   if pgrep -f dashy.py &> /dev/null; then
      echo "Camera Uploader is running."
   else
      echo "Camera Uploader failed to start."
      exit 102
   fi

   sleep 300
done
