#!/usr/bin/env python3
"""Silence gate: reads raw PCM (S16_LE, stereo) from stdin and forwards it
to an OwnTone named pipe only while an actual music signal is present.

Gate open   -> FIFO is opened, prebuffer + live audio flow to OwnTone,
               which autostarts playback on the selected AirPlay outputs.
Gate closed -> FIFO is closed (the EOF makes OwnTone stop playback and
               re-arms the pipe autostart for the next record).

Configuration via environment variables:
  GATE_ON        level (0..32767) that opens the gate          (default 1200)
  GATE_OFF       level below which silence is counted          (default 400)
  GATE_HOLD_SECS seconds of silence until the gate closes      (default 10)
  GATE_OPEN_SECS seconds of sustained signal required to open
                 the gate - filters out short pops and clicks  (default 0.4)
  START_VOLUME   volume (0..100) applied to all selected
                 OwnTone outputs on every session start;
                 empty = leave volume untouched                (default empty)
  SNAPSHOT_DIR   directory (use tmpfs!) receiving periodic
                 10 s WAV snapshots for track recognition;
                 empty = disabled                              (default empty)
  SNAPSHOT_SECS  interval between snapshots                    (default 40)
  LOCK_TRANSPORT if "yes": pause/stop/skip from the Sonos app
                 are ignored - while the record is spinning,
                 playback is resumed automatically. Only the
                 needle can stop it.                           (default yes)
  NOTIFY_URL     HTTP endpoint (e.g. https://ntfy.sh/mytopic) that
                 receives a push message via POST on session
                 start/end; empty = disabled                   (default empty)
"""
import json
import os
import sys
import collections
import threading
import time
import urllib.request
import wave
from array import array

FIFO = sys.argv[1]
RATE = 44100
CHANNELS = 2
BYTES_PER_FRAME = 2 * CHANNELS
BLOCK_SECS = 0.1
BLOCK_BYTES = int(RATE * BLOCK_SECS) * BYTES_PER_FRAME

GATE_ON = int(os.environ.get("GATE_ON", "1200"))
GATE_OFF = int(os.environ.get("GATE_OFF", "400"))
HOLD_BLOCKS = int(float(os.environ.get("GATE_HOLD_SECS", "10")) / BLOCK_SECS)
OPEN_BLOCKS = max(1, int(float(os.environ.get("GATE_OPEN_SECS", "0.4")) / BLOCK_SECS))
# prebuffer written on gate open so the beginning of a song is not cut off
PREBUFFER_BLOCKS = OPEN_BLOCKS + 4


