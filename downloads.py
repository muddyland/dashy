import json
import os, sys, shutil
import requests
import tempfile
import subprocess

class Downloads:
    def __init__(self, db_path, queue_path, download_path, base_url, thumbnail_path):
        self.db_path = db_path
        self.queue_path = queue_path
        self.base_url = base_url
        self.download_path = download_path
        self.thumbnail_path = thumbnail_path
        
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
        downloaded_files = Downloads.load_downloaded_files(self)
        file_name = name.split('/')[-1]
        if name not in downloaded_files:
            queue = Downloads.load_download_queue(self)
            if name in queue:
                print("Video already in queue")
            else:
                print(f"Appending file {name} to downloads queue...")
                queue.append(name)
                Downloads.save_download_queue(self, queue)
                
    def queue_length(self):
        return len(Downloads.load_download_queue(self))
    
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
            subprocess.run(command, shell=True, check=True)
            print("Thumbnail generated successfully using ffmpeg.")
        except subprocess.CalledProcessError as e:
            print(f"Error generating thumbnail: {e}")
    
    def download_video(self):
        downloaded_files = Downloads.load_downloaded_files(self)
        # Get Queue from file
        file_urls = Downloads.load_download_queue(self)
        if not file_urls:
            print("No files to download... moving on!")
            return True
        else:
            if not os.path.exists(self.download_path):
                os.makedirs(self.download_path)
            try:
                Downloads.start_download_lock(self)
                for file_url in file_urls:
                    file_name = file_url.split('/')[-1]
                    file_path = f'{self.download_path}/{file_name}'
                    with requests.get(self.base_url + file_url, stream=True) as response:
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
                    downloaded_files = Downloads.load_downloaded_files(self)
                    downloaded_files.append(file_url)
                    Downloads.save_downloaded_files(self, downloaded_files)
                    downloaded_files = Downloads.load_downloaded_files(self)
                        
                    # Remove from queue, save back to file
                    file_urls = Downloads.load_download_queue(self)
                    file_urls.remove(file_url)
                    Downloads.save_download_queue(self, file_urls)
                    file_urls = Downloads.load_download_queue(self)
                    
                # Save the updated downloaded_files list to downloads.json
                Downloads.stop_download_lock(self)
                print("Downloads complete!")
                return True

            except:
                e = sys.exc_info()
                print(f"Exception while downloading: {e}")
                return False