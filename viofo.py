import os, sys, shutil
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
import contextlib
import ipaddress
import json
import re
import sqlite3
import socket
import threading
import time
import xml.etree.ElementTree as ET
from dashy_config import Config
import subprocess
from datetime import datetime, timedelta
import logging
logger = logging.getLogger("[viofo.py]")
handler = logging.StreamHandler()
formatter = logging.Formatter('%(name)s - %(levelname)s - %(asctime)s -  %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Camera HTTP transport
#
# The dashcam runs a minimal single-threaded HTTP server on an SoC that is also
# busy encoding video. It copes badly with several sockets at once: opening a
# handful in parallel (the settings page used to fire ~40) makes it stall, reset
# connections, and in the worst case drop the WiFi association entirely.
#
# So everything here funnels through one Session per purpose, with keep-alive
# and a pool of exactly one connection, and control traffic is serialised behind
# a lock. At most two sockets are ever open to the camera: one for control, one
# for the file being downloaded.
# ---------------------------------------------------------------------------

# (connect, read). Short connect: the camera is on the LAN, so if the SYN isn't
# answered in a couple of seconds it is gone. Bounded read: the old 900s read
# timeout meant a link that died mid-transfer wedged the downloader for 15
# minutes before anything noticed.
CONTROL_TIMEOUT = (3.05, 15)
SCRAPE_TIMEOUT = (3.05, 60)
DOWNLOAD_TIMEOUT = (3.05, 45)

# Serialises control/settings/listing requests against the camera.
_CONTROL_LOCK = threading.Lock()


def _new_session():
    """Session with keep-alive and a single pooled connection to the camera."""
    session = requests.Session()
    # max_retries=0: retries are handled at the call site, where we can back off
    # and re-check the connection rather than hammering a camera that is busy.
    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
    session.mount('http://', adapter)
    session.headers.update({
        'Connection': 'keep-alive',
        'User-Agent': 'Dashy',
    })
    return session


# One session for short control calls, one for long file transfers, so a
# download in flight never delays a settings read (and vice versa).
_control_session = _new_session()
_download_session = _new_session()


def _reset_sessions():
    """Drop pooled sockets. Called when the camera goes away so we never try to
    reuse a keep-alive connection that died with the WiFi link."""
    global _control_session, _download_session
    for old in (_control_session, _download_session):
        try:
            old.close()
        except Exception:
            pass
    _control_session = _new_session()
    _download_session = _new_session()


def _parse_camera_response(response):
    """Normalise a camera reply into a dict with at least an 'rval' key.

    Firmware revisions disagree on the wire format: some return JSON
    ({"rval":0,"cur_value":"On","options":[...]}), others return XML
    (<Function><Cmd>2002</Cmd><Status>0</Status><Value>On</Value></Function>).
    Calling .json() on the XML ones raised, which is why camera commands looked
    like they silently did nothing. Always keep the raw body for diagnostics.
    """
    text = (response.text or '').strip()
    if not text:
        # Some firmwares answer a successful set with an empty 200.
        return {"rval": 0, "raw": ""}

    if text[0] in '{[':
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                parsed.setdefault("rval", 0)
                parsed.setdefault("raw", text)
                return parsed
            return {"rval": 0, "value": parsed, "raw": text}
        except ValueError:
            pass

    if text[0] == '<':
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return {"rval": -1, "raw": text, "error": "Unparseable camera response"}

        result = {"raw": text}
        options = []
        for child in root:
            tag = child.tag.lower()
            value = (child.text or '').strip()
            if tag in ('option', 'setting', 'item'):
                options.append(value)
            elif tag == 'status':
                # <Status> is overloaded: the result code for a SET, but the
                # current value for a GET. Keep both readings so callers can
                # pick the one that applies.
                result['rval'] = _to_int(value, -1)
                result['status_value'] = _to_int(value, value)
            elif tag == 'cmd':
                result['type'] = _to_int(value, None)
            elif tag in ('value', 'cur_value', 'string'):
                result['cur_value'] = value
            elif tag == 'param':
                result['param'] = _to_int(value, value)
            else:
                result[tag] = value
        if options:
            result['options'] = options
        result.setdefault('rval', 0)
        return result

    # Plain-text body. Treat a bare number as an rval, anything else as opaque.
    as_int = _to_int(text, None)
    if as_int is not None:
        return {"rval": as_int, "raw": text}
    return {"rval": 0, "cur_value": text, "raw": text}


def _parse_all_settings(text):
    """Parse the bulk settings document returned by cmd 3014.

    The camera answers with a run of <Function> blocks, each carrying the
    command id and that setting's current value:

        <Function><Cmd>2002</Cmd><Status>1</Status></Function>
        <Function><Cmd>9212</Cmd><Status>0</Status></Function>

    Note the current value arrives in <Status>, not <Value>. Returns
    {cmd: value_string}, empty if this isn't a bulk response.
    """
    text = (text or '').strip()
    if not text.startswith('<'):
        return {}
    # A bulk reply is a sequence of sibling elements with no single root.
    try:
        root = ET.fromstring(f"<Root>{_strip_xml_decl(text)}</Root>")
    except ET.ParseError:
        return {}

    settings = {}
    for function in root.iter():
        cmd = None
        value = None
        for child in function:
            tag = child.tag.lower()
            if tag == 'cmd':
                cmd = _to_int(child.text)
            elif tag in ('status', 'string') and value is None:
                value = (child.text or '').strip()
        if cmd is not None and value is not None:
            settings[cmd] = value
    # One <Function> alone is an ordinary single-command reply, not a table.
    return settings if len(settings) > 1 else {}


def _strip_xml_decl(text):
    return re.sub(r'^\s*<\?xml[^>]*\?>', '', text).strip()


def _camera_path_to_url(path):
    """Turn a camera filesystem path into an HTTP path.

    The camera reports paths like `A:\\DCIM\\Movie\\RO\\2025_0313_042216_47F.MP4`.
    The part after the drive colon is what the web server serves.
    """
    normalised = (path or '').replace('\\', '/')
    _, sep, tail = normalised.partition(':')
    if sep:
        normalised = tail
    if not normalised.startswith('/'):
        normalised = '/' + normalised
    return normalised


def _parse_file_list(text):
    """Parse the XML file list returned by cmd 3015.

    The response is a run of <File> blocks, each carrying NAME, FPATH, SIZE,
    TIMECODE, TIME and ATTR. This is strictly
    better than scraping the HTML directory index -- it is one request for the
    whole card, and it gives real size and timestamp instead of inferring the
    timestamp from the filename.
    """
    text = (text or '').strip()
    if not text.startswith('<'):
        return []
    try:
        root = ET.fromstring(f"<Root>{_strip_xml_decl(text)}</Root>")
    except ET.ParseError:
        return []

    files = []
    for element in root.iter():
        if element.tag.upper() != 'FILE':
            continue
        entry = {}
        for child in element:
            entry[child.tag.upper()] = (child.text or '').strip()
        name = entry.get('NAME')
        path = entry.get('FPATH')
        if not name or not path:
            continue
        files.append({
            'name': name,
            'path': path,
            'size': _to_int(entry.get('SIZE'), 0),
            'timecode': _to_int(entry.get('TIMECODE'), 0),
            'time': entry.get('TIME', ''),
            'attr': _to_int(entry.get('ATTR'), 0),
        })
    return files


def _to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


_HOSTNAME_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9\-\.]*[A-Za-z0-9])?$')


def _looks_like_host(value):
    """True for an IP literal or plausible hostname.

    Exists to reject the "10.x.x.x" placeholder shipped in
    config_template.json. Left in the config it reached getaddrinfo on every
    connection check, and the resulting gaierror escaped and killed the status
    thread.
    """
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    if not _HOSTNAME_RE.match(value):
        return False
    # Placeholder octets ("10.x.x.x", "192.168.x.x") are never real hostnames.
    return not any(label.lower() in ('x', 'xx', 'xxx') for label in value.split('.'))


