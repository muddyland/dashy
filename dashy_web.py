import hmac
import os
import threading
import time
import requests as http_requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, Response, jsonify
from viofo import (
    Camera, Downloads, DownloadsDB, CameraStatus, CameraOffline,
    is_safe_clip_name, is_safe_clip_url,
)
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
# Set as soon as the camera is reachable. The downloader waits on this rather
# than polling a flag on a timer, so the gap between the camera appearing and
# the first byte being fetched is the probe interval, not reconnect_interval.
camera_online = threading.Event()
downloads = Downloads(config)

# ---------------------------------------------------------------------------
# Optional access control
#
# Off by default, because Dashy is normally on a trusted home LAN. Worth turning
# on: there is no other protection on the camera control surface, and it can
# read and change the dashcam's WiFi password (cmd 3004), start and stop
# recording, and delete clips. Anything that can reach the port can do all of
# that.
# ---------------------------------------------------------------------------

AUTH_USER = os.environ.get('DASHY_USERNAME') or config_json.get('auth_username')
AUTH_PASSWORD = os.environ.get('DASHY_PASSWORD') or config_json.get('auth_password')

# Left reachable without credentials so Home Assistant polling and the PWA
# manifest keep working when auth is enabled.
AUTH_EXEMPT_PATHS = {'/api/hass', '/api/hass/locked', '/api/hass/downloading', '/manifest.json'}


@app.before_request
def require_auth():
    if not (AUTH_USER and AUTH_PASSWORD):
        return None
    if request.path in AUTH_EXEMPT_PATHS or request.path.startswith('/static/'):
        return None
    auth = request.authorization
    # compare_digest on both fields: a plain == leaks length and prefix through
    # timing.
    if auth and hmac.compare_digest(auth.username or '', AUTH_USER) \
            and hmac.compare_digest(auth.password or '', AUTH_PASSWORD):
        return None
    return Response('Authentication required', 401,
                    {'WWW-Authenticate': 'Basic realm="Dashy"'})


if AUTH_USER and AUTH_PASSWORD:
    logger.info("HTTP basic auth is enabled")
else:
    logger.info("HTTP basic auth is disabled (set DASHY_USERNAME and DASHY_PASSWORD to enable)")


def get_max(a, b):
    return max(a, b)

def get_min(a, b):
    return min(a, b)

@app.context_processor
def custom_filters():
    return dict(get_max=get_max, get_min=get_min)

# How often the status thread probes the camera. Slow while connected (the
# downloader is already exercising the link), fast while disconnected so a
# camera coming up in the driveway is picked up almost immediately instead of
# after the old 30s + 300s worst case.
CONNECTED_POLL_SECONDS = int(config_json.get("connected_poll_seconds", 30))
DISCONNECTED_POLL_SECONDS = int(config_json.get("disconnected_poll_seconds", 5))

# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

def camera_check_loop():
    """Keep the shared CameraStatus in step with the camera.

    Polls quickly while disconnected so pulling into the driveway is noticed
    within seconds, and slowly once connected so we are not opening sockets at
    a camera that is busy serving a download.

    Every iteration is wrapped: an escaping exception used to kill this thread
    outright, after which the UI reported "disconnected" forever and every
    camera command returned 503 even with the camera sitting right there.
    """
    while True:
        if download_event.is_set():
            # A transfer in flight is proof the link is up. Don't add a probe
            # socket on top of it -- the downloader reports failures itself,
            # and this loop resumes probing the moment it finishes.
            time.sleep(CONNECTED_POLL_SECONDS)
            continue
        try:
            cam.check_camera_connection(force=True)
            cam_status.update(cam)
            if cam_status.connected:
                camera_online.set()
            else:
                camera_online.clear()
        except Exception as e:
            logger.error(f"Camera check failed: {e}")
        time.sleep(CONNECTED_POLL_SECONDS if cam_status.connected else DISCONNECTED_POLL_SECONDS)


def find_missing_thumbnails():
    for f in os.listdir(downloads.download_path):
        # Only finished clips. Partial downloads (.part) are still being written
        # and would just make ffmpeg fail on every pass.
        if not is_safe_clip_name(f):
            continue
        base_name = f[:-4]
        if not os.path.isfile(os.path.join(downloads.thumbnail_path, base_name + ".jpg")):
            downloads.generate_preview(os.path.join(downloads.download_path, f), base_name)


