#!/usr/bin/env bash
set -euo pipefail

# Mount a NAS SMB/CIFS share for manga storage and generate a local
# docker-compose.override.yml that keeps large manga files on the NAS while
# leaving Komga/pipeline SQLite state on the Ubuntu host.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DATA_DIR="${PROJECT_DIR}/data"
STORAGE_ROOT=""

DEFAULT_NAS_IP="192.168.1.50"
DEFAULT_SHARE_NAME="Entertainment/Comic"
DEFAULT_MOUNT_POINT="/mnt/kobo-nas"
DEFAULT_CREDENTIAL_FILE="/etc/samba/credentials/kobo-nas.cred"

prompt_default() {
  local prompt="$1"
  local default="$2"
  local value
  read -r -p "${prompt} [${default}]: " value
  printf '%s' "${value:-$default}"
}

require_sudo() {
  if ! sudo -v; then
    echo "sudo authentication failed."
    exit 1
  fi
}

is_yes_default() {
  local value="${1,,}"
  [[ -z "$value" || "$value" == "y" || "$value" == "yes" ]]
}

ensure_line_absent() {
  local file="$1"
  local pattern="$2"
  if sudo test -f "$file"; then
    sudo sed -i "\|${pattern}|d" "$file"
  fi
}

copy_dir_if_present() {
  local name="$1"
  local src="${LOCAL_DATA_DIR}/${name}/"
  local dst="${STORAGE_ROOT}/${name}/"

  if [[ -d "$src" ]]; then
    echo "Migrating ${src} -> ${dst}"
    sudo -u "#${PUID}" rsync -a --ignore-existing "$src" "$dst"
  fi
}

stop_existing_mount() {
  local automount_unit
  local mount_unit

  automount_unit="$(systemd-escape -p --suffix=automount "$MOUNT_POINT" 2>/dev/null || true)"
  mount_unit="$(systemd-escape -p --suffix=mount "$MOUNT_POINT" 2>/dev/null || true)"

  echo "Stopping Docker Compose services before remount..."
  (cd "$PROJECT_DIR" && docker compose down) || true

  if [[ -n "$automount_unit" ]]; then
    sudo systemctl stop "$automount_unit" 2>/dev/null || true
  fi
  if [[ -n "$mount_unit" ]]; then
    sudo systemctl stop "$mount_unit" 2>/dev/null || true
  fi

  if mountpoint -q "$MOUNT_POINT"; then
    echo "Unmounting existing mount: ${MOUNT_POINT}"
    sudo umount "$MOUNT_POINT" 2>/dev/null || sudo umount -l "$MOUNT_POINT"
  fi
}

echo "This script will:"
echo "  1. Install cifs-utils and rsync if needed."
echo "  2. Mount a NAS SMB share on this Ubuntu host."
echo "  3. Put inbox/archive_cbz/kepub_ready/komga-library on the NAS."
echo "  4. Keep Komga config and pipeline state under ${LOCAL_DATA_DIR}."
echo "  5. Generate docker-compose.override.yml for this machine."
echo

NAS_IP="$(prompt_default "NAS IP" "$DEFAULT_NAS_IP")"
SHARE_NAME="$(prompt_default "NAS SMB share name or share/subdirectory" "$DEFAULT_SHARE_NAME")"
MOUNT_POINT="$(prompt_default "Local mount point" "$DEFAULT_MOUNT_POINT")"
SMB_VERSION="$(prompt_default "SMB version" "3.0")"
PUID="$(id -u)"
PGID="$(id -g)"

read -r -p "NAS username: " NAS_USERNAME
read -r -s -p "NAS password: " NAS_PASSWORD
echo
read -r -p "NAS domain/workgroup, leave empty if not needed: " NAS_DOMAIN

if [[ -z "$NAS_USERNAME" || -z "$NAS_PASSWORD" ]]; then
  echo "NAS username and password are required."
  exit 1
fi

require_sudo

echo "Installing required packages..."
sudo apt-get update
sudo apt-get install -y cifs-utils rsync

echo "Writing credential file: ${DEFAULT_CREDENTIAL_FILE}"
sudo install -d -m 0700 /etc/samba/credentials
sudo tee "$DEFAULT_CREDENTIAL_FILE" >/dev/null <<EOF
username=${NAS_USERNAME}
password=${NAS_PASSWORD}
EOF
if [[ -n "$NAS_DOMAIN" ]]; then
  echo "domain=${NAS_DOMAIN}" | sudo tee -a "$DEFAULT_CREDENTIAL_FILE" >/dev/null
fi
sudo chmod 0600 "$DEFAULT_CREDENTIAL_FILE"

