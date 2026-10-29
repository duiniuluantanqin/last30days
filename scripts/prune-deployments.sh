#!/bin/sh
# Run only after the new site, state and blocked-page purge have succeeded.
set -eu

identifier=$2
case "$identifier" in
    release-*) number=${identifier#release-} ;;
    *) echo 'Invalid release ID' >&2; exit 1 ;;
esac
case "$number" in
    ''|*[!0-9]*) echo 'Invalid release ID' >&2; exit 1 ;;
esac

root=$(cd "$1" && pwd -P)
for directory in releases incoming; do
    test -d "$root/$directory"
    test ! -L "$root/$directory"
done
test -L "$root/current"
current=$(readlink -f "$root/current")
test "$current" = "$root/releases/$identifier"
test -f "$current/index.html"

# Numeric publication IDs define version order; never follow symlink directories.
find "$root/releases" -mindepth 1 -maxdepth 1 -type d -name 'release-*' -printf '%f\n' |
    sort -t- -k2,2nr |
    {
        kept=0
        while IFS= read -r name; do
            number=${name#release-}
            case "$number" in ''|*[!0-9]*) continue ;; esac
            kept=$((kept + 1))
            if [ "$kept" -gt 3 ] && [ "$name" != "$identifier" ]; then
                rm -rf -- "$root/releases/$name"
            fi
        done
    }

# Remove this successful upload even when fresh; retain recent failed uploads.
# Restrict deletion to known artifacts directly inside incoming, never state.
find "$root/incoming" -mindepth 1 -maxdepth 1 -type f \
    \( -name "$identifier.*" -o ! -newermt '24 hours ago' \) -print |
    while IFS= read -r path; do
        name=${path##*/}
        case "$name" in
            release-*.tar.gz) upload=${name%.tar.gz} ;;
            release-*.backup.sqlite3) upload=${name%.backup.sqlite3} ;;
            release-*.sqlite3) upload=${name%.sqlite3} ;;
            release-*.removed.txt) upload=${name%.removed.txt} ;;
            release-*.purge.sh) upload=${name%.purge.sh} ;;
            release-*.prune.sh) upload=${name%.prune.sh} ;;
            *) continue ;;
        esac
        number=${upload#release-}
        case "$number" in ''|*[!0-9]*) continue ;; esac
        rm -f -- "$path"
    done
