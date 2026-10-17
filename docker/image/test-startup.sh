#!/usr/bin/env bash
# SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

# Container regression checks for docker/image/run.sh: account selection for existing data
# directories, restart after a killed container, and the LANG override.
# Never contacts kleinanzeigen.de: the bot only runs "help".
#
# Usage: bash docker/image/test-startup.sh [image]

set -eu

if [ -z "${BASH_VERSINFO:-}" ]; then /usr/bin/env bash "$0" "$@"; exit; fi

set -o pipefail

image=${1:-second-hand-friends/kleinanzeigen-bot}
work=$(mktemp -d)
containers=()
prefix=kab-startup-test-$$
failures=0

cleanup() {
   [ "${#containers[@]}" -eq 0 ] || docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
   docker volume ls -q --filter "name=^$prefix-" | xargs -r docker volume rm -f >/dev/null 2>&1 || true
   rm -rf "$work"
}
trap cleanup EXIT

# run.sh sources this file as the account the bot will run as
cat >"$work/init.sh" <<'EOF'
echo "UID=$(id -u)"
if touch /mnt/data/.probe 2>/dev/null; then rm -f /mnt/data/.probe; echo "WRITABLE=1"; else echo "WRITABLE=0"; fi
echo "LC_CTYPE_LINE=$(locale | grep '^LC_CTYPE=')"
EOF
chmod 644 "$work/init.sh"

check() { # name, grep pattern, text
   if grep -q -- "$2" <<<"$3"; then
      echo "ok   - $1"
   else
      echo "FAIL - $1 (expected /$2/)"
      echo "$3" | sed 's/^/         /'
      failures=$((failures + 1))
   fi
}

# create a volume and prepare its content as root
volume() { # name, shell snippet working in /mnt/data
   local name=$prefix-$1
   docker volume create "$name" >/dev/null
   docker run --rm -v "$name:/mnt/data" --entrypoint bash "$image" -c "cd /mnt/data && $2"
   echo "$name"
}

# start the bot with the probe init script and print what it reported
probe() { # volume, extra docker run args...
   local vol=$1
   shift
   docker run --rm -v "$vol:/mnt/data" -v "$work/init.sh:/init.sh:ro" \
      -e INIT_SH_FILE=/init.sh -e VNC_DISABLE=1 "$@" "$image" help 2>&1
}

http_code() { curl -s -o /dev/null -w '%{http_code}' "$@" || true; }

# wait until the desktop answers 401 and return the published address
wait_for_desktop() { # container
   local addr
   for _ in $(seq 60); do
      addr=$(docker port "$1" 6080/tcp | head -n1)
      [ "$(http_code "http://$addr/")" = 401 ] && { echo "$addr"; return; }
      sleep 1
   done
   echo "-"
}

out=$(probe "$(volume empty 'true')")
check "empty data directory: runs as user" '^UID=1000$' "$out"
check "empty data directory: is writable" '^WRITABLE=1$' "$out"

out=$(probe "$(volume legacy 'touch bot.log')")
check "root-owned files: keeps running as root" '^UID=0$' "$out"
check "root-owned files: logs the chown hint" 'chown -R 1000:1000' "$out"

out=$(probe "$(volume mixed 'touch bot.log && chown 1000:1000 . && chmod 644 bot.log')")
check "user-owned directory with root-owned file: keeps running as root" '^UID=0$' "$out"
check "user-owned directory with root-owned file: is writable" '^WRITABLE=1$' "$out"

out=$(probe "$(volume foreign 'touch config.yaml && chown 2000:2000 config.yaml && chown root:root . && chmod 755 .')")
check "root-owned directory with user-owned files: runs as their owner" '^UID=2000$' "$out"
check "root-owned directory with user-owned files: is writable" '^WRITABLE=1$' "$out"

out=$(probe "$(volume forced 'chown 1000:1000 .')" -e RUN_AS_ROOT=1)
check "RUN_AS_ROOT=1: runs as root" '^UID=0$' "$out"

out=$(probe "$(volume explicit 'touch config.yaml && chown -R 1234:1234 .')" --user 1234:1234)
check "--user: runs as that account" '^UID=1234$' "$out"
check "--user: is writable" '^WRITABLE=1$' "$out"

out=$(probe "$(volume lang 'true')" -e LANG=en_US.UTF-8)
check "LANG override reaches the locale" '^LC_CTYPE_LINE=LC_CTYPE="en_US.UTF-8"$' "$out"

# a killed container leaves the X11 lock and socket behind; the desktop must still come back
cid=$(docker run -d --init -e VNC_PASSWORD=restart-test -p 127.0.0.1::6080 \
   --entrypoint /bin/bash "$image" -c 'bash /opt/run.sh help >/tmp/run.log 2>&1 & sleep 300')
containers+=("$cid")
addr=$(wait_for_desktop "$cid")
check "desktop answers 401 without password" '^127.0.0.1:' "$addr"
docker kill "$cid" >/dev/null
docker start "$cid" >/dev/null
addr=$(wait_for_desktop "$cid")
check "desktop answers 401 again after kill and start" '^127.0.0.1:' "$addr"
if [ "$addr" != "-" ]; then
   check "desktop accepts the password after kill and start" '^200$' \
      "$(http_code -u user:restart-test "http://$addr/vnc.html")"
fi

if [ "$failures" -gt 0 ]; then
   echo "$failures check(s) failed"
   exit 1
fi
echo "all checks passed"