class CameraOffline(Exception):
    """Raised when an operation needs the camera and it is not reachable."""


class ClipGone(Exception):
    """Raised when the camera no longer has a queued clip.

    Distinct from a transient failure: loop recording overwrites clips
    continuously, so a queued file disappearing is normal and must drop out of
    the queue rather than being retried forever.
    """


class DownloadCancelled(Exception):
    """Raised when a transfer is interrupted by request.

    Not an error: the partial file is kept and the queue is left intact, so the
    next cycle resumes from where it stopped.
    """


def classify_lens(name):
    """Which camera a clip came from.

    Keyed off the character immediately before the extension: F front,
    R rear, I interior, T front-tele. Dashy previously did a substring test for
    "R" anywhere in the sequence field, which mislabels anything that isn't a
    plain F/R suffix.
    """
    lowered = name.lower()
    if 'f.' in lowered:
        return 'Front'
    if 'i.' in lowered:
        return 'Interior'
    if 'r.' in lowered:
        return 'Rear'
    if 't.' in lowered:
        return 'Front Tele'
    return 'Unknown'


def classify_mode(path, name=''):
    """Driving vs parking, from the directory the clip lives in.

    The directory is the reliable signal across models. The filename's P
    marker is only a fallback for local files with no path context.
    """
    if 'parking' in (path or '').lower():
        return 'Parking'
    stem = name.rsplit('.', 1)[0]
    # Parking clips end PF/PR; a bare trailing P also appears on some models.
    return 'Parking' if re.search(r'P[FRIT]?$', stem) else 'Driving'


def is_locked_path(path):
    """Locked (protected) clips live under an RO directory."""
    return 'RO' in (path or '')


# Clip names look like 20240115123456_0001F.MP4 (A129) or
# 2025_0313_042216_000047F.MP4 (A229). Nothing that fails this pattern should
# ever reach the filesystem, a shell, or a URL we build -- the camera is not a
# trusted source of filenames.
_CLIP_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}\.(?:MP4|mp4)$')


def is_safe_clip_name(name):
    """True if `name` is a plain clip filename with no path or shell syntax."""
    if not name or not isinstance(name, str):
        return False
    if '/' in name or '\\' in name or name.startswith('.'):
        return False
    return _CLIP_NAME_RE.match(name) is not None


def is_safe_clip_url(url):
    """True if `url` is a camera clip path we are willing to fetch.

    Constrains the queue to real clip paths under /DCIM: without this, anything
    handed to /storage/grab was concatenated onto the camera base URL and its
    last path segment used as a local filename.
    """
    if not url or not isinstance(url, str):
        return False
    if not url.startswith('/DCIM/') or '..' in url:
        return False
    directory, _, name = url.rpartition('/')
    return directory in Camera.CLIP_DIRS.values() and is_safe_clip_name(name)


# ---------------------------------------------------------------------------
# Camera settings definitions
#
# HTTP API:
#   Get:  GET http://IP/?custom=1&cmd=<N>
#   Set:  GET http://IP/?custom=1&cmd=<N>&par=<number>
#         GET http://IP/?custom=1&cmd=<N>&str=<text>
#
# Most firmware answers in XML; some models answer JSON. See
# _parse_camera_response.
# ---------------------------------------------------------------------------

# Base command IDs shared by all supported models.
_CMD_BASE = {
    # Video
    "MOVIE_RESOLUTION":       2002,
    "MOVIE_CYCLIC_REC":       2003,
    "MOVIE_WDR":              2004,
    "MOVIE_EXPOSURE":         2005,
    "MOTION_DET":             2006,
    "MOVIE_AUDIO":            2007,
    "MOVIE_DATE_PRINT":       2008,
    "MOVIE_GSENSOR_SENS":     2011,
    "MOVIE_BITRATE":          9212,
    "GPS_INFO_STAMP":         9214,
    "GPS":                    9410,
    "SPEED_UNIT":             9412,
    # Camera
    "REAR_CAMERA_MIRROR":     9219,
    "PARKING_G_SENSOR":       9220,  # A229 repurposes this slot for interior mirror
    "IMAGE_ROTATE":           9093,
    "IR_CAMERA_COLOR":        9218,
    # Parking
    "PARKING_MODE":           9421,
    "PARKING_MOTION_DETECT":  9221,
    "PARKING_RECORDING_TIMER": 9428,
    # System
    "BEEP_SOUND":             9094,
    "SCREEN_SAVER":           9405,
    "FREQUENCY":              9406,
    "AUTO_POWER_OFF":         3007,
    "TIME_ZONE":              9411,
    "WIFI_NAME":              3003,
    "WIFI_PWD":               3004,
}

# A129-Plus overrides.
_CMD_A129 = {
    **_CMD_BASE,
    "BEEP_SOUND":                9403,
    "IMAGE_ROTATE":              9413,
    "ENTER_PARKING_MODE_TIMER":  9435,
}

# A229-Plus overrides.
# Note: cmd 9220 is INTERIOR_CAMERA_MIRROR on A229, not PARKING_G_SENSOR.
_CMD_A229 = {
    **_CMD_BASE,
    "INTERIOR_CAMERA_MIRROR":    9220,   # reuses 9220; no separate parking G-sensor on A229
    "REAR_IMAGE_ROTATE":         8225,
    "INTERIOR_IMAGE_ROTATE":     8226,
    "FRONT_CAMERA_HDR":          9318,
    "REAR_CAMERA_HDR":           9319,
    "INTERIOR_CAMERA_HDR":       9333,
    "HDR_STAMP":                 9311,
    "PARKING_GPS":               9225,
    "PARKING_HDR":               9320,
    "DAYLIGHT_SAVING":           9323,
    "TIME_FORMAT":               9321,
    "FORMAT_REMINDER":           9312,
}
# A229 has no separate parking G-sensor (slot is taken by interior mirror).
del _CMD_A229["PARKING_G_SENSOR"]


def _s(cmd_map, key, label):
    """Return a setting dict only if the cmd is defined (non-zero) for this model."""
    cmd = cmd_map.get(key, 0)
    if cmd:
        return {"cmd": cmd, "label": label}
    return None


