#!/usr/bin/env python3
"""
Mock Viofo camera HTTP server for UI development/testing.

Mimics the directory listing and file serving behaviour that viofo.py expects.
Run with sudo (port 80 requires root), then set CAM_IP=127.0.0.1 in your config.

Usage:
    sudo python3 mock_camera.py [--port PORT]
    sudo python3 mock_camera.py --port 80   # default, matches Camera class
"""

import argparse
from datetime import datetime, timedelta
from flask import Flask, Response, request, jsonify
import random

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Fake clip generation
# ---------------------------------------------------------------------------

def make_filename(dt, index, location, parking=False):
    """Generate a realistic A229-Plus filename.

    Format: YYYY_MMDD_HHMMSS_<index>[P]<L>.MP4

    The lens letter is last, immediately before the extension, and the parking
    marker precedes it -- parking clips end PF.MP4 / PR.MP4. This matters
    because lens detection keys off the character before the dot.
    """
    loc_char = 'F' if location == 'Front' else 'R'
    park_char = 'P' if parking else ''
    seq = str(index).zfill(6)
    return f"{dt.strftime('%Y_%m%d_%H%M%S')}_{seq}{park_char}{loc_char}.MP4"


def generate_clips(count, base_dt, parking=False):
    """Return a list of (filename, directory_path) tuples."""
    clips = []
    for i in range(count):
        dt = base_dt - timedelta(minutes=i * 3)
        for loc in ['Front', 'Rear']:
            clips.append(make_filename(dt, i + 1, loc, parking=parking))
    return clips


now = datetime.now()

CLIPS = {
    '/DCIM/Movie':           generate_clips(8, now, parking=False),
    '/DCIM/Movie/RO':        generate_clips(5, now - timedelta(hours=1), parking=False),
    '/DCIM/Movie/Parking':   generate_clips(4, now - timedelta(hours=3), parking=True),
    '/DCIM/Movie/Parking/RO': generate_clips(3, now - timedelta(hours=5), parking=True),
}

# ---------------------------------------------------------------------------
# Tiny valid MP4 (black 1-frame, ~300 bytes, enough for Content-Length)
# Used for all file download responses.
# ---------------------------------------------------------------------------
DUMMY_MP4 = bytes([
    # ftyp box
    0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70,
    0x6d, 0x70, 0x34, 0x32, 0x00, 0x00, 0x00, 0x00,
    0x6d, 0x70, 0x34, 0x32, 0x69, 0x73, 0x6f, 0x6d,
    # mdat box (empty)
    0x00, 0x00, 0x00, 0x08, 0x6d, 0x64, 0x61, 0x74,
    # moov box (minimal)
    0x00, 0x00, 0x00, 0x08, 0x6d, 0x6f, 0x6f, 0x76,
])

# ---------------------------------------------------------------------------
# Directory listing helper
# ---------------------------------------------------------------------------

def render_directory(path, filenames):
    links = []
    for fname in filenames:
        href = f"{path}/{fname}"
        # Also add a delete link (which viofo.py filters out via del=1)
        links.append(f'<a href="{href}">{fname}</a>')
        links.append(f'<a href="{href}?del=1">Delete</a>')
    body = "\n".join(links)
    return f"<html><body>{body}</body></html>"

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/DCIM/Movie')
def movie_all():
    return render_directory('/DCIM/Movie', CLIPS['/DCIM/Movie'])


@app.route('/DCIM/Movie/RO')
def movie_locked():
    return render_directory('/DCIM/Movie/RO', CLIPS['/DCIM/Movie/RO'])


@app.route('/DCIM/Movie/Parking')
def movie_parking():
    return render_directory('/DCIM/Movie/Parking', CLIPS['/DCIM/Movie/Parking'])


@app.route('/DCIM/Movie/Parking/RO')
def movie_parking_locked():
    return render_directory('/DCIM/Movie/Parking/RO', CLIPS['/DCIM/Movie/Parking/RO'])


@app.route('/DCIM/Movie/<filename>')
@app.route('/DCIM/Movie/RO/<filename>')
@app.route('/DCIM/Movie/Parking/<filename>')
@app.route('/DCIM/Movie/Parking/RO/<filename>')
def serve_file(filename):
    if not filename.endswith('.MP4'):
        return 'Not found', 404
    # ?del=1 is the delete link the camera's own directory index uses.
    if request.args.get('del') == '1':
        return 'OK' if _delete_clip(filename) else ('Not found', 404)
    # 404 for anything not in the listing, like a real camera does once loop
    # recording has overwritten a clip. Serving every plausible name would hide
    # the queue's handling of files that no longer exist.
    if not any(filename in names for names in CLIPS.values()):
        return 'Not found', 404
    return Response(
        DUMMY_MP4,
        status=200,
        headers={
            'Content-Type': 'video/mp4',
            'Content-Length': str(len(DUMMY_MP4)),
            'Content-Disposition': f'attachment; filename="{filename}"',
        }
    )


