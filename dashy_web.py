import os
from flask import Flask, render_template, request, Response, jsonify
import threading
import time
from viofo import Camera, Downloads, DownloadsDB
from dashy_config import Config
global config
config = Config("config.json")
global config_json
config_json = config.config_data

app = Flask(__name__, static_url_path='/static', static_folder='./static')

global cam
cam = Camera(config, check_connection=True)

def loop_camera_check():
    # Cache the cam check to save on time 
    while True:
        app.logger.info('Background cam check running!')
        cam.check_camera_connection(return_as_string=True)
        time.sleep(30)

def get_max(a, b):
    return max(a, b)

def get_min(a, b):
    return min(a, b)

@app.context_processor
def custom_filters():
    return dict(get_max=get_max, get_min=get_min)

# Get timeout, not needed in this context, but whatever 
requests_timeout = int(config_json.get("requests_timeout", 900))

downloads = Downloads(config)

@app.route('/stream')
def stream_video():
    return Response(cam.generate_video_frames(), mimetype='video/mp4')

@app.route('/storage/grab')
def upload_file():
    downloads = DownloadsDB(config)
    file_url = request.args.get('file', None, type=str)
    if not file_url:
        return "No file URL"
    else:            
        downloads.append_download_queue(file_url)
        return f"Appended {file_url} to the downloads queue"

def get_video_files():
    video_files = []
    for file_name in os.listdir(f"{config_json['video_path']}/locked"):
        video_files.append(cam.parse_filename(file_name))
    if video_files:
        return sorted(video_files, key=lambda x: x['created_date'], reverse=True)
    else:
        return []

# Dummy function for pagination
def get_paged_files(video_files, page, per_page):
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, len(video_files))
    return video_files[start_idx:end_idx]

if config_json.get("no_proxy", False):
    @app.route('/thumbnails')
    def serve_thumbnails():
        return True

@app.route('/')
def index():
    cam_proxy = "http://" + str(str(request.host).split(":")[0])
    video_files = get_video_files()
     # Pagination implementation
    per_page = 6  # Number of items per page
    page = 1
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, len(video_files))
    video_files_paginated = video_files[start_idx:end_idx]
    return render_template('index.html', cam_status=cam.connected_string, hostname=cam_proxy, cam_proxy=str(str(request.host).split(":")[0]) + ":8080", video_files=video_files_paginated)

@app.route('/manifest.json')
def manifest():
    manifest_json = {
        "name": "Dashy",
        "short_name": "Dashy",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ff0000",
        "icons": [
            {
                "src": "/static/img/car_emoji.png",
                "sizes": "192x192",
                "type": "image/png"
            }
        ]
    }
    return jsonify(manifest_json)

@app.route('/api/hass')
def hass_api():
    return jsonify({"status" : cam.connected})

@app.route('/api/hass/locked')
def hass_api_locked():
    return jsonify({"count" : len(get_video_files())})

@app.route('/api/hass/downloading')
def hass_api_is_downloading():
    return jsonify({"download_in_progress" : os.path.isfile(".download-in-progress")})

@app.route('/api/queue_len')
def api_queue_len():
    downloads = DownloadsDB(config)
    try:
        return jsonify({"count" : downloads.queue_length()})
    except:
        return jsonify({"count" : 0})

@app.route('/api/queue')
def api_queue():
    downloads = DownloadsDB(config)
    try:
        queue = downloads.load_download_queue()
        return jsonify({"queue" : queue})
    except:
        queue = []
        return jsonify({"queue" : queue})
    

@app.route('/api/storage/delete', methods=['DELETE'])
def delete_file():
    downloads = Downloads(config)
    data = request.get_json()
    app.logger.info(data)
    if not data or 'filename' not in data:
        return jsonify({"error": "Invalid request. Please provide JSON data with 'filename' key."}), 400

    filename = data['filename']
    app.logger.info(filename)
    file_path = os.path.join(downloads.download_path, filename)

    if os.path.exists(file_path):
        os.remove(file_path)
        return jsonify({"message": f"{filename} deleted successfully."}), 200
    else:
        return jsonify({"error": f"File '{filename}' not found."}), 404


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
                            cam_status=cam.connected_string,
                            cam_proxy=str(str(request.host).split(":")[0]) + ":8080"
                        )

@app.route('/cam/all')
def list_all_cam_files():
    try:
        parking = request.args.get('parking', False, type=bool)
        if parking:
            mode = "parking"
        else:
            mode = "driving"
        video_files = cam.scrape_webserver(mode=mode, locked=False)
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
                               cam_status=cam.connected_string,
                               cam_proxy=str(str(request.host).split(":")[0]) + ":8080",
                               parking=parking
                            )
    except Exception as e:
        return render_template('list_cam_files.html', video_files=[], error=f"Exception: {e}")

@app.route('/cam/locked')
def list_cam_files():
    try:
        parking = request.args.get('parking', False, type=bool)
        if parking:
            mode = "parking"
        else:
            mode = "driving"
        video_files = cam.scrape_webserver(mode=mode, locked=True)
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
                               cam_status=cam.connected_string,
                               cam_proxy=str(str(request.host).split(":")[0]) + ":8080",
                               parking=parking
                            )
    except Exception as e:
        return render_template('list_cam_files.html', video_files=[], error=f"Exception: {e}")

cam_check = threading.Thread(target=loop_camera_check)
cam_check.start()

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0")