def _build_settings(cmd_map, model):
    """Assemble settings groups for one camera model, omitting unsupported entries."""
    def group(items):
        return [i for i in items if i is not None]

    video = group([
        _s(cmd_map, "MOVIE_RESOLUTION",   "Video Resolution"),
        _s(cmd_map, "MOVIE_CYCLIC_REC",   "Loop Recording"),
        _s(cmd_map, "MOVIE_WDR",          "WDR"),
        _s(cmd_map, "MOVIE_EXPOSURE",     "Exposure Value"),
        _s(cmd_map, "MOTION_DET",         "Motion Detection"),
        _s(cmd_map, "MOVIE_AUDIO",        "Audio Recording"),
        _s(cmd_map, "MOVIE_DATE_PRINT",   "Date Stamp"),
        _s(cmd_map, "MOVIE_GSENSOR_SENS", "G-Sensor Sensitivity"),
        _s(cmd_map, "MOVIE_BITRATE",      "Video Bitrate"),
        _s(cmd_map, "GPS_INFO_STAMP",     "GPS Info Stamp"),
        _s(cmd_map, "GPS",                "GPS"),
        _s(cmd_map, "SPEED_UNIT",         "Speed Unit"),
    ])

    camera = group([
        _s(cmd_map, "REAR_CAMERA_MIRROR",      "Rear Camera Mirror"),
        _s(cmd_map, "IMAGE_ROTATE",            "Image Rotate"),
        _s(cmd_map, "IR_CAMERA_COLOR",         "Interior Camera Color"),
        # A229-Plus only
        _s(cmd_map, "INTERIOR_CAMERA_MIRROR",  "Interior Camera Mirror"),
        _s(cmd_map, "REAR_IMAGE_ROTATE",       "Rear Image Rotate"),
        _s(cmd_map, "INTERIOR_IMAGE_ROTATE",   "Interior Image Rotate"),
        _s(cmd_map, "FRONT_CAMERA_HDR",        "Front Camera HDR"),
        _s(cmd_map, "REAR_CAMERA_HDR",         "Rear Camera HDR"),
        _s(cmd_map, "INTERIOR_CAMERA_HDR",     "Interior Camera HDR"),
        _s(cmd_map, "HDR_STAMP",               "HDR Stamp"),
    ])

    parking = group([
        _s(cmd_map, "PARKING_MODE",            "Parking Mode"),
        _s(cmd_map, "PARKING_MOTION_DETECT",   "Parking Motion Detection"),
        _s(cmd_map, "PARKING_G_SENSOR",        "Parking G-Sensor"),
        _s(cmd_map, "PARKING_RECORDING_TIMER", "Parking Recording Timer"),
        _s(cmd_map, "ENTER_PARKING_MODE_TIMER","Enter Parking Mode Timer"),
        # A229-Plus only
        _s(cmd_map, "PARKING_GPS",             "Parking GPS"),
        _s(cmd_map, "PARKING_HDR",             "Parking HDR"),
    ])

    system = group([
        _s(cmd_map, "BEEP_SOUND",    "Beep Sound"),
        _s(cmd_map, "SCREEN_SAVER",  "Screen Saver"),
        _s(cmd_map, "FREQUENCY",     "Power Frequency"),
        _s(cmd_map, "AUTO_POWER_OFF","Auto Power Off"),
        _s(cmd_map, "TIME_ZONE",     "Time Zone"),
        # A229-Plus only
        _s(cmd_map, "TIME_FORMAT",   "Time Format"),
        _s(cmd_map, "DAYLIGHT_SAVING","Daylight Saving"),
        _s(cmd_map, "FORMAT_REMINDER","Format Reminder"),
        _s(cmd_map, "WIFI_NAME",     "WiFi Name"),
        _s(cmd_map, "WIFI_PWD",      "WiFi Password"),
    ])

    return {"Video": video, "Camera": camera, "Parking": parking, "System": system}


# Pre-built per-model settings, keyed by cam_model string from config.
CAMERA_SETTINGS = {
    "A129-Plus": _build_settings(_CMD_A129, "A129-Plus"),
    "A229-Plus": _build_settings(_CMD_A229, "A229-Plus"),
}


# Control and status command IDs (not settings; used for direct actions and queries).
# Verified against the camera's own behaviour.
_CMD_CONTROL = {
    "MOVIE_RECORD":      2001,  # par=1 → start, par=0 → stop
    "PHOTO_CAPTURE":     1001,  # trigger photo
    "CHANGE_MODE":       3001,  # par=1 → video/record mode, par=2 → playback mode
    "CARD_FREE_SPACE":   3017,
    "FIRMWARE_VERSION":  3012,
    "GET_ALL_SETTINGS":  3014,  # returns EVERY setting in one XML document
    "HEART_BEAT":        3016,  # keeps the camera's session alive
    "GET_FILE_LIST":     3015,  # XML file list (name, path, size, attr)
    "GET_BATTERY_LEVEL": 3019,
    "GET_CARD_STATUS":   3024,
    "DELETE_ONE_FILE":   4003,
}

# cmd 3001 parameters.
MODE_VIDEO = 1
MODE_PLAYBACK = 2


class CameraStatus:
    """Thread-safe shared camera state. Updated by a background thread;
    read by web routes and the downloader without ever blocking on a socket."""
    def __init__(self):
        self._lock = threading.Lock()
        self.connected = False
        self.connected_string = 'disconnected'
        self.connected_ip = None
        self.base_url = None

    def update(self, camera):
        with self._lock:
            self.connected = camera.connected
            self.connected_string = camera.connected_string
            self.connected_ip = camera.connected_ip
            self.base_url = camera.base_url

