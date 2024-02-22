import os, sys
import requests
import time
from bs4 import BeautifulSoup
import json
import subprocess

def read_config_file(file_path):
    try:
        with open(file_path, 'r') as file:
            config_data = json.load(file)
        return config_data
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
    except json.JSONDecodeError:
        print(f"Error: Unable to parse JSON in '{file_path}'.")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

# Function to check WiFi connection status
def check_wifi_connection():
    # Modify the command below to your specific requirements
    output = os.popen("iwgetid").read()

    if config_json['cam_ssid'] in output:
        return True
    else:
        return False

def start_download_lock():
    with open(".download-in-progress", "w") as f:
        f.write("downloading")
        
def stop_download_lock():
    os.unlink(".download-in-progress")
        
# Function to extract file URLs from the HTML content
def extract_file_urls(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    file_urls = []
    
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href')
        if '.MP4' in href and 'del=1' not in href:
            file_urls.append(href)

    return file_urls

# Function to load downloaded files data from downloads.json
def load_downloaded_files():
    if os.path.exists(db_path):
        with open(db_path, 'r') as file:
            return json.load(file)
    else:
        return []

# Function to load downloaded files data from downloads.json
def load_download_queue():
    if os.path.exists(queue_path):
        with open(queue_path, 'r') as file:
            return json.load(file)
    else:
        return []

# Function to save downloaded files data to downloads.json
def save_downloaded_files(downloaded_files):
    with open(db_path, 'w') as file:
        json.dump(downloaded_files, file)
        file.close()

# Function to save downloaded files data to downloads.json
def save_download_queue(queue):
    with open(queue_path, 'w') as file:
        json.dump(queue, file)
        file.close()

# Function to save downloaded files data to downloads.json
def append_download_queue(name):
    downloaded_files = load_downloaded_files()
    if name not in downloaded_files:
        print(f"Appending file {name} to downloads queue...")
        if os.path.exists(queue_path):
            with open(queue_path, "r") as file:
                queue = json.load(file)
                file.close()
        else:
            queue = []
            
        queue.append(name)
        
        with open(queue_path, 'w') as file:
            json.dump(queue, file)
            file.close()
    # Fail silently, as files that are already downloaded are not needed 
def download_video():
    downloaded_files = load_downloaded_files()
    # Get Queue from file
    file_urls = load_download_queue()
    if not file_urls:
        print("No files to download... moving on!")
        return True
    else:
        if not os.path.exists(video_path):
            os.makedirs(video_path)
        try:
            start_download_lock()
            for file_url in file_urls:
                file_name = file_url.split('/')[-1]
                file_path = f'{video_path}/{file_name}'
                with requests.get(base_url + file_url, stream=True) as response:
                    print(f"Downloading from URL: {base_url}{file_url}")
                    with open(file_path, 'wb') as file:
                        for chunk in response.iter_content(chunk_size=4096):  # Adjust chunk size as needed
                            if chunk:
                                file.write(chunk)
                
                # Append file to downloaded_files list, keep track of the downloads
                downloaded_files.append(file_url)
                save_downloaded_files(downloaded_files)
                downloaded_files = load_downloaded_files()
                
                # Delete video
                del_url = base_url + file_url.replace('.MP4', '.MP4?del=1')
                del_req = requests.get(del_url)
                if del_req.status_code == 200:
                    print(f"Deleted file: {del_url}")
                else:
                    print(f"Cannot Delete file: {del_url}")
                    
                # Remove from queue, save back to file
                file_urls.remove(file_url)
                save_download_queue(file_urls)
                file_urls = load_download_queue()
                
            # Save the updated downloaded_files list to downloads.json
            stop_download_lock()
            print("Downloads complete!")
            return True

        except:
            e = sys.exc_info()
            print(f"Exception while downloading: {e}")
            return False
        
# Function to download files from the server
def download_files(base_url):
    response = requests.get(base_url + "/DCIM/Movie/RO")
    if response.status_code == 200:
        file_urls = extract_file_urls(response.content)

        if not file_urls:
            print("No video files found.")
            return False

        for file_url in file_urls:
            append_download_queue(file_url)
        
# Function to download files from the server
def download_parking_files(base_url):
    response = requests.get(base_url + "/DCIM/Parking/RO")
    if response.status_code == 200:
        file_urls = extract_file_urls(response.content)

        if not file_urls:
            print("No locked parking video files found.")

        # Create a videos directory if it doesn't exist        
        for file_url in file_urls:
            append_download_queue(file_url)
            

# Main script
if __name__ == "__main__":
    
    # Example of how to use the function
    config_file_path = "config.json"
    config_json = read_config_file(config_file_path)
    if config_json:
        print("Configuration found!")
    else:
        print("Missing config file!")
        os._exit(100)
    
    base_url = f"http://{config_json['cam_ip']}"
    video_path = f"{config_json['video_path']}/locked"
    db_path = f"{config_json['video_path']}/downloads.json"
    queue_path = f"{config_json['video_path']}/downloads_queue.json"
    while True:
        if check_wifi_connection():
            print("WiFi connected.")
            try:
                  # Load downloaded files data from downloads.json
              
              # Append Locked driving mode videos
              print("Checking for Locked clips...")
              if config_json.get('download_locked', False):
                download_files(base_url)
                print("Locked Driving Mode Clips have been added to the queue (if any)")
                
              # Append parking mode files to downloads list
              if config_json.get('download_parking', False):
                download_parking_files(base_url)
                print("Locked Parking Mode Clips have been added to the queue (if any)")
              
              # Download all files from queue
              download_video()
            except Exception as e:
              print(f"Error downloading files: {str(e)}")
        else:
            print("WiFi not connected.")
        # Wait for 5 minutes before checking again
        time.sleep(180)
