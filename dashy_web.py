import os, sys
from flask import Flask, render_template, request, Response, jsonify
from datetime import datetime
from bs4 import BeautifulSoup
import requests 
import subprocess
import json
import pytz
import requests_cache
requests_cache.install_cache('dashy', expire_after=300)
requests_cache.disabled()

app = Flask(__name__)

def get_max(a, b):
    return max(a, b)

def get_min(a, b):
    return min(a, b)

@app.context_processor
def custom_filters():
    return dict(get_max=get_max, get_min=get_min)

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

def generate_video_frames():
    cmd = [
        "ffmpeg", "-i", "rtsp://192.168.1.254:554/movie123.mov", 
        "-c:v", "libx264", "-preset", "ultrafast", "-f", "mpegts", "-"
    ]
    ffmpeg_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    while True:
        frame = ffmpeg_process.stdout.read(8192)
        if not frame:
            break
        yield frame

config_file_path = "config.json"
global config_json
config_json = read_config_file(config_file_path)
if config_json:
     print("Configuration found!")
else:
    print("Missing config file!")
    os._exit(100)
global base_url
base_url = f"http://{config_json['cam_ip']}"
global video_path
video_path = f"{config_json['video_path']}/locked"
global db_path
db_path = f"{config_json['video_path']}/downloads.json"
global queue_path
queue_path = f"{config_json['video_path']}/downloads_queue.json"

@app.route('/stream')
def stream_video():
    return Response(generate_video_frames(), mimetype='video/mp4')


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

def check_downloads_queue(name):
    if os.path.exists(queue_path):
        with open(queue_path, "r") as file:
            queue = json.load(file)
            file.close()
            
        if name in queue:
            return True
        else:
            return False
    else:
        return False
    

@app.route('/storage/grab')
def upload_file():
    file_url = request.args.get('file', None, type=str)
    if not file_url:
        return "No file URL"
    else:            
        append_download_queue(file_url)
        return f"Appended {file_url} to the downloads queue"

# Function to check WiFi connection status
def check_wifi_connection():
    # Modify the command below to your specific requirements
    output = os.popen("iwgetid").read()
    if config_json['cam_ssid'] in output:
        return 'connected'
    else:
        return 'disconnected'

def extract_file_urls(html_content, file_dir):
    soup = BeautifulSoup(html_content, 'html.parser')
    file_urls = []
    downloaded_files = load_downloaded_files()
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href')
        if '.MP4' in href and 'del=1' not in href:
            file_name = href.replace(f"{file_dir}/", "") 
            
            if href in downloaded_files:
               downloaded = True
            else:
                downloaded = False
            
            local_tz = pytz.timezone('America/New_York')
            created_date_from_filename = file_name.split(".")[0].split("_")[0]  # Extract the date from the filename
            created_date = datetime.strptime(created_date_from_filename, '%Y%m%d%H%M%S')
            # Convert to UTC timezone assuming the original time is in UTC
            created_date_utc = pytz.utc.localize(created_date)
            # Convert to local timezone
            created_date_local = created_date_utc.astimezone(local_tz)

            created_date_formatted = created_date_local.strftime('%m/%d/%Y %H:%M')
            if "R" in file_name.split(".")[0].split("_")[1]:
               location = "Rear"
               number = file_name.split(".")[0].split("_")[1]
            elif "F" in file_name.split(".")[0].split("_")[1]:
               location = "Front"
               number = file_name.split(".")[0].split("_")[1]
            else:
               location = "Unknown"
               number = None
            
            file_urls.append({'filename': file_name, 'name': created_date_formatted, 'created_date': created_date_formatted, 'location' : location, 'number' : number, 'dir' : file_dir, 'downloaded' : downloaded, 'in_queue' : check_downloads_queue(href)})

    if file_urls:
        return sorted(file_urls, key=lambda x: x['created_date'], reverse=True)
    else:
        return []
def get_video_files():
    video_files = []
    for file_name in os.listdir(video_path):
        if file_name.endswith('.mp4') or file_name.endswith('.MP4'):
            local_tz = pytz.timezone('America/New_York')
            created_date_from_filename = file_name.split(".")[0].split("_")[0]  # Extract the date from the filename
            created_date = datetime.strptime(created_date_from_filename, '%Y%m%d%H%M%S')
            # Convert to UTC timezone assuming the original time is in UTC
            created_date_utc = pytz.utc.localize(created_date)
            # Convert to local timezone
            created_date_local = created_date_utc.astimezone(local_tz)

            created_date_formatted = created_date_local.strftime('%m/%d/%Y %H:%M')
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
              
            video_files.append({'filename': file_name, 'name': created_date_formatted, 'created_date': created_date_formatted, 'location' : location, 'number' : number, 'dir' : '/locked', "mode" : mode})
    if video_files:
        return sorted(video_files, key=lambda x: x['created_date'], reverse=True)
    else:
        return []
# Dummy function for pagination
def get_paged_files(video_files, page, per_page):
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, len(video_files))
    return video_files[start_idx:end_idx]