class Camera:
    # A successful probe is trusted for this long. The downloader used to probe
    # port 80 before every single file while the previous transfer's socket was
    # still in TIME_WAIT, which is a big part of why the camera appeared to
    # connect and disconnect constantly.
    PROBE_TTL = 20
    # A failed probe is retried sooner, so pulling into the driveway is noticed
    # quickly rather than being held off by a stale "disconnected".
    PROBE_TTL_FAILED = 3
    PROBE_TIMEOUT = 2

    def __init__(self, config, check_connection=False):
        if not isinstance(config, Config):
            raise Exception("You must pass the config as a Config class")

        # Get data from config
        config_data = config.config_data

        # Create variables from config
        self.config = config
        self.cam_ip = config_data.get("cam_ip", "192.168.1.254")
        self.cam_wifi_ip = config_data.get("cam_wifi_ip", None)
        self.cam_model = config_data.get('cam_model', "A129-Plus")
        self.cam_port = int(config_data.get("cam_port", 80) or 80)

        # Probe the home-WiFi address first when configured: that is what the
        # docs promise ("preferred over AP mode"), and probing the AP address
        # first meant a timeout on every single check.
        candidates = []
        if self.cam_wifi_ip and isinstance(self.cam_wifi_ip, str):
            candidates.append(self.cam_wifi_ip.strip())
        if self.cam_ip and isinstance(self.cam_ip, str):
            candidates.append(self.cam_ip.strip())
        # Drop blanks/placeholders like the "10.x.x.x" in config_template.json.
        self.cam_ip_list = [ip for ip in candidates if ip and _looks_like_host(ip)]
        if not self.cam_ip_list:
            logger.warning("No usable camera address configured; defaulting to 192.168.1.254")
            self.cam_ip_list = ["192.168.1.254"]

        # Connection state. Guarded by _lock because the web threads, the status
        # poller and the downloader all touch it.
        self._lock = threading.RLock()
        self.connected = False
        self.connected_string = "disconnected"
        self.connected_ip = None
        self.base_url = None
        self.result = None
        self._last_probe = 0.0
        # Which query parameter this firmware accepts for setting values;
        # detected once on the first successful set (see set_setting).
        self._set_param_name = config_data.get("cam_set_param") or None
        # cmd 3015 file-list support: None until probed, then True/False.
        self._file_list_supported = None
        self._file_list_cache = None
        self._file_list_at = 0.0

        if check_connection:
            self.check_camera_connection()

    def check_camera_connection(self, return_as_string=False, force=False):
        """Probe the camera's HTTP port and update cached connection state.

        Cheap to call: a recent result is reused rather than opening a fresh
        socket, and the previous state is only replaced once every candidate
        address has been tried, so callers never observe a transient
        "disconnected" mid-probe.
        """
        with self._lock:
            age = time.monotonic() - self._last_probe
            ttl = self.PROBE_TTL if self.connected else self.PROBE_TTL_FAILED
            if not force and self._last_probe and age < ttl:
                return self.connected_string if return_as_string else self.connected

            was_connected = self.connected
            found_ip = None
            last_result = None

            for ip in self.cam_ip_list:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(self.PROBE_TIMEOUT)
                        last_result = s.connect_ex((ip, self.cam_port))
                except OSError as e:
                    # gaierror/ENETUNREACH used to escape this method and kill
                    # the status thread outright, freezing the UI on
                    # "disconnected" until Dashy was restarted.
                    logger.debug(f"Probe of {ip}:{self.cam_port} failed: {e}")
                    last_result = getattr(e, 'errno', -1)
                    continue

                if last_result == 0:
                    found_ip = ip
                    break

            self._last_probe = time.monotonic()
            self.result = last_result

            if found_ip:
                changed = not was_connected or self.connected_ip != found_ip
                if self.connected_ip and self.connected_ip != found_ip:
                    # Moved between AP and home WiFi: pooled sockets point at
                    # the old address.
                    _reset_sessions()
                self.connected = True
                self.connected_string = "connected"
                self.connected_ip = found_ip
                self.base_url = f"http://{found_ip}:{self.cam_port}"
                if changed:
                    logger.info(f"{self.cam_model} connected at {found_ip}")
            else:
                self.connected = False
                self.connected_string = "disconnected"
                self.connected_ip = None
                self.base_url = None
                if was_connected:
                    logger.info(f"{self.cam_model} disconnected")
                    _reset_sessions()

            return self.connected_string if return_as_string else self.connected

    def require_base_url(self):
        """Return the camera base URL, or raise if it is not reachable."""
        with self._lock:
            if self.connected and self.base_url:
                return self.base_url
        # Cached state says down -- confirm before giving up, the camera may
        # have just come back.
        if self.check_camera_connection():
            with self._lock:
                if self.base_url:
                    return self.base_url
        raise CameraOffline("Camera is not connected")

    @property
    def settings(self):
        """Return the settings definition for the configured camera model."""
        return CAMERA_SETTINGS.get(self.cam_model, {})

    def known_commands(self):
        """Command IDs this model advertises, plus the control/status commands."""
        cmds = {item['cmd'] for group in self.settings.values() for item in group}
        cmds.update(_CMD_CONTROL.values())
        return cmds

    CLIP_DIRS = {
        ("parking", True):  "/DCIM/Movie/Parking/RO",
        ("driving", True):  "/DCIM/Movie/RO",
        ("driving", False): "/DCIM/Movie",
        ("parking", False): "/DCIM/Movie/Parking",
    }

    def scrape_webserver(self, mode="driving", locked=True, db=None):
        base_url = self.require_base_url()

        file_dir = self.CLIP_DIRS.get((mode, bool(locked)))
        if not file_dir:
            raise ValueError(f"Unknown listing mode: {mode!r}")

        # One query each for the downloaded/queued sets instead of two per
        # file: a full card can list hundreds of clips.
        downloads = db or DownloadsDB(self.config)
        downloaded = set(downloads.load_downloaded_files())
        queued = set(downloads.load_download_queue())

        listing = self._list_via_api(file_dir, downloaded, queued)
        if listing is not None:
            return listing

        with _CONTROL_LOCK:
            response = _control_session.get(base_url + file_dir, timeout=SCRAPE_TIMEOUT)

        if response.status_code == 404:
            raise Exception(f"Camera does not have any video files in: {file_dir}, are you sure there are any files here?")
        if response.status_code != 200:
            raise Exception(f"Camera did not return expected status code 200: {response.status_code} - {response.text}")

        soup = BeautifulSoup(response.text, 'html.parser')
        file_urls = []
        for a_tag in soup.find_all('a'):
            href = a_tag.get('href') or ''
            if '.MP4' not in href.upper() or 'del=1' in href:
                continue
            file_name = href.replace(f"{file_dir}/", "").lstrip('/')
            if not is_safe_clip_name(file_name):
                logger.warning(f"Ignoring clip with unexpected name from camera: {file_name!r}")
                continue
            try:
                file_info = self.parse_filename(file_name, file_dir)
            except (ValueError, IndexError) as e:
                # A clip whose name doesn't match the model's pattern must not
                # abort the whole listing.
                logger.warning(f"Skipping unparseable filename {file_name!r}: {e}")
                continue
            file_info['downloaded'] = href in downloaded
            file_info['in_queue'] = href in queued
            file_info['dir'] = file_dir
            file_urls.append(file_info)

        return sorted(file_urls, key=lambda x: x['created_date'], reverse=True)

    # cmd 3015 covers the whole card, so cache it briefly rather than issuing
    # it once per directory the UI asks about.
    FILE_LIST_TTL = 10

    def fetch_file_list(self):
        """All files on the card via cmd 3015, or None if unsupported."""
        now = time.monotonic()
        with self._lock:
            if self._file_list_cache is not None and now - self._file_list_at < self.FILE_LIST_TTL:
                return self._file_list_cache

        if self._file_list_supported is False:
            return None

        try:
            # Generous read timeout: a full card is a large document and the
            # camera is slow to assemble it.
            response = self._request(
                {"custom": 1, "cmd": _CMD_CONTROL["GET_FILE_LIST"]},
                timeout=(CONTROL_TIMEOUT[0], 100),
            )
        except CameraOffline:
            raise
        except Exception as e:
            logger.warning(f"File list command failed ({e}); falling back to directory listing")
            self._file_list_supported = False
            return None

        files = _parse_file_list(response.get('raw', ''))
        if not files:
            logger.info("Camera did not return a file list; using directory listing instead")
            self._file_list_supported = False
            return None

        self._file_list_supported = True
        with self._lock:
            self._file_list_cache = files
            self._file_list_at = now
        return files

    def _list_via_api(self, file_dir, downloaded, queued):
        """Build a listing for `file_dir` from the cmd 3015 file table.

        Returns None if the camera doesn't support it, so the caller falls back
        to scraping the HTML directory index. Preferred because it reports the
        real timestamp and size instead of inferring them from the filename,
        which is what makes unsupported models work.
        """
        files = self.fetch_file_list()
        if not files:
            return None

        results = []
        for entry in files:
            url_path = _camera_path_to_url(entry['path'])
            directory, _, file_name = url_path.rpartition('/')
            if directory != file_dir:
                continue
            if not is_safe_clip_name(file_name):
                logger.warning(f"Ignoring clip with unexpected name from camera: {file_name!r}")
                continue

            created_date = self._entry_timestamp(entry, file_name)
            if created_date is None:
                logger.warning(f"Skipping clip with no usable timestamp: {file_name!r}")
                continue

            results.append({
                'filename': file_name,
                'name': created_date.strftime("%m/%d/%Y %I:%M %p"),
                'created_date': created_date,
                'created_date_formatted': created_date.strftime("%m/%d/%Y %I:%M %p"),
                'location': classify_lens(file_name),
                'number': file_name.rsplit('.', 1)[0].split('_')[-1],
                'dir': file_dir,
                'mode': classify_mode(file_dir, file_name),
                'thumbnail': file_name.rsplit('.', 1)[0] + '.jpg',
                'size': entry['size'],
                'downloaded': url_path in downloaded,
                'in_queue': url_path in queued,
            })

        return sorted(results, key=lambda x: x['created_date'], reverse=True)

    def _entry_timestamp(self, entry, file_name):
        """Timestamp for a file-list entry, preferring what the camera reports."""
        # The camera's own TIME field ("2025/03/13 04:22:16") needs no
        # model-specific filename knowledge, so it works on models whose naming
        # Dashy doesn't recognise.
        raw_time = entry.get('time') or ''
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw_time.strip(), fmt)
            except ValueError:
                continue
        try:
            return self.parse_filename(file_name)['created_date']
        except (ValueError, IndexError, KeyError):
            return None

    def parse_filename(self, file_name, directory=''):
        """Pull the timestamp, camera position and mode out of a clip name.

        `directory` is the camera path the clip came from, which is the
        reliable source for driving-vs-parking; local files pass nothing and
        fall back to the filename marker.

        Raises ValueError for anything that doesn't match the model's pattern;
        callers skip those rather than failing the whole listing. Previously an
        unrecognised name (or an unrecognised cam_model) raised NameError from
        an unbound local, which aborted the page.
        """
        plain_filename = file_name.rsplit(".", 1)[0]
        parts = plain_filename.split("_")

        if self.cam_model == "A129-Plus":
            # Example: 20240115123456_0001F.MP4
            if len(parts) < 2:
                raise ValueError(f"Not an A129-Plus clip name: {file_name!r}")
            stamp = parts[0]
            suffix = parts[1]
        else:
            # Example: 2025_0313_042216_000047F.MP4 (A229-Plus and similar)
            if len(parts) < 4:
                raise ValueError(f"Not an A229-Plus clip name: {file_name!r}")
            stamp = f"{parts[0]}{parts[1]}{parts[2]}"
            suffix = parts[3]

        created_date = datetime.strptime(stamp, '%Y%m%d%H%M%S')
        created_date_formatted = created_date.strftime("%m/%d/%Y %I:%M %p")

        location = classify_lens(file_name)
        number = suffix if location != "Unknown" else None
        mode = classify_mode(directory, file_name)
        thumbnail_name = file_name.rsplit(".", 1)[0] + ".jpg"
        return {
                'filename': file_name, 
                'name': created_date_formatted, 
                'created_date' : created_date, 
                'created_date_formatted': created_date_formatted, 
                'location' : location, 
                'number' : number, 
                'dir' : '/locked', 
                "mode" : mode, 
                'thumbnail' : thumbnail_name
            }
    def _request(self, params, timeout=CONTROL_TIMEOUT):
        """Issue one control request, serialised against all other control
        traffic, and return the normalised response."""
        base_url = self.require_base_url()
        with _CONTROL_LOCK:
            try:
                response = _control_session.get(base_url + "/", params=params, timeout=timeout)
            except requests.RequestException as e:
                # A dead keep-alive socket surfaces here; force a re-probe so
                # the UI reflects reality instead of retrying a dead link.
                logger.warning(f"Camera request {params} failed: {e}")
                self.check_camera_connection(force=True)
                raise
        response.raise_for_status()
        parsed = _parse_camera_response(response)
        logger.debug(f"cmd {params.get('cmd')} -> {parsed}")
        return parsed

    def get_setting(self, cmd):
        """GET /?custom=1&cmd=N -- read a setting or status value."""
        return self._request({"custom": 1, "cmd": int(cmd)})

    def set_setting(self, cmd, value):
        """GET /?custom=1&cmd=N&<param>=VALUE -- write a setting.

        The firmware uses two different parameter names: `&par=` for numeric
        values and `&str=` for text (WiFi name and password, custom stamp, car
        number). Dashy only ever sent `param_0`, which these cameras accept
        with a non-zero status and then ignore -- the change silently never
        applied.

        Numeric values go via `par`, text via `str`. `param_0` is kept as a
        last-resort fallback for firmware that wants it, and whichever form the
        camera accepts is remembered.
        """
        cmd = int(cmd)
        numeric = _to_int(value, None)

        if numeric is not None and numeric > -1:
            candidates = ["par", "param_0"]
            encoded = numeric
        else:
            # Text value. The camera decodes '+' as a space.
            candidates = ["str", "param_0"]
            encoded = str(value).replace(' ', '+')

        if self._set_param_name in candidates:
            # Try the known-good name first, but keep the others as fallback.
            candidates = [self._set_param_name] + [c for c in candidates if c != self._set_param_name]

        last = None
        for name in candidates:
            result = self._request({"custom": 1, "cmd": cmd, name: encoded})
            last = result
            if _to_int(result.get("rval"), -1) == 0:
                if self._set_param_name != name:
                    logger.info(f"Camera accepts '{name}' for set commands")
                    self._set_param_name = name
                return result
            logger.debug(f"set cmd={cmd} via '{name}' returned rval={result.get('rval')}")

        # Every form was rejected. Return the last reply verbatim -- the caller
        # surfaces rval and the raw body so the failure is diagnosable instead
        # of looking like nothing happened.
        return last if last is not None else {"rval": -1, "error": "No response from camera"}

    def heartbeat(self):
        """Tell the camera we are still here (cmd 3016).

        Sent periodically while a session is held open. Without it the camera
        is free to decide the client has gone away and tear the connection down
        mid-transfer.
        """
        return self._request({"custom": 1, "cmd": _CMD_CONTROL["HEART_BEAT"]})

    def set_mode(self, mode):
        """Switch between recording (1) and playback (2) mode via cmd 3001."""
        return self.set_setting(_CMD_CONTROL["CHANGE_MODE"], int(mode))

    @contextlib.contextmanager
    def playback_mode(self):
        """Put the camera in playback mode for the duration of the block.

        This matters a lot: otherwise the camera is encoding 4K, writing to
        the SD card and serving HTTP over WiFi at the same time. That
        contention is what makes downloads crawl and connections drop
        mid-transfer.

        IMPORTANT: playback mode stops recording. Video mode is always restored
        on the way out, including on error, and a restore is also attempted at
        startup in case a previous run was killed mid-download.
        """
        entered = False
        try:
            result = self.set_mode(MODE_PLAYBACK)
            entered = _to_int(result.get('rval'), -1) == 0
            if entered:
                logger.info("Camera switched to playback mode for transfer")
            else:
                # Not fatal -- downloads still work while recording, just more
                # slowly. Don't fail the batch over it.
                logger.warning(
                    f"Camera refused playback mode (rval={result.get('rval')}); "
                    "downloading while it records"
                )
        except Exception as e:
            logger.warning(f"Could not switch to playback mode: {e}")

        try:
            yield entered
        finally:
            if entered:
                self.restore_video_mode()

    def restore_video_mode(self):
        """Put the camera back into recording mode. Never raises."""
        try:
            result = self.set_mode(MODE_VIDEO)
            if _to_int(result.get('rval'), -1) == 0:
                logger.info("Camera returned to recording mode")
                return True
            logger.error(
                f"Camera did not return to recording mode (rval={result.get('rval')}) "
                "-- it may not be recording. Check the camera."
            )
        except Exception as e:
            logger.error(f"Failed to restore recording mode: {e} -- the camera may not be recording")
        return False

    @property
    def mjpeg_url(self):
        """MJPEG stream URL served by the camera on port 8192."""
        return f"http://{self.connected_ip}:8192" if self.connected_ip else None

    def get_camera_info(self):
        """
        Fetch camera status in one call: state, free space, card health, firmware.
        Each key holds the normalised camera response, or {"rval":-1, "error": str}
        on failure.
        """
        result = {}
        for key, cmd in [
            ("state",       _CMD_CONTROL["MOVIE_RECORD"]),
            ("free_space",  _CMD_CONTROL["CARD_FREE_SPACE"]),
            ("card_status", _CMD_CONTROL["GET_CARD_STATUS"]),
            ("firmware",    _CMD_CONTROL["FIRMWARE_VERSION"]),
        ]:
            try:
                response = self.get_setting(cmd)
                # On XML firmware a GET returns its value in <Status>, the same
                # field a SET uses for its result code. Surface it as `param`
                # too so the UI reads one shape regardless of wire format.
                if 'param' not in response and 'status_value' in response:
                    response = dict(response, param=response['status_value'], rval=0)
                result[key] = response
            except Exception as e:
                result[key] = {"rval": -1, "error": str(e)}
        return result

    def delete_file(self, url_path):
        """Delete one clip from the camera's card.

        `url_path` is the web path, e.g. /DCIM/Movie/RO/2025_..._47F.MP4.
        Returns True only if the camera confirmed the deletion.

        Two mechanisms, because firmware varies: the delete command with the
        file path as a string, then the `?del=1` form used by the camera's own
        directory listing.
        """
        if not is_safe_clip_url(url_path):
            logger.warning(f"Refusing to delete unexpected path: {url_path!r}")
            return False

        try:
            result = self._request({
                "custom": 1,
                "cmd": _CMD_CONTROL["DELETE_ONE_FILE"],
                "str": url_path,
            })
            if _to_int(result.get('rval'), -1) == 0:
                return True
            logger.debug(f"Delete cmd for {url_path} returned rval={result.get('rval')}")
        except CameraOffline:
            raise
        except Exception as e:
            logger.debug(f"Delete cmd for {url_path} failed: {e}")

        # Fallback: the delete link the directory index itself uses.
        try:
            base_url = self.require_base_url()
            with _CONTROL_LOCK:
                response = _control_session.get(
                    base_url + url_path, params={"del": 1}, timeout=CONTROL_TIMEOUT
                )
            if response.status_code == 200:
                return True
            logger.debug(f"del=1 for {url_path} returned HTTP {response.status_code}")
        except CameraOffline:
            raise
        except Exception as e:
            logger.debug(f"del=1 for {url_path} failed: {e}")

        return False

    def read_all_settings(self):
        """Read every setting for this model.

        cmd 3014 returns the whole settings table in a single XML document.
        The settings page used to fire one request per setting (~40 of them) on
        load, which the camera's single-threaded HTTP server could not keep up
        with.

        Falls back to reading commands individually if the camera doesn't
        support the bulk query.
        """
        try:
            response = self._request({"custom": 1, "cmd": _CMD_CONTROL["GET_ALL_SETTINGS"]})
        except CameraOffline:
            raise
        except Exception as e:
            logger.warning(f"Bulk settings read failed ({e}); falling back to individual reads")
            response = None

        if response:
            bulk = _parse_all_settings(response.get('raw', ''))
            if bulk:
                wanted = self.known_commands()
                return {
                    str(cmd): {"rval": 0, "cur_value": value}
                    for cmd, value in bulk.items() if cmd in wanted
                }
            logger.info("Camera did not return a bulk settings table; reading individually")

        values = {}
        for group in self.settings.values():
            for item in group:
                cmd = item["cmd"]
                try:
                    values[str(cmd)] = self.get_setting(cmd)
                except CameraOffline:
                    values[str(cmd)] = {"rval": -1, "error": "Camera not connected"}
                    return values
                except Exception as e:
                    values[str(cmd)] = {"rval": -1, "error": str(e)}
        return values

    def start_recording(self):
        return self.set_setting(_CMD_CONTROL["MOVIE_RECORD"], 1)

    def stop_recording(self):
        return self.set_setting(_CMD_CONTROL["MOVIE_RECORD"], 0)

    def take_photo(self):
        return self.get_setting(_CMD_CONTROL["PHOTO_CAPTURE"])

    def generate_video_frames(self):
        if not self.connected or not self.connected_ip:
            raise Exception("Camera is not connected")
        cmd = [
            "ffmpeg", "-i", f"rtsp://{self.connected_ip}:554/movie123.mov", 
            "-c:v", "libx264", "-preset", "ultrafast", "-f", "mpegts", "-"
        ]
        ffmpeg_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        try:
            while True:
                frame = ffmpeg_process.stdout.read(8192)
                if not frame:
                    break
                yield frame
        finally:
            ffmpeg_process.kill()
            ffmpeg_process.wait()