_last_cleanup = 0.0
CLEANUP_INTERVAL = 3600


def maybe_cleanup_old_files():
    """Run retention at most hourly.

    The downloader loop now cycles as fast as the queue allows, and stat-ing
    every clip on disk each cycle is wasted work when retention is measured in
    days.
    """
    global _last_cleanup
    if time.monotonic() - _last_cleanup < CLEANUP_INTERVAL:
        return
    _last_cleanup = time.monotonic()
    cleanup_old_files()


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


def queue_locked_clips(db):
    """Add any not-yet-downloaded locked clips to the queue. Returns count added."""
    added = 0
    wanted = []
    if config_json.get('download_locked', False):
        wanted.append(("driving", "Locked Driving Mode"))
    if config_json.get('download_parking', False):
        wanted.append(("parking", "Locked Parking Mode"))

    for mode, label in wanted:
        try:
            logger.info(f"Checking for {label} clips...")
            files = cam.scrape_webserver(mode=mode, locked=True, db=db)
            for file in files:
                if not file['downloaded'] and not file['in_queue']:
                    db.append_download_queue(f"{file['dir']}/{file['filename']}")
                    added += 1
        except CameraOffline:
            raise
        except Exception as e:
            logger.error(f"Error queuing {label.lower()} clips: {e}")
    return added


def downloader_loop():
    """Background thread: queues and downloads locked clips whenever the camera is connected.

    The whole body is exception-guarded. Nothing in here may be allowed to kill
    the thread, because a dead downloader thread looks exactly like a camera
    that never connects.
    """
    scrape_interval = int(config_json.get('scrape_interval', 900))
    retry_delay = min(int(config_json.get('reconnect_interval', 15)), 30)
    db = DownloadsDB(config)

    while True:
        try:
            if not camera_online.wait(timeout=60):
                # Camera still absent; nothing to do but keep waiting. The wait
                # returns the moment the status thread sees it come back.
                continue

            queued = queue_locked_clips(db)
            if queued:
                logger.info(f"Queued {queued} new clip(s)")

            # Clear out anything loop recording has already overwritten, before
            # spending attempts on clips that cannot succeed.
            if db.queue_length():
                try:
                    downloads.prune_missing(cam)
                except CameraOffline:
                    raise
                except Exception as e:
                    logger.warning(f"Queue prune failed: {e}")

            queue_len_before = db.queue_length()
            drained = True
            if queue_len_before:
                download_event.set()
                try:
                    drained = downloads.download_video(cam=cam)
                finally:
                    download_event.clear()

            if queue_len_before:
                fire_ha_webhook(queue_len_before)

            find_missing_thumbnails()
            maybe_cleanup_old_files()

            remaining = db.queue_length()
            made_progress = remaining < queue_len_before
            if remaining and drained is not False and cam_status.connected and made_progress:
                # Still work to do and the camera is still there: go straight
                # round again. Sleeping out the full scrape interval between
                # passes is what stretched a handful of clips across an hour.
                logger.info(f"{remaining} clip(s) still queued; continuing")
                continue
            if remaining and not made_progress:
                # The queue didn't shrink, so looping immediately would spin:
                # re-listing, re-attempting the same failures, and thrashing the
                # camera in and out of playback mode without ever settling.
                logger.warning(
                    f"{remaining} clip(s) queued but none could be fetched this pass; "
                    f"backing off for {scrape_interval}s"
                )
                sleep_until_disconnected(scrape_interval)
                continue
            if remaining:
                logger.info(f"{remaining} clip(s) queued but the camera is unavailable; will retry")
                time.sleep(retry_delay)
                continue

            logger.info(f"Queue empty. Next check in {scrape_interval}s.")
            # Wake early if the camera disappears, so we return to the fast
            # idle poll instead of holding a stale connected state.
            sleep_until_disconnected(scrape_interval)

        except CameraOffline:
            logger.info("Camera not connected; waiting for it to come back.")
            time.sleep(retry_delay)
        except Exception as e:
            logger.exception(f"Downloader loop error: {e}. Retrying in {retry_delay}s.")
            time.sleep(retry_delay)


