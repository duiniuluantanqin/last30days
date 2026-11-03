#!/bin/sh
# Executed as the existing non-root SSH user. Never replace either SQLite file.
set -eu
root=$(cd "$1" && pwd -P)
identifier=$2
origin=$3
case "$identifier" in
    analytics-*[!0-9]*|analytics-) echo "Invalid analytics release ID" >&2; exit 1 ;;
    analytics-[0-9]*) ;;
    *) echo "Invalid analytics release ID" >&2; exit 1 ;;
esac

deploy_user=$(id -un)
if [ "$(loginctl show-user "$deploy_user" --property=Linger --value)" != yes ]; then
    echo "Enable persistent user services once on ECS: sudo loginctl enable-linger $deploy_user" >&2
    exit 1
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
systemctl --user show-environment > /dev/null
if systemctl --system is-active --quiet forum-analytics.service; then
    echo "Stop the old system service once before migration: sudo systemctl disable --now forum-analytics.service" >&2
    exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'

analytics=$root/analytics
for path in "$analytics" "$analytics/releases" "$root/state"; do
    if [ -L "$path" ]; then
        echo "Refusing symlinked service/state directory: $path" >&2
        exit 1
    fi
done
mkdir -p "$analytics/releases" "$root/state"
active=$analytics/active
previous=
if [ -L "$active" ]; then
    previous=$(readlink "$active")
    case "$previous" in
        releases/analytics-*[!0-9]*|releases/analytics-) echo "Invalid previous release" >&2; exit 1 ;;
        releases/analytics-[0-9]*) test -d "$analytics/$previous" ;;
        *) echo "Invalid previous release" >&2; exit 1 ;;
    esac
elif [ -e "$active" ]; then
    echo "Analytics active path must be a managed symlink" >&2
    exit 1
fi
release=$analytics/releases/$identifier
mkdir "$release"
tar -xzf "$root/incoming/$identifier.tar.gz" -C "$release"
if ! python3 -m venv "$release/.venv"; then
    echo "Install Python 3.11+ and its venv package on ECS, then retry deployment" >&2
    exit 1
fi
"$release/.venv/bin/python" -m pip install --disable-pip-version-check "$release"
"$release/.venv/bin/python" -c 'from forum_archive.analytics import AnalyticsHandler; from forum_archive.config import Settings; import sys; Settings.load(sys.argv[1])' "$release/config.toml"

service=forum-analytics.service
unit_dir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
mkdir -p "$unit_dir"
unit=$unit_dir/$service
if [ -L "$unit" ]; then
    echo "Refusing symlinked analytics unit file" >&2
    exit 1
fi
if [ -f "$unit" ]; then
    cp "$unit" "$release/previous.service"
fi

rollback() {
    status=$?
    trap - EXIT
    echo "Analytics deployment failed; restoring previous service" >&2
    if [ -n "$previous" ]; then
        ln -s "$previous" "$analytics/rollback-$identifier"
        mv -Tf "$analytics/rollback-$identifier" "$active"
        if [ -f "$release/previous.service" ]; then
            cp "$release/previous.service" "$unit"
        fi
        systemctl --user daemon-reload && systemctl --user restart "$service" || true
    else
        systemctl --user disable --now "$service" || true
        if [ -L "$active" ]; then unlink "$active"; fi
    fi
    exit "$status"
}
trap rollback EXIT
trap 'exit 1' HUP INT TERM
cp "$release/analytics.service" "$unit"
ln -s "releases/$identifier" "$analytics/active-$identifier"
mv -Tf "$analytics/active-$identifier" "$active"
systemctl --user daemon-reload
systemctl --user enable "$service"
systemctl --user restart "$service"
"$release/.venv/bin/python" - "$origin" <<'PY'
import json
import subprocess
import sys
import time
from urllib.request import Request, urlopen

for attempt in range(20):
    try:
        request = Request("http://127.0.0.1:3030/api/analytics", headers={"Origin": sys.argv[1]})
        with urlopen(request, timeout=2) as response:
            assert response.headers.get("Access-Control-Allow-Origin") == sys.argv[1]
            counts = json.load(response)
            assert all(type(counts[period][metric]) is int and counts[period][metric] >= 0
                       for period in ("today", "total") for metric in ("uv", "pv"))
        subprocess.run(["systemctl", "--user", "is-active", "--quiet", "forum-analytics.service"], check=True)
        break
    except (OSError, ValueError, AssertionError, subprocess.CalledProcessError):
        if attempt == 19:
            raise
        time.sleep(1)
PY
trap - EXIT HUP INT TERM
echo "Analytics service deployed and local GET/CORS health check passed"
