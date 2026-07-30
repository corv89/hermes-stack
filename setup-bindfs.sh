#!/usr/bin/env bash
#
# setup-bindfs.sh — give the host user transparent read/write access to Hermes'
# rootless-podman files.
#
# WHY: rootless podman maps the webui's in-container uid (your uid, set by
# run.py as WANTED_UID=$(id -u)) onto a *subordinate* uid on the host (e.g.
# 525287 for corv). Hermes writes its state 0600/0700, so those files are
# invisible to you on the host, and a shared group cannot help — a 0600 mode
# zeroes the ACL/group mask, so only the *owner* can ever access them.
#
# FIX: bindfs, mounted as ROOT, remaps ownership <webui-subuid> <-> <you> so
# you see and edit the files as your own; your edits map back to the webui's
# subuid so Hermes keeps ownership. The agent itself stays uid-isolated — only
# this passive mount daemon runs as root.
#
#   ~/hermes            <- hermes-data volume (config, sessions, skills, ...)
#   ~/hermes-workspace  <- shared scratch space
#
# Run with sudo (it installs bindfs, mounts, and writes /etc/fstab):
#   sudo bash setup-bindfs.sh            # apply
#   sudo bash setup-bindfs.sh --dry-run  # show what would happen, change nothing
#
# Multiple mappings must be ONE --map, colon-separated:  user-map : @group-map.
#
# IMPORTANT: a chown issued on a live bindfs mount passes THROUGH to the
# underlying source directory. We therefore always UNMOUNT first and only chown
# the bare mount-point dirs — never a mounted view — so we can never re-own the
# webui's volume root out from under it.
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The script must run as root via sudo, but everything is computed for the
# *invoking* (non-root) user — the one who runs run.py and owns the pod.
REAL_USER="${SUDO_USER:-$USER}"
if [ "$REAL_USER" = "root" ]; then
  echo "ERROR: run via sudo from your own account (sudo bash setup-bindfs.sh)," >&2
  echo "       not as root directly — I need your username to compute the mapping." >&2
  exit 1
fi
REAL_UID="$(id -u "$REAL_USER")"
REAL_GID="$(id -g "$REAL_USER")"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"

# Rootless mapping: container uid 0 -> host <you>; container uid N(>=1) ->
# host <subuid_start + N - 1>. The webui runs as container uid = your uid
# (run.py: WANTED_UID=$(id -u)), so its files land at:
SUBUID_START="$(awk -F: -v u="$REAL_USER" '$1==u{print $2; exit}' /etc/subuid)"
SUBGID_START="$(awk -F: -v u="$REAL_USER" '$1==u{print $2; exit}' /etc/subgid)"
if [ -z "$SUBUID_START" ] || [ -z "$SUBGID_START" ]; then
  echo "ERROR: no subordinate uid/gid range for $REAL_USER in /etc/subuid|subgid." >&2
  exit 1
fi
WEBUI_HOST_UID=$(( SUBUID_START + REAL_UID - 1 ))
WEBUI_HOST_GID=$(( SUBGID_START + REAL_GID - 1 ))
MAP="${WEBUI_HOST_UID}/${REAL_UID}:@${WEBUI_HOST_GID}/@${REAL_GID}"

VOLUME_NAME="hermes-data"
VOLUME_SRC="${REAL_HOME}/.local/share/containers/storage/volumes/${VOLUME_NAME}/_data"
SCRATCH_SRC="${SCRIPT_DIR}/hermes-workspace"
M_VOLUME="${REAL_HOME}/hermes"
M_SCRATCH="${REAL_HOME}/hermes-workspace"

echo "bindfs host-access config for $REAL_USER:"
echo "  webui host uid:gid   = $WEBUI_HOST_UID:$WEBUI_HOST_GID  (subuid start $SUBUID_START)"
echo "  map                  = $MAP"
echo "  volume   $VOLUME_SRC"
echo "       ->  $M_VOLUME"
echo "  scratch  $SCRATCH_SRC"
echo "       ->  $M_SCRATCH"
echo

if [ "$DRY_RUN" = 1 ]; then
  echo "[dry-run] would: install bindfs if missing; UNMOUNT existing views;"
  echo "          chown the bare mount points; set fstab entries; remount."
  exit 0
fi

command -v bindfs >/dev/null 2>&1 || dnf install -y bindfs

mkdir -p "$M_VOLUME" "$M_SCRATCH"

# 1. Unmount any existing views FIRST. A chown on a mounted bindfs passes
#    through to the source dir, so the view must be down before we chown.
for m in "$M_VOLUME" "$M_SCRATCH"; do
  if mountpoint -q "$m"; then
    umount "$m" 2>/dev/null || fusermount3 -u "$m" 2>/dev/null || true
    echo "unmounted: $m"
  fi
done

# 2. Now the mount points are bare directories — safe to chown to the user.
chown "$REAL_UID:$REAL_GID" "$M_VOLUME" "$M_SCRATCH"

# 3. (Re)mount the views.
mount_one() {
  local src="$1" dst="$2"
  if [ ! -d "$src" ]; then
    echo "WARN: source missing, skipping: $src"
    return
  fi
  bindfs --map="$MAP" -o allow_other "$src" "$dst"
  echo "mounted: $dst"
}
mount_one "$VOLUME_SRC"  "$M_VOLUME"
mount_one "$SCRATCH_SRC" "$M_SCRATCH"

# 4. Persist across reboots (idempotent, exact-field match; nofail guards boot).
cp -a /etc/fstab "/etc/fstab.bak.$(date +%s)"
awk -v a="$M_VOLUME" -v b="$M_SCRATCH" '$2!=a && $2!=b' /etc/fstab > /etc/fstab.new
echo "bindfs#${VOLUME_SRC} ${M_VOLUME} fuse map=${MAP},allow_other,nofail 0 0"  >> /etc/fstab.new
echo "bindfs#${SCRATCH_SRC} ${M_SCRATCH} fuse map=${MAP},allow_other,nofail 0 0" >> /etc/fstab.new
cat /etc/fstab.new > /etc/fstab
rm -f /etc/fstab.new
echo "fstab entries set"

echo
echo "Verify (no sudo):  ls $M_VOLUME ; head -1 $M_VOLUME/config.yaml"
