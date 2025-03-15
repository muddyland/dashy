import os, sys
from bs4 import BeautifulSoup
import requests
import json
import pytz
import requests_cache
import socket
from dashy_config import Config
import os, sys, shutil
import tempfile
import subprocess
from functools import lru_cache, wraps
from datetime import datetime, timedelta

def timed_lru_cache(seconds: int, maxsize: int = None):

    def wrapper_cache(func):
        func = lru_cache(maxsize=maxsize)(func)
        func.lifetime = timedelta(seconds=seconds)
        func.expiration = datetime.now() + func.lifetime

        @wraps(func)
        def wrapped_func(*args, **kwargs):
            print('Checking Camera Status')
            if datetime.now() >= func.expiration:
                print('Connection cache expired, rechecking')
                func.cache_clear()
                func.expiration = datetime.now() + func.lifetime
            else:
                print(f"Using camera status cache, will recheck at {func.expiration}")

            return func(*args, **kwargs)

        return wrapped_func

    return wrapper_cache

class Camera:
    def __init__(self, config, check_connection=False):
        if not isinstance(config, Config):
            raise Exception("You must pass the config as a Config class")
        
        # Get data from config 
        config_data = config.config_data
        
        # Create variables from config
        self.config = config
        self.cam_ip = config_data.get("cam_ip", "192.168.1.254")
        self.cam_wifi_ip = config_data.get("cam_wifi_ip", None)
        self.cam_model = config_data.get('cam_model', "A129-Plus")
        
        if check_connection:
            self.check_camera_connection()
        
    @timed_lru_cache(30)    
    def check_camera_connection(self, return_as_string=False):
        result = None
        if self.cam_wifi_ip:
            # Check to see if port 80 is open on this IP 
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                result = s.connect_ex((self.cam_wifi_ip, 80))
                
        
        if result != 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                result = s.connect_ex((self.cam_ip, 80))
        else:
            self.connected_ip = self.cam_wifi_ip
            self.connected = True

            self.base_url = f"http://{self.connected_ip}:80"

            self.result = result
            if return_as_string:
                return "connected"
            else:
                return True
        

        if result == 0:
            self.connected_ip = self.cam_ip
            self.connected = True
            
            self.base_url = f"http://{self.connected_ip}:80"
            
            self.result = result
            if return_as_string:
                return "connected"
            else:
                return True
        else:
            self.connected = False
            self.connected_ip = None
            
            self.base_url = None
            
            self.result = result
            if return_as_string:
                return "disconnected"
            else:
                return False
        
    def scrape_webserver(self, mode="driving", locked=True):
        if not self.connected or not self.base_url:
            raise Exception("Camera is not connected, impossible to scrape")
        
        # Open Downloads DB to tag a video as downloaded
        downloads = DownloadsDB(self.config)
        
        if mode == "parking" and not locked:
            file_dir = "/DCIM/Parking/RO"
        elif mode == "driving" and not locked:
            file_dir = "/DCIM/Movie"
        elif mode == "driving" and locked:
            file_dir = "/DCIM/Movie/RO"
        elif mode == "parking" and locked:
            file_dir = "/DCIM/Movie/Parking"
        
        response = requests.get(self.base_url + file_dir, timeout=180)
        if response.status_code == 200:
            html_content = response.text

            soup = BeautifulSoup(html_content, 'html.parser')
            file_urls = []
            for a_tag in soup.find_all('a'):
                href = a_tag.get('href')
                if '.MP4' in href and 'del=1' not in href:
                    file_name = href.replace(f"{file_dir}/", "") 
                    file_info = self.parse_filename(file_name)
                    file_info['downloaded'] = downloads.check_downloaded(href)
                    file_info['in_queue'] = downloads.check_downloads_queue(href)
                    file_info['dir'] = file_dir
                    file_urls.append(file_info)

            if file_urls:
                return sorted(file_urls, key=lambda x: x['created_date'], reverse=True)
            else:
                return []
        else:
            return Exception("Failed to connect to camera")
        
    def parse_filename(self, file_name):
        if self.cam_model == "A129-Plus":
            created_date_from_filename = file_name.split(".")[0].split("_")[0]  # Extract the date from the filename
            created_date = datetime.strptime(created_date_from_filename, '%Y%m%d%H%M%S')
            created_date_formatted = created_date.strftime("%m/%d/%Y %I:%M %p")
            if "R" in file_name.split(".")[0].split("_")[1]:
                location = "Rear"
                number = file_name.split(".")[0].split("_")[1]
            elif "F" in file_name.split(".")[0].split("_")[1]:
                location = "Front"
                number = file_name.split(".")[0].split("_")[1]
            else:
                location = "Unknown"
                number = None
            
            if "P" in file_name.split(".")[0].split("_")[1]:
                mode = "Parking"
            else:
                mode = "Driving"
            thumbnail_name = file_name.replace(".MP4", ".jpg")
        elif self.cam_model == "A229-Plus":
            # Example: 2025_0313_042216_000047F.MP4
            plain_filename = file_name.split(".")[0]
            date_name_split = plain_filename.split("_")
            year = date_name_split[0]
            month_day = date_name_split[1]
            full_time = date_name_split[2]
            created_date = datetime.strptime(f"{year}{month_day}{full_time}", '%Y%m%d%H%M%S')
            created_date_formatted = created_date.strftime("%m/%d/%Y %I:%M %p")
            if "R" in file_name.split(".")[0].split("_")[1]:
                location = "Rear"
                number = file_name.split(".")[0].split("_")[3]
            elif "F" in file_name.split(".")[0].split("_")[3]:
                location = "Front"
                number = file_name.split(".")[0].split("_")[3]
            else:
                location = "Unknown"
                number = None
            
            if "P" in file_name.split(".")[0].split("_")[3]:
                mode = "Parking"
            else:
                mode = "Driving"
                     
        thumbnail_name = file_name.replace(".MP4", ".jpg")    
        return {
                'filename': file_name, 
                'name': created_date_formatted, 
                'created_date' : created_date, 
                'created_date_formatted': created_date_formatted, 
                'location' : location, 
                'number' : number, 
                'dir' : '/locked', 
                "mode" : mode, 
                'thumbnail' : thumbnail_name
            }
    def generate_video_frames(self):
        if not self.connected or not self.connected_ip:
            raise Exception("Camera is not connected")
        cmd = [
            "ffmpeg", "-i", f"rtsp://{self.connected_ip}:554/movie123.mov", 
            "-c:v", "libx264", "-preset", "ultrafast", "-f", "mpegts", "-"
        ]
        ffmpeg_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        while True:
            frame = ffmpeg_process.stdout.read(8192)
            if not frame:
                break
            yield frame

