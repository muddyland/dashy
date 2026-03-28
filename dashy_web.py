import os
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, request, Response, jsonify
from viofo import Camera, Downloads, DownloadsDB, CameraStatus
from dashy_config import Config
import logging

logger = logging.getLogger("[dashy_web.py]")
handler = logging.StreamHandler()
formatter = logging.Formatter('%(name)s - %(levelname)s - %(asctime)s -  %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

config = Config("config.json")
config_json = config.config_data

app = Flask(__name__, static_url_path='/static', static_folder='./static')

cam = Camera(config)
cam_status = CameraStatus()
download_event = threading.Event()
downloads = Downloads(config)

def get_max(a, b):
    return max(a, b)

def get_min(a, b):
    return min(a, b)

@app.context_processor
def custom_filters():
    return dict(get_max=get_max, get_min=get_min)

requests_timeout = int(config_json.get("requests_timeout", 900))

# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

def camera_check_loop():
    """Checks camera connection every 30s and updates shared CameraStatus."""
    while True:
        time.sleep(30)
        cam.check_camera_connection()
        cam_status.update(cam)
        logger.info(f"Camera status: {cam_status.connected_string}")


def find_missing_thumbnails():
    local_files = os.listdir(downloads.download_path)
    for f in local_files:
        file_name = f.split("/")[-1].replace(".MP4", "")
        if not os.path.isfile(f"{downloads.thumbnail_path}/{file_name}.jpg"):
            downloads.generate_preview(f"{downloads.download_path}/{file_name}.MP4", file_name)


def cleanup_old_files():
    if not config_json.get('retention_enabled', True):
        return
    retention_days = config_json.get('retention_days', 180)
    cutoff = datetime.now() - timedelta(days=retention_days)
    db = DownloadsDB(config)
    deleted = 0
    for file_name in os.listdir(downloads.download_path):
        if not file_name.endswith('.MP4'):
            continue
        file_path = os.path.join(downloads.download_path, file_name)
        if datetime.fromtimestamp(os.path.getmtime(file_path)) < cutoff:
            try:
                os.remove(file_path)
                thumbnail = os.path.join(downloads.thumbnail_path, file_name.replace('.MP4', '.jpg'))
                if os.path.exists(thumbnail):
                    os.remove(thumbnail)
                db.remove_downloaded(file_name)
                logger.info(f"Deleted old clip: {file_name}")
                deleted += 1
            except Exception as e:
                logger.error(f"Failed to delete {file_name}: {e}")
    logger.info(f"Cleanup complete. {deleted} clip(s) deleted older than {retention_days} days.")


def downloader_loop():
    """Background thread: queues and downloads locked clips whenever the camera is connected."""
    while True:
        if cam_status.connected:
            try:
                db = DownloadsDB(config)

                if config_json.get('download_locked', False):
                    try:
                        logger.info("Checking for Locked Driving Mode clips...")
                        files = cam.scrape_webserver(mode="driving", locked=True)
                        for file in files:
                            db.append_download_queue(f"{file['dir']}/{file['filename']}")
                    except Exception as e:
                        logger.error(f"Error queuing driving clips: {e}")

                if config_json.get('download_parking', False):
                    try:
                        logger.info("Checking for Locked Parking Mode clips...")
                        files = cam.scrape_webserver(mode="parking", locked=True)
                        for file in files:
                            db.append_download_queue(f"{file['dir']}/{file['filename']}")
                    except Exception as e:
                        logger.error(f"Error queuing parking clips: {e}")

                download_event.set()
                try:
                    downloads.download_video(cam=cam)
                finally:
                    download_event.clear()

                time.sleep(10)
                find_missing_thumbnails()
                cleanup_old_files()

                scrape_interval = config_json.get('scrape_interval', 900)
                logger.info(f"Sleeping for {scrape_interval}s...")
                time.sleep(scrape_interval)

            except Exception as e:
                scrape_interval = config_json.get('scrape_interval', 900)
                logger.error(f"Downloader loop error: {e}. Retrying in {scrape_interval}s.")
                time.sleep(scrape_interval)
        else:
            reconnect_interval = config_json.get('reconnect_interval', 300)
            logger.info(f"Camera not connected, checking again in {reconnect_interval}s...")
            time.sleep(reconnect_interval)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/stream')
def stream_video():
    return Response(cam.generate_video_frames(), mimetype='video/mp4')

@app.route('/storage/grab')
def upload_file():
    db = DownloadsDB(config)
    file_url = request.args.get('file', None, type=str)
    if not file_url:
        return "No file URL"
    db.append_download_queue(file_url)
    return f"Appended {file_url} to the downloads queue"

def get_video_files():
    video_files = []
    for file_name in os.listdir(f"{config_json['video_path']}/locked"):
        video_files.append(cam.parse_filename(file_name))
    if video_files:
        return sorted(video_files, key=lambda x: x['created_date'], reverse=True)
    return []

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
    per_page = 6
    page = 1
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, len(video_files))
    video_files_paginated = video_files[start_idx:end_idx]
    return render_template('index.html',
                           cam_status=cam_status.connected_string,
                           hostname=cam_proxy,
                           proxy_port=config_json.get("dashy_proxy_port", 80),
                           cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}",
                           video_files=video_files_paginated)

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "Dashy",
        "short_name": "Dashy",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ff0000",
        "icons": [{"src": "/static/img/car_emoji.png", "sizes": "192x192", "type": "image/png"}]
    })

