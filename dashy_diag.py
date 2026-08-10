#!/usr/bin/env python3
"""
Camera diagnostics: measure what the camera actually does, rather than guessing.

Answers the questions that decide download speed and whether camera commands
work, by testing against the real camera:

  * What link speed do we actually get, and does chunk size change it?
  * Does the camera honour HTTP Range requests (i.e. can we resume)?
  * Does keep-alive help, or does it insist on a connection per request?
  * How many parallel connections can it take before it starts failing?
  * Which query parameter does this firmware accept for set commands?
  * What does it send back -- JSON or XML?

Usage:
    python3 dashy_diag.py                 # uses config.json
    python3 dashy_diag.py --ip 192.168.1.254
    python3 dashy_diag.py --seconds 15    # longer throughput sample
"""

import argparse
import json
import os
import socket
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from viofo import (  # noqa: E402
    Camera, _CMD_CONTROL, _parse_camera_response, _new_session,
)
from dashy_config import Config  # noqa: E402


def header(text):
    print(f"\n{'=' * 68}\n{text}\n{'=' * 68}")


def find_camera(ip_override=None):
    if ip_override:
        for port in (80,):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3)
                if s.connect_ex((ip_override, port)) == 0:
                    return f"http://{ip_override}:{port}", ip_override
        print(f"Could not reach {ip_override}:80")
        sys.exit(1)

    cam = Camera(Config("config.json"), check_connection=True)
    if not cam.connected:
        print(f"Camera not reachable at any of {cam.cam_ip_list}")
        sys.exit(1)
    return cam.base_url, cam.connected_ip


def pick_test_clip(base_url, session):
    """Find the largest clip we can use as a throughput target."""
    from bs4 import BeautifulSoup
    for directory in ("/DCIM/Movie/RO", "/DCIM/Movie", "/DCIM/Movie/Parking/RO"):
        try:
            r = session.get(base_url + directory, timeout=(3, 30))
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all('a'):
            href = a.get('href') or ''
            if '.MP4' in href.upper() and 'del=1' not in href:
                return href
    return None


def test_link(ip):
    header("Link")
    # Association details decide the ceiling: 2.4 GHz tops out around 3-5 MB/s
    # on these cameras, 5 GHz gets you into double digits. No amount of client
    # tuning beats being on the wrong band.
    print(f"Camera IP: {ip}")
    try:
        import subprocess
        out = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            for line in out.stdout.splitlines():
                line = line.strip()
                if line.startswith(("Interface", "ssid", "channel", "txpower")):
                    print(f"  {line}")
        link = subprocess.run(["iw", "dev", "wlan0", "link"], capture_output=True, text=True, timeout=5)
        if link.returncode == 0:
            for line in link.stdout.splitlines():
                line = line.strip()
                if any(k in line for k in ("SSID", "freq", "bitrate", "signal")):
                    print(f"  {line}")
    except Exception:
        print("  (iw not available -- check band/signal manually)")
    print("\n  A 5 GHz association is worth more than any client-side tuning.")
    print("  If 'freq' is ~2.4xx, that alone caps you at a few MB/s.")


def test_throughput(base_url, clip, seconds):
    header("Throughput vs chunk size")
    if not clip:
        print("No clip found to test against; skipping.")
        return
    print(f"Test clip: {clip}\nSampling ~{seconds}s per chunk size.\n")
    print(f"{'chunk size':>12}  {'MB/s':>8}  {'MB read':>9}")
    for chunk_size in (2048, 65536, 262144, 1048576, 4194304):
        session = _new_session()
        try:
            started = time.monotonic()
            read = 0
            with session.get(base_url + clip, stream=True, timeout=(3, 30)) as r:
                if r.status_code != 200:
                    print(f"{chunk_size:>12}  HTTP {r.status_code}")
                    continue
                for block in r.iter_content(chunk_size=chunk_size):
                    read += len(block)
                    if time.monotonic() - started > seconds:
                        break
            elapsed = max(time.monotonic() - started, 0.001)
            print(f"{chunk_size:>12}  {read / 1048576 / elapsed:>8.2f}  {read / 1048576:>9.1f}")
        except requests.RequestException as e:
            print(f"{chunk_size:>12}  failed: {e}")
        finally:
            session.close()
    print("\n  Dashy used 2048 before this change. If the larger sizes are")
    print("  materially faster here, that gain is already in the downloader.")