def read_block(stream):
    buf = bytearray()
    while len(buf) < BLOCK_BYTES:
        chunk = stream.read(BLOCK_BYTES - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def peak(block):
    samples = array("h")
    samples.frombytes(block)
    return max(max(samples), -min(samples))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


START_VOLUME = os.environ.get("START_VOLUME", "")
OWNTONE_API = "http://localhost:3689/api/outputs"


def reset_start_volume():
    """Resets all selected OwnTone outputs to the configured start volume."""
    if not START_VOLUME:
        return
    try:
        with urllib.request.urlopen(OWNTONE_API, timeout=3) as r:
            outputs = json.load(r)["outputs"]
        for o in outputs:
            if not o["selected"]:
                continue
            req = urllib.request.Request(
                f"{OWNTONE_API}/{o['id']}",
                data=json.dumps({"volume": int(START_VOLUME)}).encode(),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            urllib.request.urlopen(req, timeout=3).close()
            log(f"vinyl-gate: start volume {START_VOLUME} for '{o['name']}'")
    except Exception as e:
        log(f"vinyl-gate: setting start volume failed: {e}")


NOTIFY_URL = os.environ.get("NOTIFY_URL", "")


def notify(message, tag):
    """Sends a push message (ntfy-compatible) - in a thread so a slow
    server can never stall the audio path."""
    if not NOTIFY_URL:
        return

    def _send():
        try:
            req = urllib.request.Request(
                NOTIFY_URL,
                data=message.encode(),
                headers={"Title": "Turntable", "Tags": tag},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            log(f"vinyl-gate: notification failed: {e}")

    threading.Thread(target=_send, daemon=True).start()


LOCK_TRANSPORT = os.environ.get("LOCK_TRANSPORT", "yes").lower() in ("1", "yes", "true")
OWNTONE_PLAYER = "http://localhost:3689/api/player"
# set by main(): timestamp of gate opening, or None
GATE = {"open_since": None}


def transport_watchdog():
    """Resumes playback if it gets paused while the record is spinning.

    Pause/stop/skip from the Sonos app make no sense for vinyl (the needle
    keeps going); this thread turns them into no-ops."""
    while True:
        time.sleep(2)
        since = GATE["open_since"]
        # 5 s grace period after gate opening so autostart can kick in
        if since is None or time.time() - since < 5:
            continue
        try:
            with urllib.request.urlopen(OWNTONE_PLAYER, timeout=3) as r:
                state = json.load(r).get("state")
            if state != "play":
                req = urllib.request.Request(OWNTONE_PLAYER + "/play", method="PUT")
                urllib.request.urlopen(req, timeout=3).close()
                log("vinyl-gate: paused/stopped while record is spinning - resumed")
        except Exception:
            pass  # OwnTone briefly unreachable - retry in 2 s


SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", "")
SNAPSHOT_BLOCKS = int(float(os.environ.get("SNAPSHOT_SECS", "40")) / BLOCK_SECS)
SAMPLE_BLOCKS = int(10 / BLOCK_SECS)  # 10 s of audio per snapshot


def write_snapshot(blocks):
    """Writes the most recent blocks as WAV (atomically) for track recognition."""
    if not SNAPSHOT_DIR:
        return
    try:
        tmp = os.path.join(SNAPSHOT_DIR, "sample.wav.tmp")
        dst = os.path.join(SNAPSHOT_DIR, "sample.wav")
        with wave.open(tmp, "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(blocks))
        os.replace(tmp, dst)
    except Exception as e:
        log(f"vinyl-gate: snapshot failed: {e}")


def clear_snapshot():
    if SNAPSHOT_DIR:
        try:
            os.unlink(os.path.join(SNAPSHOT_DIR, "sample.wav"))
        except FileNotFoundError:
            pass
        except Exception:
            pass


def main():
    stdin = sys.stdin.buffer
    prebuffer = collections.deque(maxlen=PREBUFFER_BLOCKS)
    snapshot_buf = collections.deque(maxlen=SAMPLE_BLOCKS)
    blocks_open = 0
    fifo = None
    loud_streak = 0
    quiet_streak = 0

    log(f"vinyl-gate: on={GATE_ON} off={GATE_OFF} hold={HOLD_BLOCKS * BLOCK_SECS:.0f}s"
        + (" transport-lock" if LOCK_TRANSPORT else ""))
    if LOCK_TRANSPORT:
        threading.Thread(target=transport_watchdog, daemon=True).start()

    while True:
        block = read_block(stdin)
        if block is None:
            log("vinyl-gate: stdin EOF (arecord terminated)")
            break
        level = peak(block)

        if fifo is None:
            prebuffer.append(block)
            loud_streak = loud_streak + 1 if level >= GATE_ON else 0
            if loud_streak >= OPEN_BLOCKS:
                log(f"vinyl-gate: signal detected (level {level}) - gate open")
                notify("Record playing - playback started", "musical_note")
                reset_start_volume()
                # blocks until OwnTone is reading the pipe
                fifo = open(FIFO, "wb", buffering=0)
                GATE["open_since"] = time.time()
                try:
                    while prebuffer:
                        fifo.write(prebuffer.popleft())
                except BrokenPipeError:
                    fifo.close()
                    fifo = None
                    GATE["open_since"] = None
                quiet_streak = 0
                blocks_open = 0
                snapshot_buf.clear()
        else:
            snapshot_buf.append(block)
            blocks_open += 1
            # first snapshot once 10 s are available, then periodically
            if blocks_open == SAMPLE_BLOCKS or (
                blocks_open > SAMPLE_BLOCKS
                and (blocks_open - SAMPLE_BLOCKS) % SNAPSHOT_BLOCKS == 0
            ):
                write_snapshot(list(snapshot_buf))
            try:
                fifo.write(block)
            except BrokenPipeError:
                log("vinyl-gate: reader gone (OwnTone restart?) - gate closed")
                GATE["open_since"] = None
                fifo.close()
                fifo = None
                loud_streak = 0
                clear_snapshot()
                continue
            quiet_streak = quiet_streak + 1 if level < GATE_OFF else 0
            if quiet_streak >= HOLD_BLOCKS:
                log("vinyl-gate: silence - gate closed")
                notify("Playback ended - record finished or needle lifted", "stop_button")
                GATE["open_since"] = None
                fifo.close()
                fifo = None
                loud_streak = 0
                prebuffer.clear()
                clear_snapshot()

    if fifo is not None:
        fifo.close()


if __name__ == "__main__":
    main()