# ---------------------------------------------------------------------------
# Mock camera settings API  (?custom=1&cmd=N and ?custom=1&cmd=N&param_0=V)
# ---------------------------------------------------------------------------

# Control/status responses (state is mutable so start/stop recording works in the mock)
_mock_recording = False

MOCK_STATUS = {
    1001: lambda: {"rval": 0, "type": 1001, "msg": "photo_capture"},
    3012: lambda: {"rval": 0, "type": 3012, "cur_value": "VIOFO_A229_V2.1_20240115"},
    3017: lambda: {"rval": 0, "type": 3017, "param": 28672},
    3019: lambda: {"rval": 0, "type": 3019, "param": 85},
    3024: lambda: {"rval": 0, "type": 3024, "param": 1},
    3014: lambda: {"rval": 0, "type": 3014, "msg": "record" if _mock_recording else "idle",
                   "param": 1 if _mock_recording else 0},
}

MOCK_SETTINGS = {
    2002: {"cur_value": "2160P 30fps", "options": ["2160P 30fps", "1440P 60fps", "1080P 120fps", "1080P 60fps", "720P 240fps"]},
    2003: {"cur_value": "3 Min", "options": ["1 Min", "2 Min", "3 Min", "5 Min", "10 Min"]},
    2004: {"cur_value": "On", "options": ["On", "Off"]},
    2005: {"cur_value": "0", "options": ["-2.0", "-1.5", "-1.0", "-0.5", "0", "+0.5", "+1.0", "+1.5", "+2.0"]},
    2006: {"cur_value": "Off", "options": ["Off", "Low", "Medium", "High"]},
    2007: {"cur_value": "On", "options": ["On", "Off"]},
    2008: {"cur_value": "On", "options": ["On", "Off"]},
    2011: {"cur_value": "Medium", "options": ["Off", "Low", "Medium", "High"]},
    9212: {"cur_value": "High", "options": ["High", "Low"]},
    9214: {"cur_value": "On", "options": ["On", "Off"]},
    9218: {"cur_value": "Color", "options": ["Color", "B&W", "Auto"]},
    9219: {"cur_value": "Off", "options": ["On", "Off"]},
    9220: {"cur_value": "Off", "options": ["On", "Off"]},
    9221: {"cur_value": "Medium", "options": ["Off", "Low", "Medium", "High"]},
    9225: {"cur_value": "On", "options": ["On", "Off"]},
    9318: {"cur_value": "On", "options": ["On", "Off"]},
    9319: {"cur_value": "On", "options": ["On", "Off"]},
    9333: {"cur_value": "On", "options": ["On", "Off"]},
    9405: {"cur_value": "3 Min", "options": ["Off", "1 Min", "2 Min", "3 Min", "5 Min"]},
    9406: {"cur_value": "60Hz", "options": ["50Hz", "60Hz"]},
    9410: {"cur_value": "On", "options": ["On", "Off"]},
    9411: {"cur_value": "UTC-5", "options": ["UTC-12", "UTC-11", "UTC-10", "UTC-9", "UTC-8", "UTC-7", "UTC-6", "UTC-5", "UTC-4", "UTC-3", "UTC-2", "UTC-1", "UTC+0", "UTC+1", "UTC+2", "UTC+3", "UTC+4", "UTC+5", "UTC+6", "UTC+7", "UTC+8", "UTC+9", "UTC+10", "UTC+11", "UTC+12"]},
    9412: {"cur_value": "mph", "options": ["km/h", "mph"]},
    9421: {"cur_value": "Normal", "options": ["Normal", "Low Bitrate", "Super Night Vision", "Off"]},
    9428: {"cur_value": "5 Min", "options": ["1 Min", "3 Min", "5 Min", "10 Min"]},
    9094: {"cur_value": "On", "options": ["On", "Off"]},
    3003: {"cur_value": "VIOFO_A229", "options": []},
    3004: {"cur_value": "12345678", "options": []},
    3007: {"cur_value": "Off", "options": ["Off", "1 Min", "3 Min", "5 Min", "10 Min"]},
    8225: {"cur_value": "Off", "options": ["Off", "Flip Horizontal", "Flip Vertical", "180°"]},
    8226: {"cur_value": "Off", "options": ["Off", "Flip Horizontal", "Flip Vertical", "180°"]},
}