def test_range(base_url, clip):
    header("Resume support (HTTP Range)")
    if not clip:
        print("No clip found to test against; skipping.")
        return
    session = _new_session()
    try:
        r = session.get(base_url + clip, headers={'Range': 'bytes=1048576-'},
                        stream=True, timeout=(3, 30))
        r.close()
        if r.status_code == 206:
            print("  206 Partial Content -- resume works.")
            print(f"  Content-Range: {r.headers.get('Content-Range')}")
            print("  A clip interrupted at 90% resumes instead of restarting.")
        elif r.status_code == 200:
            print("  200 OK -- Range ignored, no resume. Interrupted clips restart.")
        else:
            print(f"  HTTP {r.status_code} -- unexpected; no resume.")
    except requests.RequestException as e:
        print(f"  Failed: {e}")
    finally:
        session.close()


def test_keepalive(base_url, clip):
    header("Keep-alive")
    if not clip:
        print("No clip found to test against; skipping.")
        return

    def timed(session, count=4):
        times = []
        for _ in range(count):
            started = time.monotonic()
            r = session.get(base_url + clip, headers={'Range': 'bytes=0-65535'}, timeout=(3, 30))
            r.content
            r.close()
            times.append(time.monotonic() - started)
        return times

    session = _new_session()
    try:
        reused = timed(session)
        print(f"  Reused connection: {['%.3fs' % t for t in reused]}")
    finally:
        session.close()

    fresh = []
    for _ in range(4):
        s = _new_session()
        s.headers['Connection'] = 'close'
        try:
            fresh.extend(timed(s, count=1))
        finally:
            s.close()
    print(f"  New connection each: {['%.3fs' % t for t in fresh]}")
    if reused and fresh:
        saved = (sum(fresh) / len(fresh)) - (sum(reused) / len(reused))
        print(f"  Keep-alive saves ~{saved * 1000:.0f}ms per request.")


def test_concurrency(base_url, clip):
    header("Parallel connection tolerance")
    if not clip:
        print("No clip found to test against; skipping.")
        return
    import concurrent.futures
    print("  How many simultaneous requests before the camera starts failing?")
    print("  (The settings page used to open ~40 of these at once.)\n")
    for parallel in (2, 4, 8, 16):
        def one(_):
            s = requests.Session()
            try:
                r = s.get(base_url + clip, headers={'Range': 'bytes=0-32767'}, timeout=(3, 20))
                return r.status_code
            except Exception as e:
                return type(e).__name__
            finally:
                s.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            started = time.monotonic()
            results = list(pool.map(one, range(parallel)))
        elapsed = time.monotonic() - started
        ok = sum(1 for r in results if r in (200, 206))
        print(f"  {parallel:>2} parallel: {ok}/{parallel} succeeded in {elapsed:.1f}s"
              f"{'' if ok == parallel else '  <-- ' + ', '.join(sorted(set(str(r) for r in results if r not in (200, 206))))}")


def test_commands(base_url):
    header("Command format")
    session = _new_session()
    try:
        # Show the raw body so the wire format (XML vs JSON) is unambiguous.
        # Most firmware answers XML; if this camera answers JSON, that is worth
        # knowing.
        cmd = _CMD_CONTROL["FIRMWARE_VERSION"]
        r = session.get(base_url + "/", params={"custom": 1, "cmd": cmd}, timeout=(3, 10))
        body = r.text.strip()
        fmt = "XML" if body.startswith('<') else ("JSON" if body.startswith('{') else "unknown")
        print(f"  GET ?custom=1&cmd={cmd} (firmware version)")
        print(f"    HTTP {r.status_code}, Content-Type: {r.headers.get('Content-Type')}")
        print(f"    Wire format: {fmt}")
        print(f"    Body: {body[:300]!r}")
        print(f"    Parsed: {_parse_camera_response(r)}")

        # Bulk settings table: every setting in one call.
        print("\n  Bulk settings (cmd 3014):")
        r = session.get(base_url + "/",
                        params={"custom": 1, "cmd": _CMD_CONTROL["GET_ALL_SETTINGS"]},
                        timeout=(3, 15))
        bulk = _parse_all_settings(r.text)
        if bulk:
            print(f"    Returned {len(bulk)} settings in one request.")
            sample = list(bulk.items())[:6]
            print(f"    Sample cmd->value: {sample}")
        else:
            print(f"    No bulk table; Dashy will read settings individually.")
            print(f"    Body: {r.text.strip()[:200]!r}")

        # Which parameter name does this firmware accept for writes? Probed
        # against the beep setting: harmless, and written back to its own
        # current value so nothing actually changes.
        print("\n  Set-parameter name (writes current value back, so nothing changes):")
        for probe_cmd in (9094, 9403):
            r = session.get(base_url + "/", params={"custom": 1, "cmd": probe_cmd}, timeout=(3, 10))
            parsed = _parse_camera_response(r)
            current = parsed.get('cur_value')
            if current is None and parsed.get('rval') is not None:
                current = parsed.get('rval')      # XML puts a GET's value in <Status>
            if current is None:
                continue
            print(f"    cmd {probe_cmd} current value: {current!r}")
            for name in ("par", "str", "param_0"):
                r2 = session.get(base_url + "/",
                                 params={"custom": 1, "cmd": probe_cmd, name: current},
                                 timeout=(3, 10))
                p2 = _parse_camera_response(r2)
                verdict = "ACCEPTED" if p2.get('rval') == 0 else f"rejected (rval={p2.get('rval')})"
                print(f"      &{name}= -> {verdict}  {r2.text.strip()[:110]!r}")
            break
        else:
            print("    Could not read a settable value to probe with.")
    except requests.RequestException as e:
        print(f"  Failed: {e}")
    finally:
        session.close()


