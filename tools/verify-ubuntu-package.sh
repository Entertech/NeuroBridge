#!/usr/bin/env bash
# Install only in a disposable Ubuntu container. Never run on an operator host.
set -euo pipefail
[[ -f /.dockerenv ]] || { echo 'Lifecycle verification requires a disposable Docker container.' >&2; exit 1; }
[[ $EUID == 0 && $# == 1 ]] || { echo 'Usage (container root): verify-ubuntu-package.sh /absolute/package.deb' >&2; exit 1; }
package=$(realpath "$1")
[[ -f $package && $(dpkg-deb -f "$package" Package) == neurobridge-ubuntu ]]
if dpkg-query -W -f='${Status}' neurobridge-ubuntu 2>/dev/null | grep -q 'install ok installed'; then
    echo 'Refusing to test over an existing installation.' >&2; exit 1
fi
dpkg -i "$package"
runuser -u neurobridge -- /opt/neurobridge/runtime/bin/python -c 'import bleak, serial, websockets, dbus_fast; from neurobridge.configuration.runtime import load_runtime_config; c=load_runtime_config("/etc/neurobridge/gateway.toml"); assert c.profile=="ubuntu_headband_wired"; assert not c.local_ui.enabled; assert c.algorithm.command==("/opt/neurobridge/runtime/bin/neurobridge_affective_bridge",); print("installed runtime imports/profile: passed")'
printf '%s\n' '{"eegRawBase64":"","hrRawBase64":""}' | runuser -u neurobridge -- /opt/neurobridge/runtime/bin/neurobridge_affective_bridge | /usr/bin/python3.12 -c 'import json,sys; d=json.load(sys.stdin); assert d=={"algorithm": {}}; print("installed bridge smoke: passed")'
grep -q '/runtime/bin/neurobridge ' /usr/lib/systemd/system/neurobridge.service
[[ $(stat -c '%a %U %G' /etc/neurobridge/gateway.toml) == '640 root neurobridge' ]]
printf '\n# lifecycle verification config marker\n' >> /etc/neurobridge/gateway.toml
printf 'synthetic retention marker; no human data\n' > /var/lib/neurobridge/recordings/package-test-marker
printf 'synthetic log marker\n' > /var/log/neurobridge/package-test-marker
config_sha=$(sha256sum /etc/neurobridge/gateway.toml)
dpkg -i "$package"
[[ $(sha256sum /etc/neurobridge/gateway.toml) == "$config_sha" ]]
dpkg --remove neurobridge-ubuntu
[[ ! -e /opt/neurobridge/runtime/bin/neurobridge ]]
dpkg --purge neurobridge-ubuntu
[[ $(sha256sum /etc/neurobridge/gateway.toml) == "$config_sha" ]]
[[ -f /var/lib/neurobridge/recordings/package-test-marker && -f /var/log/neurobridge/package-test-marker ]]
echo 'PASS: install, runtime imports/bridge, same-version reinstall, remove/purge and config/data retention; real systemd/hardware acceptance pending.'
