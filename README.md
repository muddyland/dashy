# Dashy
## WARNING:  THIS IS STILL A WORK IN PROGRESS TURN BACK NOW if you want an easy solution

## What is Dashy?
Dashy is a set of tools to aid in the ingestion, and consumption of Viofo Dashcam footage. Those of us that have a Viofo know exactly how horrible the app is, but how good the hardware is...
When you pull into the driveway, enable WiFi, and your locked videos will automatically be uploaded to a storage path of your choice (even NFS). Dashy will create thumbnails, and make the downloaded files avalible in a nice Web UI.

Dashy also proxies connections to your dashcam, allowing you to view all clips on the camera, and choose to download those files as well! The idea is to be flexible.
This is a work in progress, but I will be using it for my personal Dashcam, please keep this in mind,,,

## Assumptions before installing

1. I have only tested with a Viofo A129 Plus Duo (dual cam, 2k and 1080p), others may work
2. I assume other Dashcams use this type of setup, perhaps they will work too
3. You are using a Raspberry Pi (1,2,3,3b,4), while this may work with other Distros/Hardware, it is not built for that
4. You are running as the default Pi user, otherwise you may need to change the user in a few places
5. You are NOT using WiFi to connect to your home network (you must access via LAN cable when using Dashy, or have a second WiFi card to conenct to your network)
6. Your Raspberry Pi is close to your vehicle (Vifo cameras can be slow over WiFi)
7. You know about basics about Python pip, and Raspberry Pi (Debian) Linux. If not, please, please, please at least try to read up on these topics beforehand.
8. You have python3, python3-pip, and nginx packages installed via apt update and apt install nginx python3 python3-pip
9. I am using Debian Buster, but did test this on Bookworm with some success


## Install

1. Install Debian packages
```bash
  $ sudo apt install python3 python3-pip nginx git -y 
```
2. Create directory for Dashy
```bash
$ sudo mkdir /opt/dashy 
$ sudo chown pi:pi /opt/dashy
$ cd /opt/dashy
```

3. Clone Dashy Code (note the dot at the end!)
```bash  
  $ git clone https://github.com/muddyland/dashy.git . 
  $ cd dashy
```
4. Install Python Requirements
```bash  
  $ pip3 install -R requirements.txt
```
5. Copy content of dashy_nginx.conf to /etc/nginx/sites-enabled/dashy.conf
```bash  
  $ sudo cp dashy_nginx.conf /etc/nginx/sites-enabled/dashy.conf
```

6. Change the nginx config as needed:

* Make sure the server_name matches your domain or IP address
* Make sure your camera IP matches the expected 192.168.1.245 (default on most Viofo Cams)
* Make sure the storage location 'alias' is the correct for video and thumbnail folders, if you are not using the default 
```bash
$ sudo nano /etc/nginx/sites-enabled/dashy.conf 
```
* NOTE: SSL can be added to the above config, though it is not required to use Dashy locally. I would suggest if you have a domain, use SSL. There are plenty of guides on adding SSL to an Nginx proxy, this is all we have here, you can follow those guides, though I do not reccomend publicly exposing Dashy, and instead keep it internal and use DNS to verify your letsencrypt cert. 


1. Remove Default Nginx config
```bash
$ sudo rm /etc/nginx/sites-enabled/default
```
1. Copy and modify config
Make sure to change the following:
```json
{
    "cam_ip" : "192.168.1.254", // IP of the camera, in case it is differnt. Both the A129-Plus and A229-Plus use this IP, from my testing
    "cam_wifi_ip" : "10.x.x.x", // IP of the cam on your WiFi network, currently only the A229-Plus does this, dashy will preffer this IP over AP mode
    "cam_model" : "A229-Plus", // or A129-Plus, others may work, but the file names may not be the same
    "video_path" : "videos", // Path where to save videos/thumbnails
    "download_parking" : true, // Download Parking Mode clips
    "download_locked" : true // Download Driving Mode clips
}
```

```bash
$ cp config_template.json config.json
$ nano config.json 
```

1. Enable Dashy service - Ensure the user in the service matches your user! (pi by default)
```bash
$ sudo cp dashy.service /etc/systemd/system/dashy.service
$ sudo systemctl enable dashy.service
$ sudo systemctl start dashy 
```

1.  Turn on your Dashcam WiFi, ensure Dashy connects to the camera (top right of UI)!
* The Web UI can be accessed via (port 80):
```
http://{your_dashy_ip_or_hostname_here}/
```
* Your camera can be accessed via (port 8080) to directly view videos:
```
http://{your_dashy_ip_or_hostname}:8080/
```

## Plans for this repo
This repo is for me to play around with, at this time, I am not sure what I am going to do with it. But it is being built around my use-cases, please keep this in mind. 

Open to development help, if you would like to contribute, please open PRs and I will review :)

NOTE: This repo is mirrored to both GitLab and GitHub. I preffer GitLab but also use GitHub. The URLs are below: 

- [GitHub](https://github.com/muddyland/dashy)
- [GitLab](https://gitlab.com/muddy6910/dashy)

#### I hope you enjoy Dashy as much as I do!