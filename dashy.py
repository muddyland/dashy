import os, sys
import requests
import time
from bs4 import BeautifulSoup
import json
import subprocess
from viofo import Camera, Downloads, DownloadsDB
from dashy_config import Config
global config
config = Config("config.json")
global config_json
config_json = config.config_data
               
# Function to download files from the server
def download_files(cam):
    downloads = DownloadsDB(config)
    files = cam.scrape_webserver(mode="driving", locked=True)
    if not files:
        print("No video files found.")
        return False

    for file in files:
        file_url = f"{file['dir']}/{file['filename']}"
        downloads.append_download_queue(file_url)
        
# Function to download files from the server
def download_parking_files(cam):
    downloads = DownloadsDB(config)
    files = cam.scrape_webserver(mode="driving", locked=True)

    if not files:
        print("No locked parking video files found.")

    # Create a videos directory if it doesn't exist        
    for file in files:
        file_url = f"{file['dir']}/{file['filename']}"
        downloads.append_download_queue(file_url)
        
def find_missing_thumbnails():
    downloads = Downloads(config)
    local_files = os.listdir(downloads.download_path)
    for f in local_files:
        file_name = f.split("/")[-1]
        file_name = file_name.replace(".MP4", "")
        if not os.path.isfile(f"{downloads.thumbnail_path}/{file_name}.jpg"):
            downloads.generate_preview(f"{downloads.download_path}/{file_name}.MP4", file_name)
            

# Main script
if __name__ == "__main__":
    if config_json:
        print("Configuration found!")
    else:
        print("Missing config file!")
        os._exit(100)
    
    # Path to save video to 
    video_path = f"{config_json['video_path']}/locked"
    if not os.path.exists(video_path):
        print(f"Path {video_path} doesn't exist, creating it...")
        os.makedirs(video_path)
        
    downloads = Downloads(config)
    while True:
        cam = Camera(config)
        if cam.check_camera_connection():
            downloads.stop_download_lock()
            print("Camera connected.")
            try:
              # Append Locked driving mode videos
              print("Checking for Locked clips...")
              if config_json.get('download_locked', False):
                download_files(cam)
                print("Locked Driving Mode Clips have been added to the queue (if any)")
                
              # Append parking mode files to downloads list
              if config_json.get('download_parking', False):
                download_parking_files(cam)
                print("Locked Parking Mode Clips have been added to the queue (if any)")
              
              # Download all files from queue, pass camera to downloader for connection checks
              downloads.download_video(cam=cam)
              time.sleep(10)
              find_missing_thumbnails()
              
            except Exception as e:
              print(f"Error downloading files: {str(e)}")
        else:
            print("Camera not connected.")
        # Wait for 2 minutes before checking again
        time.sleep(120)
