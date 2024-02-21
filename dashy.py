import os, sys
import requests
import time
from bs4 import BeautifulSoup
import json
import subprocess
import requests_cache
requests_cache.install_cache('dashy', expire_after=300)
requests_cache.disabled()


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

# Function to save downloaded files data to downloads.json
def save_downloaded_files(downloaded_files):
    with open(db_path, 'w') as file:
        json.dump(downloaded_files, file)

def download_manual():
    if os.path.exists('manual_downloads.json'):
        with open('manual_downloads.json', 'r') as file:
            file_urls = json.load(file)
    else:
        return True
        
    if file_urls:
        start_download_lock()
        try:
            for file_url in file_urls:
                if file_url not in downloaded_files:
                    file_name = file_url.split('/')[-1]
                    file_path = f'{video_path}/{file_name}'
                    with requests.get(base_url + file_url, stream=True) as response:
                        print(f"Downloading from URL: {base_url}{file_url}")
                        with open(file_path, 'wb') as file:
                            for chunk in response.iter_content(chunk_size=4096):  # Adjust chunk size as needed
                                if chunk:
                                    file.write(chunk)
                    
                    # Append file to downloaded_files list
                    downloaded_files.append(file_url)

                    del_url = base_url + file_url.replace('.MP4', '.MP4?del=1')
                    del_req = requests.get(del_url)
                    if del_req.status_code == 200:
                        print(f"Deleted file: {del_url}")
                    else:
                        print(f"Cannot Delete file: {del_url}")
                    file_urls.remove(file_url)
                    with open("manual_downloads.json", "w") as f:
                        json.dump(file_urls, f)
        except:
            e = sys.exc_info()
            print(f"Exception while downloading: {e}")
            return False
        # Save the updated downloaded_files list to downloads.json
        save_downloaded_files(downloaded_files)
        stop_download_lock()
        return True
    else:
        return True
# Function to download files from the server
def download_files(base_url, downloaded_files):
    with requests_cache.enabled():
      response = requests.get(base_url + "/DCIM/Movie/RO")
    if response.status_code == 200:
        file_urls = extract_file_urls(response.content)

        if not file_urls:
            print("No video files found.")
            return False
        else:
            start_download_lock()

        # Create a videos directory if it doesn't exist
        if not os.path.exists(video_path):
            os.makedirs(video_path)

        for file_url in file_urls:
            if file_url not in downloaded_files:
                file_name = file_url.split('/')[-1]
                file_path = f'{video_path}/{file_name}'
                with requests.get(base_url + file_url, stream=True) as response:
                    print(f"Downloading from URL: {base_url}{file_url}")
                    with open(file_path, 'wb') as file:
                        for chunk in response.iter_content(chunk_size=4096):  # Adjust chunk size as needed
                            if chunk:
                                file.write(chunk)
                
                print(f'{file_name} downloaded successfully.')
                # Append file to downloaded_files list
                downloaded_files.append(file_url)

                del_url = base_url + file_url.replace('.MP4', '.MP4?del=1')
                del_req = requests.get(del_url)
                if del_req.status_code == 200:
                  print(f"Deleted file: {del_url}")
                else:
                  print(f"Cannot Delete file: {del_url}")
        # Save the updated downloaded_files list to downloads.json
        save_downloaded_files(downloaded_files)
        stop_download_lock()
        
# Function to download files from the server
def download_parking_files(base_url, downloaded_files):
    with requests_cache.enabled():
        response = requests.get(base_url + "/DCIM/Parking/RO")
    if response.status_code == 200:
        file_urls = extract_file_urls(response.content)

        if not file_urls:
            print("No video files found.")
            return False
        else:
            start_download_lock()

        # Create a videos directory if it doesn't exist
        if not os.path.exists(video_path):
            os.makedirs(video_path)
        
        for file_url in file_urls:
            if file_url not in downloaded_files:
                file_name = file_url.split('/')[-1]
                file_path = f'{video_path}/{file_name}'
                with requests.get(base_url + file_url, stream=True) as response:
                    print(f"Downloading from URL: {base_url}{file_url}")
                    with open(file_path, 'wb') as file:
                        for chunk in response.iter_content(chunk_size=4096):  # Adjust chunk size as needed
                            if chunk:
                                file.write(chunk)
                
                print(f'{file_name} downloaded successfully.')
                # Append file to downloaded_files list
                downloaded_files.append(file_url)

                del_url = base_url + file_url.replace('.MP4', '.MP4?del=1')
                del_req = requests.get(del_url)
                if del_req.status_code == 200:
                  print(f"Deleted file: {del_url}")
                else:
                  print(f"Cannot Delete file: {del_url}")
        # Save the updated downloaded_files list to downloads.json
        save_downloaded_files(downloaded_files)
        stop_download_lock()
def check_nfs(path):
  return os.path.exists(path)


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
    while True:
        if check_wifi_connection():
            print("WiFi connected.")
            try:
                  # Load downloaded files data from downloads.json
              downloaded_files = load_downloaded_files()
              print("Checking for new videos to download...")
              if config_json.get('download_locked', False):
                download_files(base_url, downloaded_files)
              if config_json.get('download_parking', False):
                download_parking_files(base_url, downloaded_files)
                print("Videos downloaded successfully.")
                print("Downloading manual files...")
              if download_manual():
                print("Successfully downloaded manual files...")
              else:
                print("Unsuccessful...")
            except Exception as e:
              print(f"Error downloading files: {str(e)}")
        else:
            print("WiFi not connected.")
        # Wait for 5 minutes before checking again
        time.sleep(180)