# Wire format. Real Viofo firmware answers in XML; the JSON mode is kept
# because some models/firmware do reply in JSON and Dashy has to cope with
# both.
#   --xml  (default) mimic real hardware
#   --json legacy behaviour
XML_MODE = True

# Real cameras take &par= for numbers and &str= for text. --lenient accepts
# param_0 as well, to exercise Dashy's fallback path.
ACCEPT_PARAM_0 = False

_mock_mode = 1  # 1 = video/record, 2 = playback


def _delete_clip(filename):
    """Remove a clip from the mock card. True if it was there."""
    for names in CLIPS.values():
        if filename in names:
            names.remove(filename)
            return True
    return False

# Set false to emulate firmware without the cmd 3015 file list, so the
# HTML directory-scraping fallback gets exercised.
SUPPORT_FILE_LIST = True

# Emulate firmware whose bulk settings query (cmd 3014) isn't supported, so the
# per-command fallback path gets exercised.
SUPPORT_BULK_SETTINGS = True

# Emulate a camera that streams over RTSP rather than MJPEG over HTTP.
LIVE_VIEW_RTSP = False

# Live view only serves frames once it has been started with cmd 2015.
_live_view_on = False


def _xml(**fields):
    body = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    return Response(f'<?xml version="1.0" encoding="UTF-8" ?>\n<Function>{body}</Function>',
                    mimetype='text/xml')


def _reply(cmd, status=0, **extra):
    if XML_MODE:
        return _xml(Cmd=cmd, Status=status, **extra)
    payload = {"rval": status, "type": cmd}
    payload.update({k.lower(): v for k, v in extra.items()})
    return jsonify(payload)


@app.route('/')
def index_or_api():
    global _mock_recording, _mock_mode
    if request.args.get('custom') != '1':
        return _index()

    cmd = request.args.get('cmd', type=int)
    if cmd is None:
        return _reply(0, -1), 400

    # Value may arrive as par (numeric), str (text), or param_0 (legacy).
    par = request.args.get('par')
    text = request.args.get('str')
    param_0 = request.args.get('param_0')
    if param_0 is not None and not ACCEPT_PARAM_0:
        # This is what real firmware does: accepts the request, reports an
        # error status, and silently doesn't apply the change.
        return _reply(cmd, -14)

    value = par if par is not None else (text if text is not None else param_0)

    if value is not None:
        # SET / ACTION
        if cmd == 4003:                       # DELETE_ONE_FILE, path in &str=
            name = str(value).replace('\\', '/').rsplit('/', 1)[-1]
            return _reply(cmd, 0 if _delete_clip(name) else -14)
        if cmd == 2015:                       # LIVE_VIEW_CONTROL
            globals()['_live_view_on'] = (str(value) == '1')
            return _reply(cmd, 0)
        if cmd == 3001:                       # CHANGE_MODE
            _mock_mode = int(value)
            return _reply(cmd, 0)
        if cmd == 2001:                       # MOVIE_RECORD
            _mock_recording = (value == '1')
            return _reply(cmd, 0)
        if cmd in MOCK_SETTINGS:
            MOCK_SETTINGS[cmd]['cur_value'] = value.replace('+', ' ')
            return _reply(cmd, 0)
        return _reply(cmd, -13)

    # GET
    if cmd == 3016:                           # HEART_BEAT
        return _reply(cmd, 0)
    if cmd == 2019:                           # LIVE_VIEW_URL
        if LIVE_VIEW_RTSP:
            return _reply(cmd, 0, MovieLiveViewLink=f'rtsp://{request.host.split(":")[0]}:554/movie123.mov')
        return _reply(cmd, 0, MovieLiveViewLink=f'http://{request.host.split(":")[0]}:8192')
    if cmd == 3015:                           # GET_FILE_LIST
        if not SUPPORT_FILE_LIST:
            return _reply(cmd, -13)
        blocks = []
        for path, names in CLIPS.items():
            for i, fname in enumerate(names):
                dt = datetime.now() - timedelta(minutes=i * 3)
                # Real cameras report a DOS-style path with a drive letter.
                fpath = ("A:" + path.replace('/', '\\') + '\\' + fname)
                blocks.append(
                    "<File>"
                    f"<NAME>{fname}</NAME>"
                    f"<FPATH>{fpath}</FPATH>"
                    f"<SIZE>{123456789}</SIZE>"
                    f"<TIMECODE>{int(dt.timestamp())}</TIMECODE>"
                    f"<TIME>{dt.strftime('%Y/%m/%d %H:%M:%S')}</TIME>"
                    f"<ATTR>{33 if '/RO' in path else 32}</ATTR>"
                    "</File>"
                )
        return Response(
            '<?xml version="1.0" encoding="UTF-8" ?>\n<LIST>' + "".join(blocks) + '</LIST>',
            mimetype='text/xml')
    if cmd == 3014:                           # bulk settings table
        if not SUPPORT_BULK_SETTINGS:
            return _reply(cmd, -13)
        if XML_MODE:
            blocks = "".join(
                f"<Function><Cmd>{c}</Cmd><Status>{i}</Status></Function>"
                for i, c in enumerate(sorted(MOCK_SETTINGS))
            )
            return Response(f'<?xml version="1.0" encoding="UTF-8" ?>\n{blocks}',
                            mimetype='text/xml')
        return jsonify(MOCK_STATUS[3014]())
    if cmd in MOCK_STATUS:
        if XML_MODE:
            data = MOCK_STATUS[cmd]()
            extra = {}
            if 'cur_value' in data:
                extra['String'] = data['cur_value']
            elif 'param' in data:
                extra['Value'] = data['param']
            return _reply(cmd, data.get('param', 0) if 'param' in data else 0, **extra)
        return jsonify(MOCK_STATUS[cmd]())
    setting = MOCK_SETTINGS.get(cmd)
    if setting:
        if XML_MODE:
            return _reply(cmd, 0, String=setting['cur_value'])
        return jsonify({"rval": 0, "type": cmd, "param": 0,
                        "cur_value": setting['cur_value'],
                        "options": setting['options']})
    return _reply(cmd, -13)


