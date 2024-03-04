import os, sys
import requests
import time
from bs4 import BeautifulSoup
import json
import subprocess
from downloads import Downloads

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
        
# Function to extract file URLs from the HTML content
def extract_file_urls(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    file_urls = []
    
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href')
        if '.MP4' in href and 'del=1' not in href:
            file_urls.append(href)

    return file_urls
        
# Function to download files from the server
def download_files(base_url):
    response = requests.get(base_url + "/DCIM/Movie/RO")
    if response.status_code == 200:
        file_urls = extract_file_urls(response.content)

        if not file_urls:
            print("No video files found.")
            return False

        for file_url in file_urls:
            downloads.append_download_queue(file_url)
        
# Function to download files from the server
def download_parking_files(base_url):
    response = requests.get(base_url + "/DCIM/Parking/RO")
    if response.status_code == 200:
        file_urls = extract_file_urls(response.content)

        if not file_urls:
            print("No locked parking video files found.")

        # Create a videos directory if it doesn't exist        
        for file_url in file_urls:
            downloads.append_download_queue(file_url)
        
def find_missing_thumbnails():
    local_files = os.listdir(video_path)
    for f in local_files:
        file_name = f.split("/")[-1]
        file_name = file_name.replace(".MP4", "")
        if not os.path.isfile(f"{thumbnail_path}/{file_name}.jpg"):
            downloads.generate_preview(f"{video_path}/{file_name}.MP4", file_name)
            

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
    thumbnail_path = f"{config_json['video_path']}/thumbnails"
    db_path = f"{config_json['video_path']}/downloads.json"
    queue_path = f"{config_json['video_path']}/downloads_queue.json"
    downloads = Downloads(db_path, queue_path, video_path, base_url, thumbnail_path)
    while True:
        if check_wifi_connection():
            print("WiFi connected.")
            try:
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
              downloads.download_video()
              time.sleep(10)
              find_missing_thumbnails()
              
            except Exception as e:
              print(f"Error downloading files: {str(e)}")
        else:
            print("WiFi not connected.")
        # Wait for 5 minutes before checking again
        time.sleep(300)
