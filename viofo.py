import os, sys, shutil
from bs4 import BeautifulSoup
import requests
import json
import sqlite3
import socket
import threading
from dashy_config import Config
import subprocess
from datetime import datetime, timedelta
import logging
logger = logging.getLogger("[viofo.py]")
handler = logging.StreamHandler()
formatter = logging.Formatter('%(name)s - %(levelname)s - %(asctime)s -  %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class CameraStatus:
    """Thread-safe shared camera state. Updated by a background thread;
    read by web routes and the downloader without ever blocking on a socket."""
    def __init__(self):
        self._lock = threading.Lock()
        self.connected = False
        self.connected_string = 'disconnected'
        self.connected_ip = None
        self.base_url = None

    def update(self, camera):
        with self._lock:
            self.connected = camera.connected
            self.connected_string = camera.connected_string
            self.connected_ip = camera.connected_ip
            self.base_url = camera.base_url

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
        self.cam_ip_list = [self.cam_ip, self.cam_wifi_ip] if self.cam_wifi_ip and isinstance(self.cam_wifi_ip, str) else [self.cam_ip]
        if check_connection:
            self.check_camera_connection()
            
    def check_camera_connection(self, return_as_string=False):
        result = None
        
        for ip in self.cam_ip_list:
            logger.info(f"Checking {self.cam_model} IP {ip}")
            # Check to see if port 80 is open on wifi IP first
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                result = s.connect_ex((ip, 80))
                
        
            if result == 0:
                self.connected_ip = ip
                self.connected = True
                self.connected_string = "connected"
                self.base_url = f"http://{ip}:80"
                self.result = result
                break
            else:
                self.connected = False
                self.connected_ip = None
                self.connected_string = "disconnected"
                self.base_url = None
                self.result = result
                
        logger.info(f"{self.cam_model} {self.connected_string} from IP {self.connected_ip}")
        
        if return_as_string:
            return self.connected_string
        else:
            return self.connected
        
    def scrape_webserver(self, mode="driving", locked=True):
        if not self.connected or not self.base_url:
            raise Exception("Camera is not connected, impossible to scrape")
        
        # Open Downloads DB to tag a video as downloaded
        downloads = DownloadsDB(self.config)
        
        if mode == "parking" and locked:
            file_dir = "/DCIM/Movie/Parking/RO"
        elif mode == "driving" and locked:
            file_dir = "/DCIM/Movie/RO"
        elif mode == "driving" and not locked:
            file_dir = "/DCIM/Movie"
        elif mode == "parking" and not locked:
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
        elif response.status_code == 404:
            raise Exception(f"Camera does not have any video files in: {file_dir}, are you sure there are any files here?")
        else:
            raise Exception(f"Camera did not return expected status code 200: {response.status_code} - {response.text}")
        
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
            if "R" in file_name.split(".")[0].split("_")[3]:
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

        try:
            while True:
                frame = ffmpeg_process.stdout.read(8192)
                if not frame:
                    break
                yield frame
        finally:
            ffmpeg_process.kill()
            ffmpeg_process.wait()

class Downloads:
    def __init__(self, config, cam_url=None):
        if not isinstance(config, Config):
            raise Exception("Config is not of class Config")
        config_data = config.config_data
        
        download_path = f"{config_data['video_path']}/locked"
        thumbnail_path = f"{config_data['video_path']}/thumbnails"
        timeout = int(config_data.get('request_timeout', 900))
            
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
            logger.info("Lockfile has been cleared")
        except FileNotFoundError:
            logger.warning("No lockfile to clear")
        
    def generate_preview(self, file_path, file_name):
        logger.info("Generating Thumbnail...")
        if not os.path.isdir(f"{self.thumbnail_path}"):
            os.mkdir(f"{self.thumbnail_path}")
        thumbnail_name = file_name.replace(".MP4", "") + ".jpg"
        thumbnail_path = f"{self.thumbnail_path}/{thumbnail_name}"
        command = f'ffmpeg -ss 1 -i "{file_path}" -vframes 1 -q:v 2 "{thumbnail_path}"'
        
        try:
            cwd = os.path.dirname(os.path.realpath(__file__))
            subprocess.run(command, shell=True, check=True, cwd=cwd)
            logger.info("Thumbnail generated successfully using ffmpeg.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error generating thumbnail: {e}")
    
    def download_video(self, cam=None):
        downloaded_files = self.db.load_downloaded_files()
        # Get Queue from file
        file_urls = self.db.load_download_queue()
        if not file_urls:
            logger.info("No files to download... moving on!")
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
                
                min_free_bytes = 1 * 1024 * 1024 * 1024  # 1 GB
                free_bytes = shutil.disk_usage(self.download_path).free
                if free_bytes < min_free_bytes:
                    logger.error(f"Insufficient disk space: {free_bytes / 1024**3:.1f} GB free, need at least 1 GB. Skipping downloads.")
                    return False

                self.start_download_lock()
                for file_url in file_urls:
                    try:
                        if cam and isinstance(cam, Camera):
                            status = cam.check_camera_connection()
                            if not status:
                                raise Exception("Connection to camera has been lost")
                            
                        file_name = file_url.split('/')[-1]
                        file_path = f'{self.download_path}/{file_name}'
                        
                        total_bytes = 0
                        bytes_downloaded = 0
                        with requests.get(self.base_url + file_url, stream=True, timeout=self.timeout) as response:
                            if response.status_code == 200:
                                total_bytes = int(response.headers.get('Content-Length', 0))
                                chunk_count = 0
                                with open(file_path, "wb+") as temp_file:
                                    logger.info(f"Downloading from URL: {self.base_url}{file_url} to {temp_file.name}")
                                    for chunk in response.iter_content(chunk_size=2048):
                                        if chunk:
                                            temp_file.write(chunk)
                                            bytes_downloaded += len(chunk)
                                            chunk_count += 1
                                            if chunk_count % 1024 == 0:
                                                self.db.set_progress(file_url, bytes_downloaded, total_bytes)

                                    logger.info(f"File successfully downloaded and moved to: {file_path}")
                            else:
                                logger.error("Failed to download file {} Status: {}".format(file_url, response.status_code))
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
                        self.db.clear_progress(file_url)
                    except Exception as e:
                        logger.info(f"Exception: {e} Skipping file: {file_url}")
                # Save the updated downloaded_files list to downloads.json
                self.stop_download_lock()
                logger.info("Downloads complete!")
                return True

            except:
                e = sys.exc_info()
                logger.error(f"Exception while downloading: {e}")
                return False
class DownloadsDB:
    def __init__(self, config):
        if not isinstance(config, Config):
            raise Exception("Config is not of class Config")
        config_data = config.config_data

        self.db_path = f"{config_data['video_path']}/dashy.db"
        self._init_db()
        self._migrate_json(config_data['video_path'])

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS downloaded (url TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS progress (
                    url TEXT PRIMARY KEY,
                    bytes_downloaded INTEGER DEFAULT 0,
                    total_bytes INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)

    def _migrate_json(self, video_path):
        old_db_path = f"{video_path}/downloads.json"
        old_queue_path = f"{video_path}/downloads_queue.json"

        if os.path.exists(old_db_path):
            with open(old_db_path, 'r') as f:
                urls = json.load(f)
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany("INSERT OR IGNORE INTO downloaded (url) VALUES (?)", [(u,) for u in urls])
            os.rename(old_db_path, old_db_path + ".migrated")
            logger.info(f"Migrated {len(urls)} entries from downloads.json to SQLite")

        if os.path.exists(old_queue_path):
            with open(old_queue_path, 'r') as f:
                urls = json.load(f)
            with sqlite3.connect(self.db_path) as conn:
                conn.executemany("INSERT OR IGNORE INTO queue (url) VALUES (?)", [(u,) for u in urls])
            os.rename(old_queue_path, old_queue_path + ".migrated")
            logger.info(f"Migrated {len(urls)} entries from downloads_queue.json to SQLite")

    def load_downloaded_files(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT url FROM downloaded").fetchall()
        return [r[0] for r in rows]

    def load_download_queue(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT url FROM queue").fetchall()
        return [r[0] for r in rows]

    def save_downloaded_files(self, downloaded_files):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM downloaded")
            conn.executemany("INSERT OR IGNORE INTO downloaded (url) VALUES (?)", [(u,) for u in downloaded_files])

    def save_download_queue(self, queue):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM queue")
            conn.executemany("INSERT OR IGNORE INTO queue (url) VALUES (?)", [(u,) for u in queue])

    def append_download_queue(self, name):
        if not self.check_downloaded(name):
            if self.check_downloads_queue(name):
                logger.warning("Video already in queue")
            else:
                logger.info(f"Appending file {name} to downloads queue...")
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("INSERT OR IGNORE INTO queue (url) VALUES (?)", (name,))

    def queue_length(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
        return row[0]

    def check_downloaded(self, file):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT 1 FROM downloaded WHERE url = ?", (file,)).fetchone()
        return row is not None

    def check_downloads_queue(self, name):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT 1 FROM queue WHERE url = ?", (name,)).fetchone()
        return row is not None

    def set_progress(self, url, bytes_downloaded, total_bytes):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO progress (url, bytes_downloaded, total_bytes, updated_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (url, bytes_downloaded, total_bytes))

    def get_progress(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT url, bytes_downloaded, total_bytes, updated_at
                FROM progress
                ORDER BY updated_at DESC LIMIT 1
            """).fetchone()
        if row:
            return {'url': row[0], 'bytes_downloaded': row[1], 'total_bytes': row[2], 'updated_at': row[3]}
        return None

    def clear_progress(self, url):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM progress WHERE url = ?", (url,))

