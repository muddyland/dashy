# Dashy

<b style="color:red"> WARNING:</b> <b> THIS IS STILL A WORK IN PROGRESS TURN BACK NOW if you want an easy solution</b>

## What is Dashy?
Dashy is a set of tools to aid in the ingestion, and consumption of Viofo Dashcam footage. Those of us that have a Viofo know exactly how horrible the app is, but how good the hardware is... 

When you pull into the driveway, enable WiFi, and your locked videos will automatically be uploaded to a storage path of your choice (even NFS). Dashy will create thumbnails, and make the downloaded files avalible in a nice Web UI. 

Dashy also proxies connections to your dashcam, allowing you to view all clips on the camera, and choose to download those files as well! The idea is to be flexible. 

This is a work in progress, but I will be using it for my personal Dashcam, please keep this in mind,,,

## Assumptions before installing
* I have only tested with a Viofo A129 Plus Duo (dual cam, 2k and 1080p)
  * Others may work
  * I assume other Dashcams use this type of setup, perhaps they will work too
* You are using a Raspberry Pi (1,2,3,3b,4), while this may work with other Distros/Hardware, it is not built for that
* You are running as the default Pi user, otherwise you may need to change the user in a few places
* You are <b>NOT</b> using WiFi to connect to your home network (you must access via LAN cable when using Dashy, or have a second WiFi card to conenct to your network) 
* Your Raspberry Pi is close to your vehicle (Vifo cameras can be slow over WiFi)
* You know about basics about Python pip, and Raspberry Pi (Debian) Linux. If not, please, please, please at least try to read up on these topics beforehand. 
* You have python3, python3-pip, and nginx packages installed via apt update and apt install nginx python3 python3-pip
* I am using Debian Buster, but did test this on Bookworm with some success

## Install
1. Install Debian packages
    <pre>
    $ sudo apt install python3 python3-pip nginx git -y </pre>
2. Create directory for Dashy
   <pre>
   $ sudo mkdir /opt/dashy 
   $ sudo chown pi:pi /opt/dashy
   $ cd /opt/dashy
   </pre> 
3. Clone Dashy Code (note the dot at the end!)
    <pre>
    $ git clone https://github.com/muddyland/dashy.git . 
    $ cd dashy</pre>
4. Install Python Requirements
    <pre>
    $ pip3 install -R requirements.txt</pre>
5. Copy content of dashy_nginx.conf to /etc/nginx/sites-enabled/dashy.conf 
    <pre>
    $ sudo cp dashy_nginx.conf /etc/nginx/sites-enabled/dashy.conf</pre>
6. Change the nginx config as needed:
   1. Make sure the server_name matches your domain or IP address
   2. Make sure your camera IP matches the expected 192.168.1.245 (default on most Viofo Cams)
   <pre>
   $ sudo nano /etc/nginx/sites-enabled/dashy.conf </pre>
7. Remove Default Nginx config 
   <pre>
   $ sudo rm /etc/nginx/sites-enabled/default</pre>
8. Copy and modify config 
   1. Ensure to set the SSID correctly!
   2. If you do not want parking clips, you can toggle that by setting "false" 
   3. Same for Driving clips, you can disable with "false" 
   4. The cam IP could be differnt, try without changing it first
   5. Set Video path to the path where you would like the videos and thumbnails to be stored. 
      1. If NFS or SMB, just ensure they are mounted 
      2. Ensure the user has permissions to write to and create folders in this path
9. 
   <pre>
   $ cp config_template.json config.json
   $ nano config.json</pre> 
10. Enable Dashy service - Ensure the user in the service matches your user! (pi by default)
    <pre>
    $ sudo cp dashy.service /etc/systemd/system/dashy.service
    $ sudo systemctl enable dashy.service
    $ sudo systemctl start dashy </pre>
11. Turn on your Dashcam WiFi, ensure Dashy connects and shows you video! 
12. The Web UI can be accessed via:
    1.  http://{your_dashy_ip_or_hostname_here}/
    2.  Your camera can be accessed via http://{your_dashy_ip_or_hostname}:8080/

## Plans for this repo
This repo is for me to play around with, at this time, I am not sure what I am going to do with it. But it is being built around my use-cases, please keep this in mind. Open to development help, if you would like to contribute, please open PRs and I will review :) 

I hope you enjoy Dashy as much as I do! 