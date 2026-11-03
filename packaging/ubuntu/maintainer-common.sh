# Included in each maintainer script: preinst must work before unpacking.
set -eu
units='neurobridge.service neurobridge-dhcp.service neurobridge-wired-address.service'
state=/var/lib/neurobridge-package
systemd_running() { [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; }
check_overrides() {
    for unit in $units; do
        if [ -e "/etc/systemd/system/$unit" ] || [ -L "/etc/systemd/system/$unit" ] || [ -d "/etc/systemd/system/$unit.d" ]; then
            echo "Cannot install: local unit override /etc/systemd/system/$unit exists. Back up configuration, stop the source deployment and migrate its units before installing the Ubuntu DEB." >&2
            exit 1
        fi
    done
}
