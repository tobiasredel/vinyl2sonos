#!/usr/bin/env bash
# vinyl2sonos installer for Raspberry Pi OS (bullseye/bookworm/trixie)
#
# Usage:   sudo ./install.sh
# Options via environment variables:
#   PIPE_NAME=Turntable       display/pipe name in OwnTone & Sonos app
#   ALSA_DEVICE=hw:CODEC,0    ALSA capture device (see: arecord -l)
#   WITH_RECOGNITION=yes      set to "no" to skip Shazam track recognition
set -euo pipefail

PIPE_NAME="${PIPE_NAME:-Turntable}"
ALSA_DEVICE="${ALSA_DEVICE:-hw:CODEC,0}"
WITH_RECOGNITION="${WITH_RECOGNITION:-yes}"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PIPE_DIR=/srv/owntone/pipes

msg()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "please run with sudo: sudo ./install.sh"

. /etc/os-release
case "${VERSION_CODENAME:-}" in
  bullseye|bookworm|trixie) ;;
  *) fail "unsupported release '${VERSION_CODENAME:-unknown}' - supported: bullseye, bookworm, trixie" ;;
esac

msg "Installing packages (sox, alsa-utils, avahi, python3-venv)..."
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q sox alsa-utils avahi-daemon python3-venv wget gpg curl

if ! apt-cache policy owntone 2>/dev/null | grep -q Candidate; then
  msg "Adding the OwnTone apt repository (${VERSION_CODENAME})..."
  wget -q -O - https://raw.githubusercontent.com/owntone/owntone-apt/refs/heads/master/repo/rpi/owntone.gpg \
    | gpg --dearmor --yes --output /usr/share/keyrings/owntone-archive-keyring.gpg
  wget -q -O /etc/apt/sources.list.d/owntone.list \
    "https://raw.githubusercontent.com/owntone/owntone-apt/refs/heads/master/repo/rpi/owntone-${VERSION_CODENAME}.list"
  apt-get update -q
fi

msg "Installing OwnTone..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -q owntone

msg "Creating the owntone system user and pipes..."
id owntone >/dev/null 2>&1 || adduser --system --group --no-create-home --home /srv/owntone owntone
usermod -aG audio owntone
mkdir -p "$PIPE_DIR"
[ -p "$PIPE_DIR/$PIPE_NAME.fifo" ] || mkfifo "$PIPE_DIR/$PIPE_NAME.fifo"
[ -p "$PIPE_DIR/$PIPE_NAME.fifo.metadata" ] || mkfifo "$PIPE_DIR/$PIPE_NAME.fifo.metadata"
chown -R owntone:owntone /srv/owntone
mkdir -p /var/cache/owntone && chown -R owntone:owntone /var/cache/owntone
touch /var/log/owntone.log && chown owntone:owntone /var/log/owntone.log

msg "Configuring OwnTone (/etc/owntone.conf)..."
sed -i 's|uid = "root"|uid = "owntone"|' /etc/owntone.conf
sed -i "s|directories = { \"/srv/music\" }|directories = { \"$PIPE_DIR\" }|" /etc/owntone.conf
sed -i "s|name = \"My Music on %h\"|name = \"$PIPE_NAME\"|" /etc/owntone.conf
if ! grep -q "$PIPE_DIR" /etc/owntone.conf; then
  echo "NOTE: /etc/owntone.conf was already customized - please set the library"
  echo "      directories to { \"$PIPE_DIR\" } yourself."
fi

msg "Installing the capture service..."
install -m 755 "$REPO_DIR/bin/vinyl-gate.py" /usr/local/bin/vinyl-gate.py
sed -e "s|__PIPE_NAME__|$PIPE_NAME|g" -e "s|hw:CODEC,0|$ALSA_DEVICE|" \
  "$REPO_DIR/systemd/vinyl-capture.service" > /etc/systemd/system/vinyl-capture.service

if [ "$WITH_RECOGNITION" = "yes" ]; then
  msg "Setting up track recognition (shazamio venv, this takes a while on a Pi)..."
  install -m 755 "$REPO_DIR/bin/vinyl-recognize.py" /usr/local/bin/vinyl-recognize.py
  mkdir -p /opt/vinyl-recognize
  [ -x /opt/vinyl-recognize/venv/bin/python ] || python3 -m venv /opt/vinyl-recognize/venv
  /opt/vinyl-recognize/venv/bin/pip install -q --upgrade shazamio audioop-lts
  sed "s|__PIPE_NAME__|$PIPE_NAME|g" \
    "$REPO_DIR/systemd/vinyl-recognize.service" > /etc/systemd/system/vinyl-recognize.service
fi

msg "Enabling services..."
systemctl daemon-reload
systemctl enable --now owntone vinyl-capture
[ "$WITH_RECOGNITION" = "yes" ] && systemctl enable --now vinyl-recognize

IP=$(hostname -I | awk '{print $1}')
cat <<EOF

--------------------------------------------------------------------
 Done! Final steps:

 1. Open the OwnTone web UI:   http://$IP:3689
    and enable your AirPlay speaker(s) under "Outputs".
 2. Drop the needle - playback starts automatically within seconds.
 3. Too quiet / too loud? Run:  sudo ./tools/measure-gain.sh
    and adjust GAIN_DB in /etc/systemd/system/vinyl-capture.service
    (then: sudo systemctl daemon-reload && sudo systemctl restart vinyl-capture)
--------------------------------------------------------------------
EOF
