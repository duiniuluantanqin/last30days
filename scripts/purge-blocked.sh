#!/bin/sh
# Delete blocked/expired generated pages named by the private numeric ID manifest.
set -eu

root=$(cd "$1" && pwd -P)
manifest=$2
test -d "$root/releases"

# Validate the whole manifest before deleting anything.
while IFS= read -r thread_id || [ -n "$thread_id" ]; do
    case "$thread_id" in
        ''|*[!0-9]*) echo 'Invalid removed thread ID' >&2; exit 1 ;;
    esac
done < "$manifest"

# find does not follow symlink directories. Only release-*/posts/<id>.html
# files directly within this deployment's releases tree are eligible.
while IFS= read -r thread_id || [ -n "$thread_id" ]; do
    find "$root/releases" -mindepth 3 -maxdepth 3 -type f \
        -path "$root/releases/release-*/posts/$thread_id.html" -delete
done < "$manifest"
