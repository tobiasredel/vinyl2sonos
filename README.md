# vinyl2sonos 🎵

Turn a Raspberry Pi with a USB turntable (or USB phono preamp) into a
**fully automatic vinyl streamer for Sonos** — or any AirPlay 2 speakers.

Drop the needle → your speakers start playing within seconds, **lossless**
(ALAC over AirPlay 2). Lift the needle → playback stops by itself. And
while your record spins, Shazam-based track recognition shows the **real
song title, artist, album and cover art** in the Sonos app.

```
turntable ──USB──▶ arecord ──▶ sox (subsonic filter + gain/limiter)
                                 │
                                 ▼
                         vinyl-gate.py  (silence gate + autostart)
                                 │            │
                                 ▼            ▼ 10 s snapshots (tmpfs)
                     OwnTone named pipe   vinyl-recognize.py ──▶ Shazam
                                 │            │
                                 ▼            ▼ title/artist/album/cover
                          OwnTone server ◀────┘
                                 │  AirPlay 2 (lossless, multiroom)
                                 ▼
                     Sonos / any AirPlay 2 speakers
```

## Features

- **Autostart / autostop** — a silence gate watches the phono signal and
  opens/closes the stream; OwnTone's pipe autostart does the rest. No app
  interaction needed, ever.
- **Lossless multiroom** — ALAC over AirPlay 2 with PTP timing
  (OwnTone ≥ 29.1 includes explicit Sonos fixes). Group more rooms in the
  Sonos app while playing.
- **Track recognition** — periodic 10 s snapshots are identified via
  Shazam; title, artist, album and cover art appear in the Sonos app and
  the OwnTone web UI. Optional (`WITH_RECOGNITION=no`).
- **Pop/click rejection** — the gate requires 0.4 s of sustained signal, so
  switching your turntable on/off doesn't trigger playback.
- **Consistent start volume** — every session starts at a configurable
  volume instead of whatever you cranked it to last night.
- **Transport lock** — pause/stop/skip in the Sonos app are meaningless for
  vinyl (the needle keeps going), so they're automatically undone within
  ~2 s. Only the needle stops playback. (`LOCK_TRANSPORT=no` to disable.)
- **Subsonic filter** — 22 Hz high-pass removes turntable rumble before it
  eats your headroom (and your subwoofer).
- **SD-card friendly** — snapshots live in tmpfs, the pipes carry
  everything else.

## Hardware

- Raspberry Pi (a Pi 3 B+ is plenty; Ethernet recommended, Wi-Fi works)
- Turntable with USB output, **or** analog turntable + USB phono preamp
  (e.g. Behringer UFO202). Anything that shows up in `arecord -l` works.
- Raspberry Pi OS Lite (bullseye, bookworm or trixie)

## Install

```sh
git clone https://github.com/tobiasredel/vinyl2sonos.git
cd vinyl2sonos
sudo ./install.sh
```

Then open the OwnTone web UI at `http://<pi-ip>:3689`, enable your
speaker(s) under **Outputs** — done. Drop a needle.

### Install options

Set as environment variables before `install.sh`:

| Variable           | Default      | Meaning                                   |
|--------------------|--------------|-------------------------------------------|
| `PIPE_NAME`        | `Turntable`  | Name shown in OwnTone / the Sonos app      |
| `ALSA_DEVICE`      | `hw:CODEC,0` | Capture device (see `arecord -l`)          |
| `WITH_RECOGNITION` | `yes`        | `no` skips the Shazam recognition service  |

Example: `sudo PIPE_NAME=Vinyl ALSA_DEVICE=hw:UFO202,0 ./install.sh`

## Tuning

All knobs live as `Environment=` lines in
`/etc/systemd/system/vinyl-capture.service`
(apply with `sudo systemctl daemon-reload && sudo systemctl restart vinyl-capture`):

| Variable         | Default | Meaning                                          |
|------------------|---------|--------------------------------------------------|
| `GAIN_DB`        | `12`    | Digital gain — many USB phono ADCs record far too quietly. Run `sudo ./tools/measure-gain.sh` to get a suggestion for **your** setup. A limiter prevents clipping. |
| `GATE_ON`        | `4800`  | Peak level (0–32767, after gain) that opens the gate |
| `GATE_OFF`       | `1600`  | Level below which silence is counted             |
| `GATE_HOLD_SECS` | `10`    | Silence duration before playback stops           |
| `GATE_OPEN_SECS` | `0.4`   | Sustained signal required to start (pop filter)  |
| `START_VOLUME`   | `18`    | Speaker volume at session start; empty = keep    |
| `LOCK_TRANSPORT` | `yes`   | Auto-resume if paused/stopped while spinning     |
| `HIGHPASS_HZ`    | `22`    | Subsonic filter corner frequency                 |
| `SNAPSHOT_SECS`  | `40`    | Interval between recognition attempts            |

## Troubleshooting

- **Nothing plays:** check `systemctl status vinyl-capture owntone` and
  `journalctl -u vinyl-capture -f` — you should see
  `signal detected … gate open` when music plays. No line? Lower `GATE_ON`.
- **Playback starts by itself:** raise `GATE_ON` and/or `GATE_OPEN_SECS`
  (dirty records with loud surface noise can trigger the gate).
- **Too quiet:** run `sudo ./tools/measure-gain.sh` while a record plays.
- **No track names:** `journalctl -u vinyl-recognize -f` — recognition
  needs internet access and works best with reasonably well-known music.
- **Wi-Fi dropouts on a Pi 3:** the installer's biggest enemy is 2.4 GHz
  congestion. Disable Wi-Fi power save or use Ethernet.
- **Latency:** ~2 s end-to-end is inherent to AirPlay buffering. Fine for
  listening — this is not a DJ monitor.

## How it works, in one paragraph

`arecord` captures the phono signal (44.1 kHz/16 bit), `sox` removes
subsonic rumble and applies make-up gain with a limiter. `vinyl-gate.py`
measures peak levels in 0.1 s blocks: on sustained signal it opens the
OwnTone named pipe (writing a small prebuffer so song intros aren't
clipped), on sustained silence it closes it — OwnTone autostarts and
autostops playback accordingly and streams to your AirPlay 2 outputs.
While the gate is open, 10-second WAV snapshots land in tmpfs, where
`vinyl-recognize.py` picks them up, queries Shazam via
[shazamio](https://github.com/shazamio/ShazamIO) and feeds
title/artist/album/cover into OwnTone's metadata pipe in Shairport-Sync
format.

## Credits

- [OwnTone](https://owntone.github.io/owntone-server/) does the heavy
  lifting: library, pipe autostart, AirPlay 2 streaming.
- [shazamio](https://github.com/shazamio/ShazamIO) for track recognition.

## License

MIT — see [LICENSE](LICENSE).
