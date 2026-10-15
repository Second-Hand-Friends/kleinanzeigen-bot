#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Tomblarom and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
set -euo pipefail
umask 077
cd /mnt/data
if [[ ! -t 0 ]]; then
  echo 'Manual login requires an interactive container console.' >&2
  exit 1
fi
mkdir -p /mnt/data/.vnc
password_file=/mnt/data/.vnc/passwd
if [[ ! -s $password_file ]]; then
  echo 'Choose a separate VNC password (exactly eight characters; do not use your account password).'
  x11vnc -storepasswd "$password_file"
fi
chmod 600 "$password_file"
session_dir=$(mktemp -d /tmp/browser-login.XXXXXX)
children=()
cleanup() {
  trap - EXIT INT TERM
  if (( ${#children[@]} )); then
    kill "${children[@]}" 2>/dev/null || true
    # Some window managers keep waiting after the X server disappears.
    for attempt in {1..50}; do
      mapfile -t remaining < <(jobs -pr)
      (( ${#remaining[@]} )) || break
      sleep 0.1
    done
    if (( ${#remaining[@]} )); then
      kill -KILL "${remaining[@]}" 2>/dev/null || true
    fi
    wait "${children[@]}" 2>/dev/null || true
  fi
  rm -rf "$session_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export DISPLAY=:99
Xvfb "$DISPLAY" -screen 0 1440x1080x24 -nolisten tcp >"$session_dir/xvfb.log" 2>&1 &
children+=("$!")
ready=false
for attempt in {1..50}; do
  if xdpyinfo >/dev/null 2>&1; then
    ready=true
    break
  fi
  kill -0 "${children[0]}" 2>/dev/null || break
  sleep 0.1
done
if [[ $ready != true ]]; then
  echo 'The virtual display could not start.' >&2
  exit 1
fi
echo 'session.screen0.rootCommand: /bin/true' >"$session_dir/fluxbox.init"
fluxbox -rc "$session_dir/fluxbox.init" >"$session_dir/fluxbox.log" 2>&1 &
children+=("$!")
# Handle VNC connections independently of X11 event polling.
x11vnc -display "$DISPLAY" -localhost -nolookup -threads -rfbport 5900 -rfbauth "$password_file" \
  -forever -shared >"$session_dir/vnc.log" 2>&1 &
children+=("$!")
/usr/bin/websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 \
  >"$session_dir/websockify.log" 2>&1 &
children+=("$!")
# Preserve the terminal for the interactive bot while supervising the desktop.
exec 3<&0
/bin/bash /opt/run.sh login "$@" <&3 3<&- &
exec 3<&-
children+=("$!")
echo 'Open http://<docker-host>:6080/vnc.html and connect using your VNC password.'
echo 'Log in manually in Chromium, then press ENTER in this console. Keep the console open until then.'
# A failure in any desktop component also ends the session.
wait -n "${children[@]}"