def _index():
    """Simple status page so you can verify the mock is running."""
    total = sum(len(v) for v in CLIPS.values())
    rows = "".join(
        f"<tr><td><a href='{path}'>{path}</a></td><td>{len(files)}</td></tr>"
        for path, files in CLIPS.items()
    )
    return f"""
    <html><head><title>Mock Viofo Camera</title></head><body>
    <h2>Mock Viofo Camera</h2>
    <p>Serving {total} fake clips across {len(CLIPS)} directories.</p>
    <table border="1" cellpadding="6">
        <tr><th>Path</th><th>Clips</th></tr>
        {rows}
    </table>
    </body></html>
    """


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Mock Viofo camera server')
    parser.add_argument('--port', type=int, default=80,
                        help='Port to listen on (default: 80, requires sudo)')
    parser.add_argument('--host', default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--json', action='store_true',
                        help='Reply in JSON instead of XML. Real firmware uses XML; '
                             'use this to exercise the JSON path.')
    parser.add_argument('--no-bulk-settings', action='store_true',
                        help='Pretend cmd 3014 is unsupported, to exercise the '
                             'per-command settings fallback.')
    parser.add_argument('--rtsp-live-view', action='store_true',
                        help='Report an RTSP live view URL instead of MJPEG.')
    parser.add_argument('--no-file-list', action='store_true',
                        help='Pretend cmd 3015 is unsupported, to exercise the '
                             'HTML directory-listing fallback.')
    parser.add_argument('--lenient', action='store_true',
                        help='Also accept the legacy &param_0= form. Real cameras '
                             'reject it, which is what this mock does by default.')
    args = parser.parse_args()

    XML_MODE = not args.json
    ACCEPT_PARAM_0 = args.lenient
    globals()['XML_MODE'] = XML_MODE
    globals()['ACCEPT_PARAM_0'] = ACCEPT_PARAM_0
    globals()['SUPPORT_FILE_LIST'] = not args.no_file_list
    globals()['SUPPORT_BULK_SETTINGS'] = not args.no_bulk_settings
    globals()['LIVE_VIEW_RTSP'] = args.rtsp_live_view
    print(f"Supports cmd 3015 file list: {not args.no_file_list}")
    print(f"Wire format: {'XML (like real hardware)' if XML_MODE else 'JSON'}")
    print(f"Accepts &param_0=: {ACCEPT_PARAM_0}")

    print(f"Starting mock camera on {args.host}:{args.port}")
    print(f"Set CAM_IP=127.0.0.1 in your Dashy config to use this mock.")
    print(f"Browse to http://localhost:{args.port}/ to verify it is running.")
    app.run(host=args.host, port=args.port, debug=False)