class Downloads:
    def __init__(self, config, cam_url=None):
        if not isinstance(config, Config):
            raise Exception("Config is not of class Config")
        config_data = config.config_data
        
        download_path = f"{config_data['video_path']}/locked"
        thumbnail_path = f"{config_data['video_path']}/thumbnails"

        self.config = config
        self.db = DownloadsDB(config)
        self.base_url = cam_url
        self.download_path = os.path.abspath(download_path)
        self.thumbnail_path = os.path.abspath(thumbnail_path)
        # Read timeout for a transfer in flight. This is *not* a deadline for
        # the whole file -- it is how long we wait for the next block before
        # deciding the link is dead. The old 900s value meant a dropped WiFi
        # link stalled the downloader for 15 minutes.
        self.read_timeout = int(config_data.get('download_read_timeout', DOWNLOAD_TIMEOUT[1]))
        # 256 KiB reads instead of 2 KiB: a 4K clip is 300-500 MB, which was a
        # quarter of a million loop iterations per file. Past ~64 KiB the
        # throughput gain flattens out, so this is sized for resume granularity
        # instead: iter_content only yields whole chunks, so a dropped link
        # discards up to one chunk of data that has to be fetched again.
        self.chunk_size = int(config_data.get('download_chunk_size', 256 * 1024))
        self.max_attempts = int(config_data.get('download_attempts', 3))
        # Switch the camera to playback mode while downloading. Much faster and
        # far more stable, but it stops recording for the duration -- set false
        # to keep the camera recording throughout.
        self.use_playback_mode = bool(config_data.get('playback_mode_for_downloads', True))
        self._last_drain_ok = True
        # Set to interrupt a transfer in progress. The partial file is kept, so
        # the next cycle resumes rather than starting the clip again.
        self.cancel_event = threading.Event()
        self._cancelled = False
        # Delete each clip from the camera once it is safely downloaded, to stop
        # the card filling up. Only ever applied to a transfer that completed
        # and matched the size the camera advertised -- a partial or truncated
        # download never triggers a delete.
        self.delete_after_download = bool(config_data.get('delete_after_download', True))

    def request_stop(self):
        """Interrupt the download in progress, if any."""
        self.cancel_event.set()

    def was_cancelled(self):
        """True if the last run stopped because it was interrupted."""
        return self._cancelled

    def _check_cancelled(self):
        if self.cancel_event.is_set():
            raise DownloadCancelled("Download interrupted by request")

    def _interruptible_sleep(self, seconds):
        """Sleep, but wake immediately if a stop is requested."""
        if self.cancel_event.wait(timeout=seconds):
            raise DownloadCancelled("Download interrupted by request")

    def resolve_in_download_dir(self, file_name):
        """Join `file_name` to the download directory, refusing to escape it."""
        if not is_safe_clip_name(file_name):
            raise ValueError(f"Refusing unsafe clip name: {file_name!r}")
        path = os.path.abspath(os.path.join(self.download_path, file_name))
        if os.path.dirname(path) != self.download_path:
            raise ValueError(f"Path escapes download directory: {file_name!r}")
        return path


    def start_download_lock(self):
        with open(".download-in-progress", "w") as f:
            f.write("downloading")
            
    def stop_download_lock(self):
        try:
            os.unlink(".download-in-progress")
            logger.info("Lockfile has been cleared")
        except FileNotFoundError:
            logger.warning("No lockfile to clear")
        
    def generate_preview(self, file_path, file_name):
        """Extract a single frame as a JPEG thumbnail.

        Runs ffmpeg without a shell: `file_name` originates from the camera's
        directory listing, and interpolating it into a shell string meant a clip
        named `x";rm -rf ~;"` would have been executed.
        """
        base_name = os.path.basename(file_name)
        if base_name.lower().endswith('.mp4'):
            base_name = base_name[:-4]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_\-]{0,63}', base_name):
            logger.warning(f"Refusing to generate thumbnail for unsafe name: {file_name!r}")
            return

        os.makedirs(self.thumbnail_path, exist_ok=True)
        thumbnail_path = os.path.join(self.thumbnail_path, base_name + ".jpg")
        command = [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-ss", "1", "-i", file_path,
            "-vframes", "1", "-q:v", "2", thumbnail_path,
        ]

        try:
            cwd = os.path.dirname(os.path.realpath(__file__))
            subprocess.run(command, check=True, cwd=cwd, timeout=60,
                           stdin=subprocess.DEVNULL, capture_output=True)
            logger.info(f"Thumbnail generated for {base_name}")
        except subprocess.TimeoutExpired:
            logger.error(f"Thumbnail generation timed out for {base_name}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error generating thumbnail for {base_name}: {e.stderr.decode(errors='replace').strip()}")
        except FileNotFoundError:
            logger.error("ffmpeg not found; cannot generate thumbnails")


    MIN_FREE_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB

    def _fetch_one(self, file_url, file_path, cam=None):
        """Download a single clip, resuming a partial `.part` file if present.

        Returns the number of bytes written. Raises on failure; the caller
        decides whether to retry. The partial file is deliberately *kept* on
        failure so the next attempt resumes instead of starting over -- a clip
        that died at 95% used to be thrown away and re-fetched from zero.
        """
        part_path = file_path + '.part'
        resume_from = 0
        if os.path.exists(part_path):
            resume_from = os.path.getsize(part_path)

        headers = {}
        if resume_from:
            headers['Range'] = f'bytes={resume_from}-'

        base_url = cam.require_base_url() if cam else self.base_url
        if not base_url:
            raise CameraOffline("Camera is not connected")
        self.base_url = base_url

        url = base_url + file_url
        timeout = (DOWNLOAD_TIMEOUT[0], self.read_timeout)
        with _download_session.get(url, stream=True, timeout=timeout, headers=headers) as response:
            if response.status_code == 416:
                # Already have the whole file according to the camera.
                logger.info(f"{file_url} already complete on disk")
                total_bytes = resume_from
            elif response.status_code == 206:
                mode = 'ab'
                total_bytes = resume_from + _to_int(response.headers.get('Content-Length'), 0)
                logger.info(f"Resuming {file_url} at {resume_from / 1048576:.1f} MB")
            elif response.status_code == 200:
                # No range support (or a fresh start): rewrite from scratch.
                if resume_from:
                    logger.info(f"Camera ignored Range for {file_url}; restarting")
                mode = 'wb'
                resume_from = 0
                total_bytes = _to_int(response.headers.get('Content-Length'), 0)
            elif response.status_code in (404, 410):
                # Rotated out by loop recording, or deleted on the camera.
                raise ClipGone(f"{file_url} is no longer on the camera")
            else:
                raise Exception(f"Camera returned HTTP {response.status_code} for {file_url}")

            if response.status_code != 416:
                bytes_downloaded = resume_from
                self.db.set_progress(file_url, bytes_downloaded, total_bytes)
                last_report = time.monotonic()
                with open(part_path, mode) as part_file:
                    for chunk in response.iter_content(chunk_size=self.chunk_size):
                        if not chunk:
                            continue
                        # Checked every chunk so a stop takes effect within a
                        # fraction of a second, not at the end of the file.
                        # Leaving the loop keeps everything written so far;
                        # the next attempt resumes from there.
                        self._check_cancelled()
                        part_file.write(chunk)
                        bytes_downloaded += len(chunk)
                        # Report on a timer rather than a chunk count so the UI
                        # updates at the same rate regardless of chunk size.
                        now = time.monotonic()
                        if now - last_report >= 1.0:
                            self.db.set_progress(file_url, bytes_downloaded, total_bytes)
                            last_report = now
                self.db.set_progress(file_url, bytes_downloaded, total_bytes)

                # A truncated transfer must not be renamed into place and
                # marked downloaded -- that produced unplayable clips that were
                # never retried.
                if total_bytes and bytes_downloaded < total_bytes:
                    raise IOError(
                        f"Truncated download for {file_url}: "
                        f"{bytes_downloaded} of {total_bytes} bytes"
                    )

        os.replace(part_path, file_path)
        return os.path.getsize(file_path)

    def download_video(self, cam=None):
        # A stop applies to the run it interrupted, so start each run clean.
        self.cancel_event.clear()
        self._cancelled = False

        file_urls = self.db.load_download_queue()
        if not file_urls:
            logger.info("No files to download... moving on!")
            return True

        os.makedirs(self.download_path, exist_ok=True)

        try:
            # Always take the current address from the camera object: caching it
            # across reconnects meant every download failed after the camera
            # moved between AP and home WiFi.
            if cam and isinstance(cam, Camera):
                self.base_url = cam.require_base_url()
            elif not self.base_url:
                cam = Camera(self.config, check_connection=True)
                self.base_url = cam.require_base_url()

            free_bytes = shutil.disk_usage(self.download_path).free
            if free_bytes < self.MIN_FREE_BYTES:
                logger.error(
                    f"Insufficient disk space: {free_bytes / 1024**3:.1f} GB free, "
                    "need at least 1 GB. Skipping downloads."
                )
                return False

            self.start_download_lock()
            completed = 0
            try:
                with self._transfer_mode(cam):
                    completed = self._drain_queue(file_urls, cam)
            except DownloadCancelled:
                # Requested stop, not a failure. Partial files and queue entries
                # are left as they are so the next cycle resumes.
                self._cancelled = True
                logger.info(
                    f"Download interrupted after {self._completed} clip(s); progress kept"
                )
                return False
            finally:
                self.stop_download_lock()

            logger.info(f"Downloads complete! {completed} clip(s) fetched.")
            return self._last_drain_ok

        except CameraOffline as e:
            logger.warning(f"Cannot download: {e}")
            return False
        except Exception as e:
            logger.exception(f"Exception while downloading: {e}")
            return False

    @contextlib.contextmanager
    def _transfer_mode(self, cam):
        """Hold the camera in playback mode for a batch, if enabled and possible."""
        if cam and isinstance(cam, Camera) and self.use_playback_mode:
            with cam.playback_mode():
                yield
        else:
            yield

    def _delete_from_camera(self, file_url, file_path, cam):
        """Remove a clip from the camera once it is safely on disk.

        Deliberately paranoid: this destroys the only other copy of the
        footage, so it only runs when the local file exists and is non-empty.
        _fetch_one has already rejected any transfer shorter than the length
        the camera advertised, so reaching here means the clip is complete.
        A failed delete is logged and otherwise ignored -- the clip is
        downloaded either way, and it will simply be skipped next cycle.
        """
        if not self.delete_after_download or not cam or not isinstance(cam, Camera):
            return False

        try:
            if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
                logger.warning(
                    f"Not deleting {file_url} from the camera: local copy is missing or empty"
                )
                return False
        except OSError as e:
            logger.warning(f"Not deleting {file_url} from the camera: {e}")
            return False

        try:
            if cam.delete_file(file_url):
                logger.info(f"Deleted {file_url.rsplit('/', 1)[-1]} from the camera")
                return True
            logger.warning(f"Camera did not confirm deletion of {file_url}")
        except CameraOffline:
            raise
        except Exception as e:
            logger.warning(f"Could not delete {file_url} from the camera: {e}")
        return False

    def prune_missing(self, cam):
        """Drop queue entries for clips the camera no longer has.

        Loop recording overwrites clips constantly, so a queue built up over
        days accumulates entries that can never succeed. Without this they are
        retried on every cycle forever and the queue never drains.

        Only runs when the camera can enumerate its whole card in one request;
        otherwise there is no cheap way to know what still exists, and the 404
        handling in _drain_queue clears them as they are attempted instead.
        """
        queued = self.db.load_download_queue()
        if not queued:
            return 0

        try:
            files = cam.fetch_file_list()
        except CameraOffline:
            raise
        except Exception as e:
            logger.debug(f"Could not enumerate camera files for pruning: {e}")
            return 0
        if not files:
            return 0

        present = {_camera_path_to_url(entry['path']) for entry in files}
        stale = [url for url in queued if url not in present]
        for url in stale:
            self.db.remove_from_queue(url)
            self.db.clear_progress(url)
        if stale:
            logger.info(
                f"Removed {len(stale)} queued clip(s) no longer on the camera "
                f"({len(queued) - len(stale)} still pending)"
            )
        return len(stale)

    def _drain_queue(self, file_urls, cam):
        """Download every queued clip. Returns the number successfully fetched.

        Sets self._last_drain_ok False if the camera went away mid-queue, so the
        caller knows the queue was abandoned rather than completed.
        """
        completed = 0
        self._completed = 0
        self._last_drain_ok = True
        self._removed_missing = 0
        last_heartbeat = time.monotonic()

        for file_url in file_urls:
            # Between files as well as during them, so a stop requested while
            # the queue is long takes effect at once.
            self._check_cancelled()
            # Keep the camera's session alive between files. A transfer is
            # itself proof of life, so this only matters in the gaps.
            if cam and isinstance(cam, Camera) and time.monotonic() - last_heartbeat > 5:
                try:
                    cam.heartbeat()
                except Exception as e:
                    logger.debug(f"Heartbeat failed: {e}")
                last_heartbeat = time.monotonic()

            if not is_safe_clip_url(file_url):
                logger.warning(f"Dropping unsafe queue entry: {file_url!r}")
                self.db.remove_from_queue(file_url)
                continue

            file_name = file_url.rsplit('/', 1)[-1]
            try:
                file_path = self.resolve_in_download_dir(file_name)
            except ValueError as e:
                logger.warning(f"{e}; dropping from queue")
                self.db.remove_from_queue(file_url)
                continue

            if os.path.exists(file_path):
                logger.info(f"{file_name} already on disk; marking done")
                self.db.mark_downloaded(file_url)
                self.db.remove_from_queue(file_url)
                self.db.clear_progress(file_url)
                continue

            # Retry in place rather than abandoning the file. A skipped clip
            # used to wait a full scrape_interval (15 min by default) before it
            # was tried again, which is what turned four clips into an hour in
            # the driveway.
            for attempt in range(1, self.max_attempts + 1):
                try:
                    started = time.monotonic()
                    size = self._fetch_one(file_url, file_path, cam=cam)
                    elapsed = max(time.monotonic() - started, 0.001)
                    logger.info(
                        f"Downloaded {file_name} ({size / 1048576:.1f} MB "
                        f"in {elapsed:.0f}s, {size / 1048576 / elapsed:.1f} MB/s)"
                    )
                    self.db.mark_downloaded(file_url)
                    self.db.remove_from_queue(file_url)
                    self.db.clear_progress(file_url)
                    completed += 1
                    self._completed = completed
                    self._delete_from_camera(file_url, file_path, cam)
                    break
                except DownloadCancelled:
                    # Propagate: the partial file stays on disk and the clip
                    # stays queued, so the next cycle picks up where this left
                    # off.
                    raise
                except ClipGone:
                    # Don't retry and don't keep it queued -- the clip does not
                    # exist any more. Left in place these accumulate forever and
                    # are re-attempted on every cycle.
                    logger.info(f"{file_name} is gone from the camera; dropping from queue")
                    self.db.remove_from_queue(file_url)
                    self.db.clear_progress(file_url)
                    self._removed_missing += 1
                    break
                except CameraOffline:
                    logger.warning("Camera went away mid-queue; pausing downloads")
                    self._last_drain_ok = False
                    return completed
                except Exception as e:
                    if attempt >= self.max_attempts:
                        logger.error(f"Giving up on {file_url} after {attempt} attempts: {e}")
                        self.db.clear_progress(file_url)
                        break
                    backoff = 2 ** (attempt - 1)
                    logger.warning(
                        f"Attempt {attempt}/{self.max_attempts} for {file_name} "
                        f"failed ({e}); retrying in {backoff}s"
                    )
                    self._interruptible_sleep(backoff)
                    # Re-probe so a genuinely dropped link is noticed here
                    # rather than after three futile attempts.
                    if cam and isinstance(cam, Camera) and not cam.check_camera_connection(force=True):
                        logger.warning("Camera is no longer reachable; pausing downloads")
                        self._last_drain_ok = False
                        return completed

        return completed


