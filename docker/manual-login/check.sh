#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
set -euo pipefail
image=${1:?Pass the optional manual-login image to test}
docker image inspect "$image" >/dev/null
# Never contact Kleinanzeigen. CLI login behavior is covered by the Python tests;
# this fake bot exercises the real desktop, password protection, and shutdown.
if docker run --rm --network=none "$image"; then
  echo 'The desktop accepted a noninteractive session.' >&2
  exit 1
fi
container=$(docker create -it --network=none --entrypoint bash "$image" -euc '
  mkdir -p /mnt/data/.vnc
  x11vnc -storepasswd offline1 /mnt/data/.vnc/passwd >/dev/null
  cat > /opt/kleinanzeigen-bot <<"BOT"
#!/bin/bash
set -eu
test "$*" = "--config /mnt/data/config.yaml login"
chromium --no-sandbox --disable-dev-shm-usage --user-data-dir=/mnt/data/offline-profile about:blank >/tmp/chromium.log 2>&1 &
browser=$!
trap '\''kill "$browser" 2>/dev/null || true; wait "$browser" 2>/dev/null || true'\'' EXIT
echo OFFLINE_LOGIN_READY
read -r
BOT
  chmod +x /opt/kleinanzeigen-bot
  exec /opt/manual-login.sh
')
trap 'docker rm -fv "$container" >/dev/null' EXIT
docker start "$container" >/dev/null
ready=false
for attempt in {1..30}; do
  if docker exec "$container" python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:6080/vnc.html", timeout=1)' 2>/dev/null \
    && docker exec "$container" bash -c 'DISPLAY=:99 xwininfo -root -tree | grep -q Chromium'; then
    ready=true
    break
  fi
  [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]] || break
  sleep 1
done
if [[ $ready != true ]]; then
  docker logs "$container"
  exit 1
fi
docker exec "$container" python3 -c '
import socket
with socket.create_connection(("127.0.0.1", 5900), timeout=5) as connection:
    assert connection.recv(12).startswith(b"RFB ")
    connection.sendall(b"RFB 003.008\n")
    count = connection.recv(1)[0]
    methods = connection.recv(count)
    assert 2 in methods and 1 not in methods, methods
'
test "$(docker exec "$container" stat -c %a /mnt/data/.vnc/passwd)" = 600
# ENTER must reach the supervised interactive process and end the whole desktop.
printf '\n' | timeout 20 script -q -e -c "docker attach $container" /dev/null >/dev/null
test "$(docker wait "$container")" = 0
echo 'Offline manual-login desktop checks passed.'
