import os
import time
from viofo import Camera, Downloads, DownloadsDB
from dashy_config import Config
import logging
logger = logging.getLogger("[dashy.py]")
handler = logging.StreamHandler()
formatter = logging.Formatter('%(name)s - %(levelname)s - %(asctime)s -  %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

global config
config = Config("config.json")
global config_json
config_json = config.config_data
               
# Function to download files from the server
def download_files(cam):
    downloads = DownloadsDB(config)
    files = cam.scrape_webserver(mode="driving", locked=True)
    if not files:
        logger.warning("No video files found.")
        return False

    for file in files:
        file_url = f"{file['dir']}/{file['filename']}"
        downloads.append_download_queue(file_url)
        
# Function to download files from the server
def download_parking_files(cam):
    downloads = DownloadsDB(config)
    files = cam.scrape_webserver(mode="parking", locked=True)

    if not files:
        logger.warning("No locked parking video files found.")

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
        logger.info("Configuration found!")
    else:
        logger.error("Missing config file!")
        os._exit(100)
    
    # Path to save video to 
    video_path = f"{config_json['video_path']}/locked"
        
    downloads = Downloads(config)
    while True:
        cam = Camera(config, check_connection=True)
        if cam.connected:
            downloads.stop_download_lock()
            logger.info("Camera connected.")
            try:
              # Append Locked driving mode videos
              
              if config_json.get('download_locked', False):
                try:
                    logger.info("Checking for Locked Driving Mode clips...")
                    download_files(cam)
                    logger.info("Locked Driving Mode Clips have been added to the queue (if any)")
                except Exception as e:
                    logger.error(f"Error adding files to queue: {str(e)}")
                
              # Append parking mode files to downloads list
              if config_json.get('download_parking', False):
                try:
                    logger.info("Checking for Locked Parking Mode clips...")
                    download_parking_files(cam)
                    logger.info("Locked Parking Mode Clips have been added to the queue (if any)")
                except Exception as e:
                    logger.error(f"Error adding files to queue: {str(e)}")
              
              # Download all files from queue, pass camera to downloader for connection checks
              downloads.download_video(cam=cam)
              time.sleep(10)
              find_missing_thumbnails()
              scrape_interval = config_json.get('scrape_interval', 900)
              print(f"Sleeping for {scrape_interval} minutes...")
              time.sleep(scrape_interval)
            except Exception as e:
              scrape_interval = config_json.get('scrape_interval', 900)
              logger.error(f"Error downloading files: {str(e)} Retrying in: {scrape_interval}")
              time.sleep(scrape_interval)
        else:
            reconnect_interval = config_json.get('reconnect_interval', 300)
            logger.info("Camera not connected, checking again in {} seconds...".format(reconnect_interval))
            # Wait for N minutes before checking again
            time.sleep(reconnect_interval)