class Downloads:
    def __init__(self, config, cam_url=None):
        if not isinstance(config, Config):
            raise Exception("Config is not of class Config")
        config_data = config.config_data
        
        download_path = f"{config_data['video_path']}/locked"
        thumbnail_path = f"{config_data['video_path']}/thumbnails"
        timeout = int(config_data.get('request_timeout', 900))
        
        if not os.path.exists(download_path):
            print(f"Path {download_path} doesn't exist, creating it...")
            os.makedirs(download_path)
            
        if not os.path.exists(thumbnail_path):
            print(f"Path {thumbnail_path} doesn't exist, creating it...")
            os.makedirs(thumbnail_path)
            
        self.config = config
        self.db = DownloadsDB(config)
        self.base_url = cam_url
        self.download_path = download_path
        self.thumbnail_path = thumbnail_path
        self.timeout = timeout
    
    def start_download_lock(self):
        with open(".download-in-progress", "w") as f:
            f.write("downloading")
            
    def stop_download_lock(self):
        try:
            os.unlink(".download-in-progress")
            print("Lockfile has been cleared")
        except FileNotFoundError:
            print("No lockfile to clear")
        
    def generate_preview(self, file_path, file_name):
        print("Generating Thumbnail...")
        if not os.path.isdir(f"{self.thumbnail_path}"):
            os.mkdir(f"{self.thumbnail_path}")
        thumbnail_name = file_name.replace(".MP4", "") + ".jpg"
        thumbnail_path = f"{self.thumbnail_path}/{thumbnail_name}"
        command = f'ffmpeg -ss 1 -i "{file_path}" -vframes 1 -q:v 2 "{thumbnail_path}"'
        
        try:
            cwd = os.path.dirname(os.path.realpath(__file__))
            subprocess.run(command, shell=True, check=True, cwd=cwd)
            print("Thumbnail generated successfully using ffmpeg.")
        except subprocess.CalledProcessError as e:
            print(f"Error generating thumbnail: {e}")
    
    def download_video(self):
        downloaded_files = self.db.load_downloaded_files()
        # Get Queue from file
        file_urls = self.db.load_download_queue()
        if not file_urls:
            print("No files to download... moving on!")
            return True
        else:
            if not os.path.exists(self.download_path):
                os.makedirs(self.download_path)
            try:
                # If there is no base_url, easy enough to grab it from the camera
                if not self.base_url:
                    cam = Camera(self.config, check_connection=True)
                    if not cam.connected:
                        raise Exception("Camera is not connected, impossible to download")
                    self.base_url = cam.base_url
                
                self.start_download_lock()
                for file_url in file_urls:
                    file_name = file_url.split('/')[-1]
                    file_path = f'{self.download_path}/{file_name}'
                    
                    with requests.get(self.base_url + file_url, stream=True, timeout=self.timeout) as response:
                        if response.status_code == 200:
                            with open(file_path, "wb+") as temp_file:
                                print(f"Downloading from URL: {self.base_url}{file_url} to {temp_file.name}")
                                for chunk in response.iter_content(chunk_size=2048):
                                    if chunk:
                                        temp_file.write(chunk)

                                print(f"File successfully downloaded and moved to: {file_path}")
                        else:
                            print("Failed to download file {} Status: {}".format(file_url, response.status_code))
                            continue
                
                    # Append file to downloaded_files list, keep track of the downloads
                    downloaded_files = self.db.load_downloaded_files()
                    downloaded_files.append(file_url)
                    self.db.save_downloaded_files(downloaded_files)
                    downloaded_files = self.db.load_downloaded_files()
                        
                    # Remove from queue, save back to file
                    file_urls = self.db.load_download_queue()
                    file_urls.remove(file_url)
                    self.db.save_download_queue(file_urls)
                    file_urls = self.db.load_download_queue()
                    
                # Save the updated downloaded_files list to downloads.json
                self.stop_download_lock()
                print("Downloads complete!")
                return True

            except:
                e = sys.exc_info()
                print(f"Exception while downloading: {e}")
                return False
