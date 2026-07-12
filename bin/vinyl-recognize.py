#!/usr/bin/env python3
"""Track recognition for the turntable stream.

Watches the WAV snapshots written by vinyl-gate (SNAPSHOT_DIR/sample.wav),
identifies the current track via Shazam (shazamio) and writes title /
artist / album / cover art in Shairport-Sync metadata format to the
OwnTone metadata pipe. A new playback session first gets default
metadata ("Turntable"), then live updates as tracks are recognized.

Environment variables:
  SNAPSHOT_DIR  directory containing sample.wav        (default /run/vinyl)
  META_PIPE     path to the OwnTone metadata pipe
                (default /srv/owntone/pipes/Turntable.fifo.metadata)
  DEFAULT_TITLE title shown before recognition         (default Turntable)
"""
import asyncio
import base64
import errno
import os
import select
import sys
import urllib.request

from shazamio import Shazam

SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", "/run/vinyl")
SAMPLE = os.path.join(SNAPSHOT_DIR, "sample.wav")
META_PIPE = os.environ.get(
    "META_PIPE", "/srv/owntone/pipes/Turntable.fifo.metadata"
)
DEFAULT_TITLE = os.environ.get("DEFAULT_TITLE", "Turntable")
POLL_SECS = 3

DMAP = {"title": "minm", "artist": "asar", "album": "asal"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def meta_item(code, payload, item_type="core"):
    if isinstance(payload, str):
        payload = payload.encode()
    data = base64.b64encode(payload).decode()
    return (
        f"<item><type>{item_type.encode().hex()}</type>"
        f"<code>{code.encode().hex()}</code>"
        f"<length>{len(payload)}</length>"
        f"<data encoding=\"base64\">{data}</data></item>"
    )


def fetch_cover(url):
    """Downloads the cover art (max 1 MB, OwnTone's limit) - None on failure."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = r.read(1048576 + 1)
        if 2 <= len(data) <= 1048576:
            return data
        log(f"meta: cover too large ({len(data)} B) - skipped")
    except Exception as e:
        log(f"meta: cover download failed: {e}")
    return None


def send_metadata(title, artist="", album="", cover=None):
    """Writes metadata to the pipe without blocking; no reader -> skip.

    Cover payloads exceed the FIFO buffer (64 KB), so writing happens in a
    select loop for as long as OwnTone keeps reading."""
    payload = meta_item(DMAP["title"], title)
    if artist:
        payload += meta_item(DMAP["artist"], artist)
    if album:
        payload += meta_item(DMAP["album"], album)
    if cover:
        payload += meta_item("PICT", cover, item_type="ssnc")
    buf = payload.encode()
    try:
        fd = os.open(META_PIPE, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        if e.errno == errno.ENXIO:  # OwnTone is not reading right now
            log("meta: no reader on the pipe - skipped")
            return False
        raise
    try:
        while buf:
            _, ready, _ = select.select([], [fd], [], 5)
            if not ready:
                log("meta: pipe write stalled - aborted")
                return False
            try:
                n = os.write(fd, buf)
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    continue
                if e.errno == errno.EPIPE:
                    log("meta: reader gone while writing - aborted")
                    return False
                raise
            buf = buf[n:]
        return True
    finally:
        os.close(fd)


async def recognize(shazam, path):
    try:
        result = await asyncio.wait_for(shazam.recognize(path), timeout=25)
    except Exception as e:
        log(f"shazam: recognition failed: {e}")
        return None
    track = result.get("track")
    if not track:
        return None
    title = track.get("title", "")
    artist = track.get("subtitle", "")
    album = ""
    for section in track.get("sections", []):
        for m in section.get("metadata", []):
            if m.get("title") == "Album":
                album = m.get("text", "")
    cover_url = track.get("images", {}).get("coverart", "")
    return (title, artist, album, cover_url) if title else None


async def main():
    shazam = Shazam()
    last_mtime = None
    last_track = None
    session = False

    log(f"vinyl-recognize: watching {SAMPLE}")
    while True:
        try:
            mtime = os.stat(SAMPLE).st_mtime
        except FileNotFoundError:
            if session:
                log("vinyl-recognize: session ended")
            session = False
            last_track = None
            last_mtime = None
            await asyncio.sleep(POLL_SECS)
            continue

        if mtime == last_mtime:
            await asyncio.sleep(POLL_SECS)
            continue
        last_mtime = mtime

        if not session:
            session = True
            send_metadata(DEFAULT_TITLE, "Vinyl")
            log("vinyl-recognize: new session - default metadata sent")

        found = await recognize(shazam, SAMPLE)
        if found and found[:3] != last_track:
            title, artist, album, cover_url = found
            cover = fetch_cover(cover_url)
            ok = send_metadata(title, artist, album, cover)
            log(f"vinyl-recognize: {artist} - {title} ({album or 'no album'}"
                + (", with cover)" if cover else ", no cover)")
                + ("" if ok else " [not delivered]"))
            if ok:
                last_track = found[:3]
        await asyncio.sleep(POLL_SECS)


if __name__ == "__main__":
    asyncio.run(main())