def test_playback_mode(base_url, clip, seconds):
    """Measure the throughput difference between recording and playback mode.

    This is the single biggest lever: in playback mode (cmd 3001 par=2) the
    SoC isn't encoding 4K and writing to the SD card while it serves the
    transfer.
    """
    header("Recording mode vs playback mode")
    if not clip:
        print("No clip found to test against; skipping.")
        return

    session = _new_session()

    def set_mode(mode):
        try:
            r = session.get(base_url + "/",
                            params={"custom": 1, "cmd": _CMD_CONTROL["CHANGE_MODE"], "par": mode},
                            timeout=(3, 15))
            return _parse_camera_response(r).get('rval') == 0
        except requests.RequestException:
            return False

    def measure():
        started = time.monotonic()
        read = 0
        try:
            with session.get(base_url + clip, stream=True, timeout=(3, 30)) as r:
                if r.status_code not in (200, 206):
                    return None
                for block in r.iter_content(chunk_size=262144):
                    read += len(block)
                    if time.monotonic() - started > seconds:
                        break
        except requests.RequestException:
            return None
        return read / 1048576 / max(time.monotonic() - started, 0.001)

    try:
        print("  Measuring while the camera is recording...")
        set_mode(1)
        time.sleep(1)
        recording = measure()

        print("  Switching to playback mode (recording stops)...")
        if not set_mode(2):
            print("  Camera refused playback mode; skipping comparison.")
            return
        time.sleep(1)
        playback = measure()

        print("  Restoring recording mode...")
        restored = set_mode(1)

        print()
        if recording and playback:
            print(f"    recording mode: {recording:6.2f} MB/s")
            print(f"    playback mode:  {playback:6.2f} MB/s   ({playback / recording:.1f}x)")
            if playback > recording * 1.2:
                print("\n  Playback mode is materially faster -- keep")
                print("  playback_mode_for_downloads enabled (the default).")
            else:
                print("\n  Little difference on this camera. You can set")
                print("  playback_mode_for_downloads=false to keep it recording.")
        else:
            print("    Could not measure one of the modes.")

        if not restored:
            print("\n  WARNING: the camera did not confirm the switch back to")
            print("  recording mode. Check it before driving.")
    finally:
        set_mode(1)
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Diagnose Viofo camera behaviour")
    parser.add_argument('--ip', help='Camera IP (default: read from config.json)')
    parser.add_argument('--seconds', type=float, default=8,
                        help='Seconds to sample per throughput test (default: 8)')
    parser.add_argument('--skip-concurrency', action='store_true',
                        help='Skip the parallel-connection test (it can upset the camera)')
    args = parser.parse_args()

    base_url, ip = find_camera(args.ip)
    print(f"Camera: {base_url}")

    session = _new_session()
    try:
        clip = pick_test_clip(base_url, session)
    finally:
        session.close()

    test_link(ip)
    test_commands(base_url)
    test_range(base_url, clip)
    test_keepalive(base_url, clip)
    test_throughput(base_url, clip, args.seconds)
    if not args.skip_concurrency:
        test_concurrency(base_url, clip)

    header("Done")
    print("Paste this output back and the download settings can be tuned to match.")


if __name__ == '__main__':
    main()