@app.route('/api/hass')
def hass_api():
    return jsonify({"status": cam_status.connected})

@app.route('/api/hass/locked')
def hass_api_locked():
    return jsonify({"count": len(get_video_files())})

@app.route('/api/hass/downloading')
def hass_api_is_downloading():
    return jsonify({"download_in_progress": download_event.is_set()})

@app.route('/api/queue_len')
def api_queue_len():
    db = DownloadsDB(config)
    try:
        return jsonify({"count": db.queue_length()})
    except:
        return jsonify({"count": 0})

@app.route('/api/queue')
def api_queue():
    db = DownloadsDB(config)
    try:
        return jsonify({"queue": db.load_download_queue()})
    except:
        return jsonify({"queue": []})

@app.route('/api/progress')
def api_progress():
    db = DownloadsDB(config)
    progress = db.get_progress()
    if progress:
        total = progress['total_bytes']
        downloaded = progress['bytes_downloaded']
        percent = round((downloaded / total) * 100) if total > 0 else 0
        return jsonify({
            'active': True,
            'url': progress['url'],
            'filename': progress['url'].split('/')[-1],
            'bytes_downloaded': downloaded,
            'total_bytes': total,
            'percent': percent
        })
    return jsonify({'active': False})

@app.route('/api/storage/delete', methods=['DELETE'])
def delete_file():
    data = request.get_json()
    logger.info(data)
    if not data or 'filename' not in data:
        return jsonify({"error": "Invalid request. Please provide JSON data with 'filename' key."}), 400
    filename = data['filename']
    file_path = os.path.join(downloads.download_path, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        return jsonify({"message": f"{filename} deleted successfully."}), 200
    return jsonify({"error": f"File '{filename}' not found."}), 404

@app.route('/storage/locked')
def list_files():
    video_files = get_video_files()
    per_page = 14
    page = request.args.get('page', 1, type=int)
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, len(video_files))
    video_files_paginated = video_files[start_idx:end_idx]
    hostname = "http://" + str(str(request.host).split(":")[0])
    return render_template('list_files.html',
                           video_files=video_files_paginated,
                           hostname=hostname,
                           proxy_port=config_json.get("dashy_proxy_port", 80),
                           has_prev=page > 1,
                           has_next=end_idx < len(video_files),
                           total_items=len(video_files),
                           per_page=per_page,
                           page=page,
                           cam_status=cam_status.connected_string,
                           cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}")

@app.route('/cam/all')
def list_all_cam_files():
    try:
        parking = request.args.get('parking', False, type=bool)
        mode = "parking" if parking else "driving"
        video_files = cam.scrape_webserver(mode=mode, locked=False)
        per_page = 14
        page = request.args.get('page', 1, type=int)
        start_idx = (page - 1) * per_page
        end_idx = min(page * per_page, len(video_files))
        hostname = "http://" + str(str(request.host).split(":")[0]) + str(config_json.get("cam_proxy_port", 8080))
        return render_template('list_cam_files.html',
                               video_files=video_files[start_idx:end_idx],
                               hostname=hostname,
                               proxy_port=config_json.get("dashy_proxy_port", 80),
                               has_prev=page > 1,
                               has_next=end_idx < len(video_files),
                               total_items=len(video_files),
                               per_page=per_page,
                               page=page,
                               cam_status=cam_status.connected_string,
                               cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}",
                               parking=parking)
    except Exception as e:
        return render_template('list_cam_files.html', video_files=[], error=f"Exception: {e}")

@app.route('/cam/locked')
def list_cam_files():
    try:
        parking = request.args.get('parking', False, type=bool)
        mode = "parking" if parking else "driving"
        video_files = cam.scrape_webserver(mode=mode, locked=True)
        per_page = 14
        page = request.args.get('page', 1, type=int)
        start_idx = (page - 1) * per_page
        end_idx = min(page * per_page, len(video_files))
        hostname = "http://" + str(str(request.host).split(":")[0]) + str(config_json.get("cam_proxy_port", 8080))
        return render_template('list_cam_files.html',
                               video_files=video_files[start_idx:end_idx],
                               hostname=hostname,
                               proxy_port=config_json.get("dashy_proxy_port", 80),
                               has_prev=page > 1,
                               has_next=end_idx < len(video_files),
                               total_items=len(video_files),
                               per_page=per_page,
                               page=page,
                               cam_status=cam_status.connected_string,
                               cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}",
                               parking=parking)
    except Exception as e:
        return render_template('list_cam_files.html', video_files=[], error=f"Exception: {e}")

# ---------------------------------------------------------------------------
# Startup: initial camera check, then launch background threads
# ---------------------------------------------------------------------------

cam.check_camera_connection()
cam_status.update(cam)

threading.Thread(target=camera_check_loop, daemon=True).start()
threading.Thread(target=downloader_loop, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0")
