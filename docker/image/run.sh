#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

set -e -u

##############################
# execute script with bash if loaded with other shell interpreter
##############################
if [ -z "${BASH_VERSINFO:-}" ]; then /usr/bin/env bash "$0" "$@"; exit; fi

set -o pipefail

trap 'echo >&2 "$(date +%H:%M:%S) Error - exited with status $? at line $LINENO:"; pr -tn $0 | tail -n+$((LINENO - 3)) | head -n7' ERR

##############################
# Browser desktop (KasmVNC + Openbox)
#
# Started unless DISPLAY is provided from outside (e.g. a host X11 server)
# or VNC_DISABLE=1. Runs as root unless started with docker run --user.
# Environment variables:
#   VNC_PASSWORD       the password
#   VNC_GEOMETRY       screen size, default 1440x900
#   GPU_ACCELERATION   auto (default) | on | off
#   GPU_RENDER_NODE    DRI render node, default: first /dev/dri/renderD*
##############################
VNC_USER=user
VNC_PORT=6080
VNC_DISPLAY=:99
VNC_PASSWORD_STORE=/mnt/data/.vnc-password

vnc_resolve_password() {
   local pw=""
   if [ -n "${VNC_PASSWORD:-}" ]; then
      pw=$VNC_PASSWORD
      vnc_password_source="VNC_PASSWORD"
   elif [ -s "$VNC_PASSWORD_STORE" ]; then
      pw=$(<"$VNC_PASSWORD_STORE")
      vnc_password_source="stored"
   else
      # 24 random bytes always yield more than 20 alphanumeric characters
      pw=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)
      if ! (umask 077 && printf '%s\n' "$pw" >"$VNC_PASSWORD_STORE") 2>/dev/null; then
         echo >&2 "WARN: Cannot persist the generated VNC password to [$VNC_PASSWORD_STORE]; it is only valid for this run."
      fi
      vnc_password_source="generated"
   fi
   [ "${#pw}" -ge 6 ] || { echo >&2 "ERROR: The VNC password (from $vnc_password_source) must be at least 6 characters long."; exit 1; }
   vnc_password=$pw
}

vnc_gpu_args() {
   local mode=${GPU_ACCELERATION:-auto} node=${GPU_RENDER_NODE:-}
   gpu_args=()
   case "$mode" in
      auto|on|off) ;;
      *) echo >&2 "ERROR: GPU_ACCELERATION must be auto, on or off (got [$mode])."; exit 1 ;;
   esac
   if [ "$mode" = off ]; then
      echo "Desktop rendering: software (GPU_ACCELERATION=off)"
      return
   fi
   if [ -z "$node" ]; then
      node=$(compgen -G '/dev/dri/renderD*' | head -n1 || true)
   fi
   if [ -n "$node" ] && [ -r "$node" ] && [ -w "$node" ]; then
      gpu_args=(-hw3d -drinode "$node")
      echo "Desktop rendering: GPU via [$node]"
      return
   elif [ "$mode" = on ]; then
      echo >&2 "ERROR: GPU_ACCELERATION=on but no usable render node found (${node:-none in /dev/dri})."
      echo >&2 "       Pass the device and its group, e.g.: --device /dev/dri --group-add \$(getent group render | cut -d: -f3)"
      exit 1
   else
      echo "Desktop rendering: software (no readable/writable render node; see GPU_ACCELERATION in the README)"
   fi
}

start_desktop() {
   # arbitrary --user ids have no usable home directory
   if [ ! -w "${HOME:-/nonexistent}" ]; then
      export HOME=/tmp/home
      mkdir -p "$HOME"
   fi

   if ! locale -a | grep -qi "^${LANG%%.*}\."; then
      echo >&2 "WARN: Locale [$LANG] is not available in this image. Available: $(locale -a | tr '\n' ' ')"
   fi

   local vnc_password vnc_password_source
   vnc_resolve_password

   local gpu_args
   vnc_gpu_args
   if [ "${#gpu_args[@]}" -eq 0 ]; then
      # no GPU: let Chromium render WebGL in software (SwiftShader) instead of disabling it
      export KLEINANZEIGEN_BOT_CHROMIUM_FLAGS="${KLEINANZEIGEN_BOT_CHROMIUM_FLAGS:-} --enable-unsafe-swiftshader"
   fi

   printf '%s\n%s\n' "$vnc_password" "$vnc_password" | kasmvncpasswd -u "$VNC_USER" -w -o >/dev/null

   mkdir -p /tmp/.X11-unix
   # leftovers of a killed container (docker kill + docker start) would make Xvnc refuse to start
   rm -f "/tmp/.X${VNC_DISPLAY#:}-lock" "/tmp/.X11-unix/X${VNC_DISPLAY#:}"
   Xvnc "$VNC_DISPLAY" \
      -geometry "${VNC_GEOMETRY:-1440x900}" -depth 24 \
      "${gpu_args[@]}" \
      -websocketPort "$VNC_PORT" -interface 0.0.0.0 -localhost no \
      -PublicIP 127.0.0.1 \
      -SecurityTypes None -sslOnly 0 \
      -httpd /usr/share/kasmvnc/www >/tmp/xvnc.log 2>&1 &
   local xvnc_pid=$!

   for _ in $(seq 100); do
      [ -S "/tmp/.X11-unix/X${VNC_DISPLAY#:}" ] && break
      kill -0 "$xvnc_pid" 2>/dev/null || break
      sleep 0.1
   done
   if [ ! -S "/tmp/.X11-unix/X${VNC_DISPLAY#:}" ]; then
      echo >&2 "ERROR: The KasmVNC X server did not start:"
      cat >&2 /tmp/xvnc.log
      exit 1
   fi

   export DISPLAY=$VNC_DISPLAY
   openbox >/tmp/openbox.log 2>&1 &

   echo "################################################################"
   echo " Browser desktop:  http://localhost:$VNC_PORT  (publish the port, e.g. -p 127.0.0.1:$VNC_PORT:$VNC_PORT)"
   echo " Username:         $VNC_USER"
   case "$vnc_password_source" in
      generated)
         echo " Password:         $vnc_password"
         echo " (generated on first start and saved as .vnc-password in your data directory,"
         echo "  so you can read it later even though this log is gone with the container;"
         echo "  set VNC_PASSWORD to use your own)" ;;
      stored)
         echo " Password:         stored as .vnc-password in your data directory" ;;
      *)
         echo " Password:         from $vnc_password_source" ;;
   esac
   echo "################################################################"
}

if [ -f "$INIT_SH_FILE" ]; then
   source "$INIT_SH_FILE"
fi

if [ -z "${DISPLAY:-}" ] && [ "${VNC_DISABLE:-0}" != 1 ]; then
   start_desktop
fi

cd /mnt/data

exec /opt/kleinanzeigen-bot --config "$CONFIG_FILE" "$@"