echo "Creating mount point: ${MOUNT_POINT}"
sudo mkdir -p "$MOUNT_POINT"

read -r -p "Stop Docker Compose and recreate existing NAS mount? [Y/n]: " DO_REMOUNT
if is_yes_default "$DO_REMOUNT"; then
  stop_existing_mount
fi

SHARE_ROOT="${SHARE_NAME%%/*}"
SHARE_SUBPATH=""
if [[ "$SHARE_NAME" == */* ]]; then
  SHARE_SUBPATH="${SHARE_NAME#*/}"
fi
FSTAB_SOURCE="//${NAS_IP}/${SHARE_ROOT}"
FSTAB_OPTIONS="rw,credentials=${DEFAULT_CREDENTIAL_FILE},uid=${PUID},gid=${PGID},iocharset=utf8,vers=${SMB_VERSION},nofail,_netdev,x-systemd.automount,file_mode=0664,dir_mode=0775"
FSTAB_LINE="${FSTAB_SOURCE} ${MOUNT_POINT} cifs ${FSTAB_OPTIONS} 0 0"
STORAGE_ROOT="$MOUNT_POINT"
if [[ -n "$SHARE_SUBPATH" ]]; then
  STORAGE_ROOT="${MOUNT_POINT}/${SHARE_SUBPATH}"
fi

echo "Updating /etc/fstab..."
ensure_line_absent /etc/fstab "$FSTAB_SOURCE"
ensure_line_absent /etc/fstab "$MOUNT_POINT"
echo "$FSTAB_LINE" | sudo tee -a /etc/fstab >/dev/null

echo "Mounting NAS share..."
sudo systemctl daemon-reload
sudo mount "$MOUNT_POINT"
sudo mount -o remount,rw "$MOUNT_POINT"

echo "Verifying NAS mount is writable..."
sudo mkdir -p "$STORAGE_ROOT"
WRITE_TEST="${STORAGE_ROOT}/.manga-pipeline-write-test"
if ! printf 'ok\n' >"$WRITE_TEST"; then
  echo "NAS mount is not writable from this Ubuntu host: ${MOUNT_POINT}"
  echo "Check the fstab line and NAS SMB write permissions, then rerun this script."
  exit 1
fi
rm -f "$WRITE_TEST"

echo "Creating NAS storage directories..."
for dir in inbox processing archive_cbz kepub_ready komga-library manual-review; do
  mkdir -p "${STORAGE_ROOT}/${dir}"
done

echo "Creating local state/config directories..."
for dir in pipeline-state logs komga-config; do
  mkdir -p "${LOCAL_DATA_DIR}/${dir}"
done

read -r -p "Copy existing large manga directories from ${LOCAL_DATA_DIR} to NAS? [y/N]: " DO_MIGRATE
if [[ "${DO_MIGRATE,,}" == "y" || "${DO_MIGRATE,,}" == "yes" ]]; then
  copy_dir_if_present inbox
  copy_dir_if_present processing
  copy_dir_if_present archive_cbz
  copy_dir_if_present kepub_ready
  copy_dir_if_present komga-library
  copy_dir_if_present manual-review
fi

echo "Writing docker-compose.override.yml..."
cat >"${PROJECT_DIR}/docker-compose.override.yml" <<EOF
services:
  manga-pipeline:
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ${STORAGE_ROOT}/inbox:/data/inbox
      - ${STORAGE_ROOT}/processing:/data/processing
      - ${STORAGE_ROOT}/archive_cbz:/data/archive_cbz
      - ${STORAGE_ROOT}/kepub_ready:/data/kepub_ready
      - ${STORAGE_ROOT}/komga-library:/data/komga-library
      - ./data/pipeline-state:/data/state
      - ${STORAGE_ROOT}/manual-review:/data/manual-review
      - ./data/logs:/data/logs
    user: "${PUID}:${PGID}"

  komga:
    volumes:
      - ./data/komga-config:/config
      - ${STORAGE_ROOT}/komga-library:/data
    user: "${PUID}:${PGID}"
EOF

echo
read -r -p "Restart Docker Compose services now? [y/N]: " DO_RESTART
if [[ "${DO_RESTART,,}" == "y" || "${DO_RESTART,,}" == "yes" ]]; then
  echo "Restarting Docker Compose services..."
  cd "$PROJECT_DIR"
  docker compose down
  docker compose up -d
fi

echo
echo "Done."
echo
echo "Next commands:"
echo "  cd ${PROJECT_DIR}"
echo "  docker compose ps"
echo
echo "Verify:"
echo "  find ${STORAGE_ROOT} -maxdepth 1 -type d -printf '%p\\n'"
echo "  docker compose ps"