class DownloadsDB:
    # Schema setup and JSON migration only need to happen once per process, not
    # on every instantiation -- the web routes build one of these per request.
    _initialised = set()
    _init_lock = threading.Lock()

    def __init__(self, config):
        if not isinstance(config, Config):
            raise Exception("Config is not of class Config")
        config_data = config.config_data

        self.db_path = f"{config_data['video_path']}/dashy.db"
        with DownloadsDB._init_lock:
            if self.db_path not in DownloadsDB._initialised:
                self._init_db()
                self._migrate_json(config_data['video_path'])
                DownloadsDB._initialised.add(self.db_path)

    def _connect(self):
        """Open a connection configured for concurrent use.

        WAL plus a busy timeout keeps the downloader thread's progress writes
        from colliding with the web threads' reads; the default configuration
        raised "database is locked" under the UI's polling.
        """
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextlib.contextmanager
    def _db(self):
        """Transactional connection that is always closed.

        `with sqlite3.connect(...)` commits but does not close, so the previous
        code leaked a file descriptor on every call -- and there is a call per
        UI poll.
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        with self._db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS downloaded (url TEXT PRIMARY KEY)")
            conn.execute("CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS progress (
                    url TEXT PRIMARY KEY,
                    bytes_downloaded INTEGER DEFAULT 0,
                    total_bytes INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)

    def clear_all_progress(self):
        """Drop stale progress rows. Called once at startup.

        This used to run inside _init_db, so every `DownloadsDB(config)` wiped
        the table -- including the one built by /api/progress immediately before
        reading it, which is why the download progress bar never appeared.
        """
        with self._db() as conn:
            conn.execute("DELETE FROM progress")

    def _migrate_json(self, video_path):
        old_db_path = f"{video_path}/downloads.json"
        old_queue_path = f"{video_path}/downloads_queue.json"

        if os.path.exists(old_db_path):
            with open(old_db_path, 'r') as f:
                urls = json.load(f)
            with self._db() as conn:
                conn.executemany("INSERT OR IGNORE INTO downloaded (url) VALUES (?)", [(u,) for u in urls])
            os.rename(old_db_path, old_db_path + ".migrated")
            logger.info(f"Migrated {len(urls)} entries from downloads.json to SQLite")

        if os.path.exists(old_queue_path):
            with open(old_queue_path, 'r') as f:
                urls = json.load(f)
            with self._db() as conn:
                conn.executemany("INSERT OR IGNORE INTO queue (url) VALUES (?)", [(u,) for u in urls])
            os.rename(old_queue_path, old_queue_path + ".migrated")
            logger.info(f"Migrated {len(urls)} entries from downloads_queue.json to SQLite")

    def load_downloaded_files(self):
        with self._db() as conn:
            rows = conn.execute("SELECT url FROM downloaded").fetchall()
        return [r[0] for r in rows]

    def load_download_queue(self):
        with self._db() as conn:
            rows = conn.execute("SELECT url FROM queue").fetchall()
        return [r[0] for r in rows]

    def save_downloaded_files(self, downloaded_files):
        with self._db() as conn:
            conn.execute("DELETE FROM downloaded")
            conn.executemany("INSERT OR IGNORE INTO downloaded (url) VALUES (?)", [(u,) for u in downloaded_files])

    def save_download_queue(self, queue):
        with self._db() as conn:
            conn.execute("DELETE FROM queue")
            conn.executemany("INSERT OR IGNORE INTO queue (url) VALUES (?)", [(u,) for u in queue])

    def mark_downloaded(self, url):
        """Record one completed download.

        Replaces the old read-all / append / rewrite-the-whole-table dance,
        which raced with anything else writing to the table.
        """
        with self._db() as conn:
            conn.execute("INSERT OR IGNORE INTO downloaded (url) VALUES (?)", (url,))

    def append_download_queue(self, name):
        if not self.check_downloaded(name):
            if self.check_downloads_queue(name):
                logger.warning("Video already in queue")
            else:
                logger.info(f"Appending file {name} to downloads queue...")
                with self._db() as conn:
                    conn.execute("INSERT OR IGNORE INTO queue (url) VALUES (?)", (name,))

    def queue_length(self):
        with self._db() as conn:
            row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
        return row[0]

    def check_downloaded(self, file):
        with self._db() as conn:
            row = conn.execute("SELECT 1 FROM downloaded WHERE url = ?", (file,)).fetchone()
        return row is not None

    def check_downloads_queue(self, name):
        with self._db() as conn:
            row = conn.execute("SELECT 1 FROM queue WHERE url = ?", (name,)).fetchone()
        return row is not None

    def remove_from_queue(self, url):
        with self._db() as conn:
            conn.execute("DELETE FROM queue WHERE url = ?", (url,))

    def clear_queue(self):
        """Empty the queue. Returns how many entries were removed."""
        with self._db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
            conn.execute("DELETE FROM queue")
            conn.execute("DELETE FROM progress")
        return count

    def set_progress(self, url, bytes_downloaded, total_bytes):
        with self._db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO progress (url, bytes_downloaded, total_bytes, updated_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (url, bytes_downloaded, total_bytes))

    def get_progress(self):
        with self._db() as conn:
            row = conn.execute("""
                SELECT url, bytes_downloaded, total_bytes, updated_at
                FROM progress
                ORDER BY updated_at DESC LIMIT 1
            """).fetchone()
        if row:
            return {'url': row[0], 'bytes_downloaded': row[1], 'total_bytes': row[2], 'updated_at': row[3]}
        return None

    def clear_progress(self, url):
        with self._db() as conn:
            conn.execute("DELETE FROM progress WHERE url = ?", (url,))

    def remove_downloaded(self, filename):
        with self._db() as conn:
            conn.execute("DELETE FROM downloaded WHERE url LIKE ?", (f'%/{filename}',))