class DownloadsDB:
    def __init__(self, config):
        # Ensure we have a proper config object
        if not isinstance(config, Config):
            raise Exception("Config is not of class Config")
        config_data = config.config_data
        
        # Define paths to various files
        db_path = f"{config_data['video_path']}/downloads.json"
        queue_path = f"{config_data['video_path']}/downloads_queue.json"
        
        # Save variables to class
        self.db_path = db_path
        self.queue_path = queue_path
        
    # Function to load downloaded files data from downloads.json
    def load_downloaded_files(self):
        if os.path.exists(self.db_path):
            with open(self.db_path, 'r') as file:
                return json.load(file)
        else:
            return []

    # Function to load downloaded files data from downloads.json
    def load_download_queue(self):
        if os.path.exists(self.queue_path):
            with open(self.queue_path, 'r') as file:
                return json.load(file)
        else:
            return []

    # Function to save downloaded files data to downloads.json
    def save_downloaded_files(self, downloaded_files):
        with open(self.db_path, 'w') as file:
            json.dump(downloaded_files, file)
            file.close()

    # Function to save downloaded files data to downloads.json
    def save_download_queue(self, queue):
        with open(self.queue_path, 'w') as file:
            json.dump(queue, file)
            file.close()

    # Function to save downloaded files data to downloads.json
    def append_download_queue(self, name):
        downloaded_files = DownloadsDB.load_downloaded_files(self)
        file_name = name.split('/')[-1]
        if name not in downloaded_files:
            queue = DownloadsDB.load_download_queue(self)
            if name in queue:
                print("Video already in queue")
            else:
                print(f"Appending file {name} to downloads queue...")
                queue.append(name)
                DownloadsDB.save_download_queue(self, queue)
                
    def queue_length(self):
        return len(DownloadsDB.load_download_queue(self))
    
    def check_downloaded(self, file):
        downloaded_files = self.load_downloaded_files()
        if file in downloaded_files:
            return True
        else:
            return False
    
    def check_downloads_queue(self, name):
        if os.path.exists(self.queue_path):
            with open(self.queue_path, "r") as file:
                queue = json.load(file)
                file.close()
                
            if name in queue:
                return True
            else:
                return False
        else:
            return False
    
