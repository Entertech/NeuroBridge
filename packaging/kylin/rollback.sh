#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
backup=${1:?Pass the exact installer rollback directory}
[[ $backup =~ ^/opt/neurobridge-rollback\.[A-Za-z0-9]+$ && -d $backup && ! -L $backup ]] || {
  echo "Invalid rollback directory." >&2; exit 1;
}
[[ ! -e "$backup/restored" ]] || { echo "This snapshot has already been restored." >&2; exit 1; }
systemctl stop neurobridge.service 2>/dev/null || true
if [[ -d "$backup/app" ]]; then
  [[ ! -d /opt/neurobridge ]] || mv /opt/neurobridge "$backup/failed-app"
  mv "$backup/app" /opt/neurobridge
elif [[ ! -e "$backup/had-app" && -d /opt/neurobridge ]]; then
  mv /opt/neurobridge "$backup/failed-app"
fi
for name in gateway.toml neurobridge.service; do
  if [[ $name == gateway.toml ]]; then target=/etc/neurobridge/gateway.toml; else target=/etc/systemd/system/neurobridge.service; fi
  if [[ -e "$backup/$name" ]]; then
    cp -a "$backup/$name" "$target"
  elif [[ -e "$target" ]]; then
    mv "$target" "$backup/failed-$name"
  fi
done
systemctl daemon-reload
if [[ -e "$backup/was-enabled" ]]; then systemctl enable neurobridge.service; else systemctl disable neurobridge.service 2>/dev/null || true; fi
if [[ -e "$backup/was-active" ]]; then systemctl start neurobridge.service; fi
touch "$backup/restored"
echo "Rollback completed; replaced files retained in $backup, recordings untouched."