@app.route('/')
def index():
    cam_proxy = str(str(request.host).split(":")[0])
    return render_template('index.html', cam_status=check_wifi_connection(), hostname=cam_proxy, cam_proxy=str(str(request.host).split(":")[0]) + ":8080")

@app.route('/api/hass')
def hass_api():
    return jsonify({"status" : check_wifi_connection()})

@app.route('/api/hass/locked')
def hass_api_locked():
    return jsonify({"count" : len(get_video_files())})

@app.route('/api/hass/downloading')
def hass_api_is_downloading():
    return jsonify({"download_in_progress" : os.path.isfile(".download-in-progress")})

@app.route('/api/queue_len')
def api_queue_len():
    try:
        with open(queue_path, "r") as queue_json:
            queue = json.load(queue_json)
            queue_json.close()
        
        return jsonify({"count" : len(queue)})
    except:
        return jsonify({"count" : 0})

@app.route('/api/queue')
def api_queue():
    try:
        with open(queue_path, "r") as queue_json:
            queue = json.load(queue_json)
            queue_json.close()
        
        return jsonify({"queue" : queue})
    except:
        return jsonify({"queue" : queue})

@app.route('/storage/locked')
def list_files():
    video_files = get_video_files()
     # Pagination implementation
    per_page = 14  # Number of items per page
    page = request.args.get('page', 1, type=int)
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, len(video_files))
    video_files_paginated = video_files[start_idx:end_idx]
    
    # Calculate pagination properties
    has_prev = page > 1
    has_next = end_idx < len(video_files)
    total_items = len(video_files)

    hostname = "http://" + str(str(request.host).split(":")[0]) + ":80"
    return render_template('list_files.html', 
                            video_files=video_files_paginated, 
                            hostname=hostname, has_prev=has_prev, 
                            has_next=has_next,
                            total_items=total_items,
                            per_page=per_page,
                            page=page,
                            cam_status=check_wifi_connection(),
                            cam_proxy=str(str(request.host).split(":")[0]) + ":8080"
                        )

@app.route('/cam/all')
def list_all_cam_files():
    base_url = "http://192.168.1.254"
    file_dir = "/DCIM/Movie"
    parking = request.args.get('parking', False, type=bool)
    if parking:
        file_dir = "/DCIM/Parking"
    try:
        with requests_cache.enabled():
          response = requests.get(base_url + file_dir, timeout=60)
    except:
        e = sys.exc_info()
        return render_template('list_cam_files.html', video_files=[], error=f"HTTP error: {e}")
    if response.status_code == 200:
        video_files = extract_file_urls(response.content, file_dir)
        # Pagination implementation
        per_page = 14  # Number of items per page
        page = request.args.get('page', 1, type=int)
        start_idx = (page - 1) * per_page
        end_idx = min(page * per_page, len(video_files))
        video_files_paginated = video_files[start_idx:end_idx]
        
        # Calculate pagination properties
        has_prev = page > 1
        has_next = end_idx < len(video_files)
        total_items = len(video_files)

        hostname = "http://" + str(str(request.host).split(":")[0]) + ":8080"
        return render_template('list_cam_files.html', 
                               video_files=video_files_paginated, 
                               hostname=hostname,
                               has_prev=has_prev, 
                               has_next=has_next,
                               total_items=total_items,
                               per_page=per_page,
                               page=page,
                               cam_status=check_wifi_connection(),
                               cam_proxy=str(str(request.host).split(":")[0]) + ":8080",
                               parking=parking
                            )
    else:
        return render_template('list_cam_files.html', video_files=[], error=f"HTTP error: {response.status_code}")

@app.route('/cam/locked')
def list_cam_files():
    base_url = "http://192.168.1.254"
    file_dir = "/DCIM/Movie/RO"
    parking = request.args.get('parking', False, type=bool)
    if parking:
        file_dir = "/DCIM/Parking/RO"
        
    try:
        response = requests.get(base_url + file_dir, timeout=10)
    except:
        e = sys.exc_info()
        return render_template('list_cam_files.html', video_files=[], error=f"HTTP error: {e}")
    if response.status_code == 200:
        video_files = extract_file_urls(response.content, file_dir)
        # Pagination implementation
        per_page = 14  # Number of items per page
        
        page = request.args.get('page', 1, type=int)
        start_idx = (page - 1) * per_page
        end_idx = min(page * per_page, len(video_files))
        video_files_paginated = video_files[start_idx:end_idx]
        
        # Calculate pagination properties
        has_prev = page > 1
        has_next = end_idx < len(video_files)
        total_items = len(video_files)

        hostname = "http://" + str(str(request.host).split(":")[0]) + ":8080"
        return render_template('list_cam_files.html', 
                               video_files=video_files_paginated, 
                               hostname=hostname, 
                               has_prev=has_prev, 
                               has_next=has_next,
                               total_items=total_items,
                               per_page=per_page,
                               page=page,
                               cam_status=check_wifi_connection(),
                               cam_proxy=str(str(request.host).split(":")[0]) + ":8080",
                               parking=parking
                            )
    else:
        return render_template('list_cam_files.html', video_files=[], error=f"HTTP error: {response.status_code}")

if __name__ == '__main__':
    # Example of how to use the function
    app.run(debug=True, host="0.0.0.0")