def sleep_until_disconnected(seconds):
    """Sleep up to `seconds`, returning early if the camera drops."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not cam_status.connected:
            return
        time.sleep(min(5, max(0.1, deadline - time.monotonic())))


def fire_ha_webhook(count):
    ha_webhook = config_json.get('ha_webhook_url')
    if not ha_webhook:
        return
    try:
        http_requests.post(ha_webhook, json={"downloads": count}, timeout=10)
        logger.info("Fired HA webhook")
    except Exception as e:
        logger.warning(f"Failed to fire HA webhook: {e}")

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
        return "No file URL", 400
    # Anything queued here is later appended to the camera base URL and its last
    # path segment used as a local filename, so only real clip paths are
    # accepted.
    if not is_safe_clip_url(file_url):
        logger.warning(f"Rejected queue request for {file_url!r}")
        return "Not a valid camera clip path", 400
    db.append_download_queue(file_url)
    return f"Appended {file_url} to the downloads queue"

@app.route('/storage/grab_all', methods=['POST'])
def grab_all():
    db = DownloadsDB(config)
    data = request.get_json(silent=True)
    if not data or 'files' not in data or not isinstance(data['files'], list):
        return jsonify({"error": "No files provided"}), 400
    queued = 0
    rejected = 0
    for file_url in data['files']:
        if not is_safe_clip_url(file_url):
            rejected += 1
            continue
        db.append_download_queue(file_url)
        queued += 1
    if rejected:
        logger.warning(f"Rejected {rejected} invalid queue entries")
    return jsonify({"queued": queued, "rejected": rejected})

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
    total_clips = len(video_files)
    per_page = 6
    page = 1
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, total_clips)
    video_files_paginated = video_files[start_idx:end_idx]
    last_download = video_files[0].get('name') if video_files else None
    return render_template('index.html',
                           cam_status=cam_status.connected_string,
                           hostname=cam_proxy,
                           proxy_port=config_json.get("dashy_proxy_port", 80),
                           cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}",
                           video_files=video_files_paginated,
                           total_clips=total_clips,
                           last_download=last_download)

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "Dashy",
        "short_name": "Dashy",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#00b4d8",
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

@app.route('/api/queue/prune', methods=['POST'])
def api_queue_prune():
    """Drop queued clips the camera no longer has.

    Loop recording overwrites clips continuously, so a queue that has built up
    over time contains entries that can never succeed. The downloader does this
    automatically each cycle; this endpoint is for clearing a backlog now.
    """
    try:
        removed = downloads.prune_missing(cam)
    except CameraOffline:
        return jsonify({"error": "Camera not connected"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"removed": removed, "remaining": DownloadsDB(config).queue_length()})


@app.route('/api/queue/clear', methods=['POST'])
def api_queue_clear():
    """Empty the download queue entirely.

    Only removes queue entries; downloaded clips and their history are
    untouched, so anything still on the camera can simply be queued again.
    """
    db = DownloadsDB(config)
    cleared = db.clear_queue()
    logger.info(f"Queue cleared by request ({cleared} entries)")
    return jsonify({"cleared": cleared})


@app.route('/api/queue/remove', methods=['POST'])
def api_queue_remove():
    """Remove one clip from the queue."""
    data = request.get_json(silent=True)
    if not data or 'file' not in data:
        return jsonify({"error": "Provide JSON with a 'file' key"}), 400
    db = DownloadsDB(config)
    db.remove_from_queue(data['file'])
    db.clear_progress(data['file'])
    return jsonify({"removed": data['file'], "remaining": db.queue_length()})


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
    data = request.get_json(silent=True)
    if not data or 'filename' not in data:
        return jsonify({"error": "Invalid request. Please provide JSON data with 'filename' key."}), 400

    filename = data['filename']
    # `filename` was previously joined straight onto the download directory, so
    # a request for "../../../etc/whatever" deleted files anywhere the process
    # could reach. Only bare clip names are accepted, and the resolved path is
    # re-checked against the download directory.
    if not is_safe_clip_name(filename):
        logger.warning(f"Rejected delete request for {filename!r}")
        return jsonify({"error": "Invalid filename."}), 400
    try:
        file_path = downloads.resolve_in_download_dir(filename)
    except ValueError:
        return jsonify({"error": "Invalid filename."}), 400

    if not os.path.isfile(file_path):
        return jsonify({"error": f"File '{filename}' not found."}), 404

    os.remove(file_path)
    thumbnail = os.path.join(downloads.thumbnail_path, filename[:-4] + '.jpg')
    if os.path.isfile(thumbnail):
        os.remove(thumbnail)
    db = DownloadsDB(config)
    db.remove_downloaded(filename)
    logger.info(f"Deleted {filename}")
    return jsonify({"message": f"{filename} deleted successfully."}), 200

@app.route('/storage/locked')
def list_files():
    video_files = get_video_files()
    mode_filter = request.args.get('mode', 'all')
    if mode_filter == 'driving':
        video_files = [f for f in video_files if f.get('mode') == 'Driving']
    elif mode_filter == 'parking':
        video_files = [f for f in video_files if f.get('mode') == 'Parking']
    per_page = 14
    page = request.args.get('page', 1, type=int)
    total_items = len(video_files)
    start_idx = (page - 1) * per_page
    end_idx = min(page * per_page, total_items)
    video_files_paginated = video_files[start_idx:end_idx]
    hostname = "http://" + str(str(request.host).split(":")[0])
    return render_template('list_files.html',
                           video_files=video_files_paginated,
                           hostname=hostname,
                           proxy_port=config_json.get("dashy_proxy_port", 80),
                           has_prev=page > 1,
                           has_next=end_idx < total_items,
                           total_items=total_items,
                           per_page=per_page,
                           page=page,
                           mode_filter=mode_filter,
                           start_idx=start_idx,
                           end_idx=end_idx,
                           cam_status=cam_status.connected_string,
                           cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}")

@app.route('/cam/all')
def list_all_cam_files():
    try:
        parking = request.args.get('parking', False, type=bool)
        force = request.args.get('force', False, type=bool)
        mode = "parking" if parking else "driving"
        video_files = cam.scrape_webserver(mode=mode, locked=False)
        per_page = 14
        page = request.args.get('page', 1, type=int)
        total_items = len(video_files)
        start_idx = (page - 1) * per_page
        end_idx = min(page * per_page, total_items)
        hostname = "http://" + str(str(request.host).split(":")[0]) + str(config_json.get("cam_proxy_port", 8080))
        effective_status = 'connected' if force else cam_status.connected_string
        return render_template('list_cam_files.html',
                               video_files=video_files[start_idx:end_idx],
                               hostname=hostname,
                               proxy_port=config_json.get("dashy_proxy_port", 80),
                               has_prev=page > 1,
                               has_next=end_idx < total_items,
                               total_items=total_items,
                               per_page=per_page,
                               page=page,
                               start_idx=start_idx,
                               end_idx=end_idx,
                               cam_status=effective_status,
                               cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}",
                               parking=parking,
                               locked=False)
    except Exception as e:
        return render_template('list_cam_files.html', video_files=[], error=f"Exception: {e}")

@app.route('/cam/live')
def cam_live():
    return render_template('live.html',
                           cam_status=cam_status.connected_string,
                           cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}")


@app.route('/cam/mjpeg_stream')
def mjpeg_stream():
    if not cam_status.connected:
        return "Camera not connected", 503
    try:
        r = http_requests.get(cam.mjpeg_url, stream=True, timeout=30)
        content_type = r.headers.get('Content-Type', 'multipart/x-mixed-replace; boundary=myboundary')
        def generate():
            try:
                for chunk in r.iter_content(chunk_size=4096):
                    if chunk:
                        yield chunk
            finally:
                r.close()
        return Response(generate(), content_type=content_type)
    except Exception as e:
        return str(e), 502


@app.route('/api/cam/info')
def api_cam_info():
    if downloading():
        # This endpoint is polled every few seconds by the live view and costs
        # four camera commands a time. Don't put that on a camera that is busy
        # streaming a clip; report the transfer instead.
        return jsonify({"downloading": True})
    try:
        return jsonify(cam.get_camera_info())
    except CameraOffline:
        return jsonify({"error": "Camera not connected"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/status')
def api_status():
    """Everything the UI needs to decide what to enable, in one request."""
    return jsonify({
        "connected": cam_status.connected,
        "downloading": downloading(),
    })


_known_cmds = cam.known_commands()


def is_known_cmd(cmd):
    return cmd in _known_cmds


# ---------------------------------------------------------------------------
# Camera writes are refused while a download is running.
#
# Three reasons, in order of severity:
#
#  1. Changing the WiFi name or password (cmd 3003/3004) restarts the camera's
#     access point, which drops the link and kills the transfer mid-file.
#  2. Downloads hold the camera in playback mode, where recording-related
#     settings are expected to be rejected. The write appears to fail for no
#     reason, which is exactly the "commands do nothing" confusion this
#     project has been digging out of.
#  3. The camera serves HTTP from a single thread while streaming the clip.
#     Every extra request competes with the transfer.
#
# Reads are still allowed: one bulk settings request is cheap, and a settings
# page that renders nothing during a download is worse than one that renders
# read-only.
# ---------------------------------------------------------------------------

def downloading():
    return download_event.is_set()


def busy_downloading_response():
    return jsonify({
        "error": "A download is in progress. Camera settings are locked until it finishes.",
        "downloading": True,
    }), 409


CAM_ACTIONS = {
    'start_recording': lambda: cam.start_recording(),
    'stop_recording':  lambda: cam.stop_recording(),
    'take_photo':      lambda: cam.take_photo(),
}


@app.route('/api/cam/control', methods=['POST'])
def api_cam_control():
    data = request.get_json(silent=True)
    action = data.get('action') if data else None
    handler = CAM_ACTIONS.get(action)
    if not handler:
        return jsonify({"error": f"Unknown action: {action}"}), 400
    if downloading():
        # Recording controls in particular: the camera is in playback mode for
        # the transfer, so starting a recording here would fight the downloader.
        return busy_downloading_response()
    try:
        result = handler()
    except CameraOffline:
        return jsonify({"error": "Camera not connected"}), 503
    except Exception as e:
        logger.error(f"Camera action {action} failed: {e}")
        return jsonify({"error": str(e)}), 502

    rval = result.get('rval', -1)
    if rval != 0:
        # Hand back what the camera actually said. "Nothing happened" with no
        # detail is impossible to debug; rval -13 (unsupported command) and a
        # rejected value look identical from the UI otherwise.
        logger.warning(f"Camera rejected {action}: rval={rval} raw={result.get('raw')!r}")
        return jsonify({
            "error": f"Camera rejected the command (rval={rval})",
            "rval": rval,
            "camera_response": result.get('raw'),
        }), 502
    return jsonify(result)


@app.route('/api/cam/settings/all')
def api_cam_settings_all():
    """Every setting for this model in one request.

    The settings page used to issue one request per setting on load; the camera
    could not keep up with that many connections and would start dropping them
    (and sometimes the WiFi link with it).
    """
    try:
        return jsonify({"settings": cam.read_all_settings()})
    except CameraOffline:
        return jsonify({"error": "Camera not connected"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/cam/raw')
def api_cam_raw():
    """Diagnostic passthrough: issue one camera command and show the raw reply.

    Deliberately read-only unless a value is supplied, and it echoes the
    unparsed body so an unsupported command or an unexpected firmware response
    format can be identified from the browser.
    """
    cmd = request.args.get('cmd', type=int)
    if cmd is None:
        return jsonify({"error": "Missing cmd"}), 400
    value = request.args.get('value')
    if value is not None and not is_known_cmd(cmd):
        return jsonify({"error": f"Refusing to write unknown command: {cmd}"}), 400
    if value is not None and downloading():
        return busy_downloading_response()
    try:
        result = cam.set_setting(cmd, value) if value is not None else cam.get_setting(cmd)
        return jsonify({"cmd": cmd, "sent_value": value, "result": result})
    except CameraOffline:
        return jsonify({"error": "Camera not connected"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route('/cam/settings')
def cam_settings():
    return render_template('settings.html',
                           cam_status=cam_status.connected_string,
                           cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}",
                           settings=cam.settings)


@app.route('/api/cam/setting/<int:cmd>', methods=['GET', 'POST'])
def api_cam_setting(cmd):
    if not is_known_cmd(cmd):
        # Only commands this model actually advertises. Stops the endpoint from
        # being a generic write channel into the camera's whole command space.
        return jsonify({"error": f"Unknown command: {cmd}"}), 400
    if request.method == 'POST' and downloading():
        return busy_downloading_response()
    try:
        if request.method == 'POST':
            data = request.get_json(silent=True)
            if not data or 'value' not in data:
                return jsonify({"error": "Missing 'value' in request body"}), 400
            result = cam.set_setting(cmd, data['value'])
            rval = result.get('rval', -1)
            if rval != 0:
                logger.warning(f"Camera rejected cmd {cmd}: rval={rval} raw={result.get('raw')!r}")
                return jsonify({
                    "error": f"Camera rejected the change (rval={rval})",
                    "rval": rval,
                    "camera_response": result.get('raw'),
                }), 502
            return jsonify(result)
        return jsonify(cam.get_setting(cmd))
    except CameraOffline:
        return jsonify({"error": "Camera not connected"}), 503
    except Exception as e:
        logger.error(f"Camera setting {cmd} failed: {e}")
        return jsonify({"error": str(e)}), 502


@app.route('/cam/locked')
def list_cam_files():
    try:
        parking = request.args.get('parking', False, type=bool)
        force = request.args.get('force', False, type=bool)
        mode = "parking" if parking else "driving"
        video_files = cam.scrape_webserver(mode=mode, locked=True)
        per_page = 14
        page = request.args.get('page', 1, type=int)
        total_items = len(video_files)
        start_idx = (page - 1) * per_page
        end_idx = min(page * per_page, total_items)
        hostname = "http://" + str(str(request.host).split(":")[0]) + str(config_json.get("cam_proxy_port", 8080))
        return render_template('list_cam_files.html',
                               video_files=video_files[start_idx:end_idx],
                               hostname=hostname,
                               proxy_port=config_json.get("dashy_proxy_port", 80),
                               has_prev=page > 1,
                               has_next=end_idx < total_items,
                               total_items=total_items,
                               per_page=per_page,
                               page=page,
                               start_idx=start_idx,
                               end_idx=end_idx,
                               cam_status='connected' if force else cam_status.connected_string,
                               cam_proxy=str(str(request.host).split(":")[0]) + f":{config_json.get('cam_proxy_port', 8080)}",
                               parking=parking,
                               locked=True)
    except Exception as e:
        return render_template('list_cam_files.html', video_files=[], error=f"Exception: {e}")

# ---------------------------------------------------------------------------
# Startup: initial camera check, then launch background threads
# ---------------------------------------------------------------------------

def _startup():
    # Progress rows describe a transfer that died with the previous process.
    try:
        DownloadsDB(config).clear_all_progress()
    except Exception as e:
        logger.warning(f"Could not clear stale progress rows: {e}")

    # A lockfile left behind by a crash makes the UI claim a download is running
    # forever.
    downloads.stop_download_lock()

    try:
        cam.check_camera_connection(force=True)
        cam_status.update(cam)
        if cam_status.connected:
            camera_online.set()
            if downloads.use_playback_mode:
                # A previous run killed mid-download could have left the camera
                # in playback mode, i.e. not recording. Put it back.
                cam.restore_video_mode()
    except Exception as e:
        logger.error(f"Initial camera check failed: {e}")

    threading.Thread(target=camera_check_loop, daemon=True, name="camera-check").start()
    threading.Thread(target=downloader_loop, daemon=True, name="downloader").start()


_startup()

if __name__ == '__main__':
    # debug=True exposes the Werkzeug console, which is remote code execution
    # for anyone who can reach the port. Opt in explicitly via DASHY_DEBUG for
    # local development only.
    app.run(host="0.0.0.0", debug=os.environ.get("DASHY_DEBUG") == "1", threaded=True)
