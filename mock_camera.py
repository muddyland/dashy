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
from flask import Flask, Response
import random

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Fake clip generation
# ---------------------------------------------------------------------------

def make_filename(dt, index, location, parking=False):
    """Generate a realistic A229-Plus filename.
    Format: YYYY_MMDD_HHMMSS_<index><L>[P].MP4
    """
    loc_char = 'F' if location == 'Front' else 'R'
    park_char = 'P' if parking else ''
    seq = str(index).zfill(6)
    return f"{dt.strftime('%Y_%m%d_%H%M%S')}_{seq}{loc_char}{park_char}.MP4"


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
    return Response(
        DUMMY_MP4,
        status=200,
        headers={
            'Content-Type': 'video/mp4',
            'Content-Length': str(len(DUMMY_MP4)),
            'Content-Disposition': f'attachment; filename="{filename}"',
        }
    )


@app.route('/')
def index():
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
    args = parser.parse_args()

    print(f"Starting mock camera on {args.host}:{args.port}")
    print(f"Set CAM_IP=127.0.0.1 in your Dashy config to use this mock.")
    print(f"Browse to http://localhost:{args.port}/ to verify it is running.")
    app.run(host=args.host, port=args.port, debug=False)
