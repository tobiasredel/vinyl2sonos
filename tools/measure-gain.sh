#!/usr/bin/env bash
# Measures the raw peak level of your USB phono ADC while a record is
# playing and suggests a GAIN_DB value for vinyl-capture.service.
#
# Usage: sudo ./tools/measure-gain.sh
set -euo pipefail

ALSA_DEVICE="${ALSA_DEVICE:-$(grep -o 'ALSA_DEVICE=[^ ]*' /etc/systemd/system/vinyl-capture.service 2>/dev/null | cut -d= -f2 || true)}"
ALSA_DEVICE="${ALSA_DEVICE:-hw:CODEC,0}"

[ "$(id -u)" -eq 0 ] || { echo "please run with sudo"; exit 1; }

echo "Stopping vinyl-capture to free the audio device..."
systemctl stop vinyl-capture

restore() { systemctl start vinyl-capture; echo "vinyl-capture restarted."; }
trap restore EXIT

echo "Put on a record now (a typical, not-too-quiet passage)."
echo "Waiting for signal, then measuring for 30 seconds..."

arecord -D "$ALSA_DEVICE" -f S16_LE -r 44100 -c 2 -t raw 2>/dev/null | python3 - <<'EOF'
import sys, time, math
from array import array
BLOCK = 17640
def peak(b):
    a = array("h"); a.frombytes(b)
    return max(max(a), -min(a))
def read_block(s):
    buf = bytearray()
    while len(buf) < BLOCK:
        c = s.read(BLOCK - len(buf))
        if not c: return None
        buf.extend(c)
    return bytes(buf)
stdin = sys.stdin.buffer
while True:
    b = read_block(stdin)
    if b is None: sys.exit(1)
    if peak(b) > 1000: break
print("Signal detected - measuring 30 s...", flush=True)
peaks = []
t0 = time.time()
while time.time() - t0 < 30:
    b = read_block(stdin)
    if b is None: break
    peaks.append(peak(b))
mx = max(peaks)
# target: peaks around 80 % full scale, leaving headroom for louder records
gain = max(0, int(20 * math.log10(26000 / mx)))
print(f"\nMax peak: {mx} / 32767  ({100*mx//32767} % full scale)")
print(f"Suggested GAIN_DB: {gain}")
print(f"\nEdit /etc/systemd/system/vinyl-capture.service:")
print(f"  Environment=GAIN_DB={gain}")
print(f"  Environment=GATE_ON={int(1200 * 10**(gain/20))}")
print(f"  Environment=GATE_OFF={int(400 * 10**(gain/20))}")
print(f"then: sudo systemctl daemon-reload && sudo systemctl restart vinyl-capture")
EOF
