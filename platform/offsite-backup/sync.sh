#!/bin/sh
# Object store → phone, encrypted. Deleted / replaced files (expired backups) are kept in trash/<day> for 14 days:
# a mistake (or an attacker) deleting the source does not empty the off-site copy at once.
set -eu
export RCLONE_CONFIG=/tmp/none RCLONE_CACHE_DIR=/tmp/cache
export RCLONE_CONFIG_SRC_TYPE=s3 RCLONE_CONFIG_SRC_PROVIDER=Other RCLONE_CONFIG_SRC_ENDPOINT="$S3_ENDPOINT" \
  RCLONE_CONFIG_SRC_ACCESS_KEY_ID="$S3_ACCESS_KEY" RCLONE_CONFIG_SRC_SECRET_ACCESS_KEY="$S3_SECRET_KEY" \
  RCLONE_CONFIG_SRC_FORCE_PATH_STYLE=true
# the phone's key may only run sftp-server (started in the backup directory): no shell commands, no remote hashes
export RCLONE_CONFIG_PHONE_TYPE=sftp RCLONE_CONFIG_PHONE_HOST="$PHONE_HOST" RCLONE_CONFIG_PHONE_PORT="$PHONE_PORT" \
  RCLONE_CONFIG_PHONE_USER=backup RCLONE_CONFIG_PHONE_KEY_FILE=/keys/ssh_private_key \
  RCLONE_CONFIG_PHONE_KNOWN_HOSTS_FILE=/config/known_hosts RCLONE_CONFIG_PHONE_SHELL_TYPE=none \
  RCLONE_CONFIG_PHONE_DISABLE_HASHCHECK=true
RCLONE_CONFIG_VAULT_PASSWORD=$(rclone obscure "$CRYPT_PASSWORD")
RCLONE_CONFIG_VAULT_PASSWORD2=$(rclone obscure "$CRYPT_SALT")
export RCLONE_CONFIG_VAULT_TYPE=crypt RCLONE_CONFIG_VAULT_REMOTE=phone: RCLONE_CONFIG_VAULT_PASSWORD RCLONE_CONFIG_VAULT_PASSWORD2

start=$(date +%s)
rclone sync src:cnpg vault:cnpg --backup-dir "vault:trash/$(date -u +%F)" --transfers 4 --checkers 8 \
  --stats-one-line --stats-log-level NOTICE --stats 60s
# trash/ exists only after the first deletion at the source.
# The listing must not hide errors (a lost connection is a failure, not "no trash"); expired files are deleted first,
# then only directories that are really empty (--min-age with --rmdirs would try to remove directories that still
# hold younger files and fail).
dirs=$(rclone lsf vault: --dirs-only)
if printf '%s\n' "$dirs" | grep -qx 'trash/'; then
  rclone delete vault:trash --min-age 14d
  rclone rmdirs vault:trash --leave-root
fi
size=$(rclone size vault:cnpg --json)
echo "synced in $(( $(date +%s) - start ))s; off-site copy: $size"
