#!/usr/bin/env bash
# Inspect currently connected USB serial devices on Galaxy Kylin. An explicit
# plug-cycle mode remains available when physical enumeration timing must be
# observed. No serial payload or recording is collected.
set -u -o pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
default_output_dir="$root_dir/.runtime/diagnostics"

usage() {
  cat <<'EOF'
Usage: sudo ./linux/diagnose-kylin-usb-serial.sh [options]

Options:
  --plug-cycle        Observe an explicit unplug/plug cycle instead of checking
                      devices that are already connected
  --timeout SECONDS   Plug-cycle wait, 5-600 seconds (default: 60)
  --output-dir DIR    Absolute directory below project .runtime/diagnostics
                      (default: project .runtime/diagnostics)
  --no-prompt         Plug-cycle compatibility mode without waiting for Enter
  -h, --help          Show this help

Default use (no unplug required):
  1. Keep the headset USB connected.
  2. Run this command once.
  3. Existing ttyACM/ttyUSB/by-id candidates are reported immediately; the
     gateway later confirms the target by its fixed handshake.

Optional plug-cycle use:
  1. Unplug the headset USB.
  2. Run this command with --plug-cycle and press Enter when prompted.
  3. Plug the headset into the computer before the timeout expires.

Results:
  exit 0: at least one current or newly inserted serial candidate was found
  exit 2: no current TTY, or plug-cycle USB appeared without a new TTY
  exit 3: plug-cycle mode saw no new USB device before timeout
  exit 130: interrupted; logs collected up to the interruption are retained

The command keeps a timestamped directory, a .tar.gz archive, and a SHA-256
file. It records system/USB/TTY/service metadata and process logs, but never
reads a serial port or records handshake, EEG, HR, or other serial payloads.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

timeout_text=60
output_dir=$default_output_dir
prompt=true
detection_mode=current
while [[ $# -gt 0 ]]; do
  case $1 in
    --plug-cycle)
      detection_mode=plug_cycle
      shift
      ;;
    --timeout)
      [[ $# -ge 2 ]] || fail "--timeout requires a value"
      timeout_text=$2
      shift 2
      ;;
    --output-dir)
      [[ $# -ge 2 ]] || fail "--output-dir requires a value"
      output_dir=$2
      shift 2
      ;;
    --no-prompt)
      detection_mode=plug_cycle
      prompt=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

[[ $timeout_text =~ ^[0-9]+$ ]] || fail "--timeout must be an integer"
timeout_seconds=$((10#$timeout_text))
(( timeout_seconds >= 5 && timeout_seconds <= 600 )) || fail "--timeout must be between 5 and 600 seconds"
[[ $output_dir == /* ]] || fail "--output-dir must be an absolute path"
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run with sudo so kernel, udev, and service logs can be collected."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable"
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This helper is only for Galaxy Kylin; detected ID=${ID:-unknown}."

for required_command in date find sort comm tar sha256sum realpath; do
  command -v "$required_command" >/dev/null 2>&1 || fail "Required command is unavailable: $required_command"
done

umask 077
output_dir=$(realpath -m -- "$output_dir")
case $output_dir in
  "$default_output_dir"|"$default_output_dir"/*) ;;
  *) fail "Output must stay under the ignored project directory: $default_output_dir" ;;
esac
artifact_uid=${SUDO_UID:-0}
artifact_gid=${SUDO_GID:-0}
[[ $artifact_uid =~ ^[0-9]+$ ]] || artifact_uid=0
[[ $artifact_gid =~ ^[0-9]+$ ]] || artifact_gid=0
install -d -o "$artifact_uid" -g "$artifact_gid" -m 0750 \
  "$root_dir/.runtime" "$default_output_dir" "$output_dir" \
  || fail "Could not create project diagnostic directory: $output_dir"
[[ -d $output_dir && -w $output_dir ]] || fail "Output directory must exist and be writable: $output_dir"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
session_name="neurobridge-usb-serial-diagnosis-${stamp}-$$"
session_dir="$output_dir/$session_name"
archive_path="$output_dir/$session_name.tar.gz"
checksum_path="$archive_path.sha256"
mkdir -m 0700 -- "$session_dir" || fail "Could not create log directory: $session_dir"
console_log="$session_dir/console.log"
summary_file="$session_dir/result-summary.txt"
monitor_pids=()
finalized=false
result_status=running
result_code=1
started_at=$(date --iso-8601=seconds)
result_usb_file=
result_tty_file=

log() {
  local message=$*
  printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$message" | tee -a "$console_log"
}

capture() {
  local name=$1
  shift
  {
    printf 'command:'
    printf ' %q' "$@"
    printf '\nstartedAt: %s\n\n' "$(date --iso-8601=seconds)"
    "$@"
    local command_result=$?
    printf '\nexitCode: %s\n' "$command_result"
  } >"$session_dir/$name.log" 2>&1 || true
}

capture_shell() {
  local name=$1
  local command_text=$2
  capture "$name" bash -o pipefail -c "$command_text"
}

list_usb_nodes() {
  local device_path vendor product serial_value manufacturer product_name
  for device_path in /sys/bus/usb/devices/*; do
    [[ -r $device_path/idVendor && -r $device_path/idProduct ]] || continue
    vendor=$(<"$device_path/idVendor")
    product=$(<"$device_path/idProduct")
    serial_value=none
    manufacturer=unknown
    product_name=unknown
    [[ -r $device_path/serial ]] && serial_value=$(tr -cd '[:print:]' <"$device_path/serial")
    [[ -r $device_path/manufacturer ]] && manufacturer=$(tr -cd '[:print:]' <"$device_path/manufacturer")
    [[ -r $device_path/product ]] && product_name=$(tr -cd '[:print:]' <"$device_path/product")
    printf '%s vid=%s pid=%s manufacturer=%s product=%s serial=%s\n' \
      "${device_path##*/}" "$vendor" "$product" "$manufacturer" "$product_name" "$serial_value"
  done | sort -u
}

list_tty_paths() {
  local tty_path
  shopt -s nullglob
  for tty_path in /dev/ttyACM* /dev/ttyUSB* /dev/serial/by-id/*; do
    [[ -e $tty_path || -L $tty_path ]] && printf '%s\n' "$tty_path"
  done
  shopt -u nullglob
}

write_usb_interface_details() {
  local destination=$1 interface_path interface_number interface_class interface_subclass interface_protocol
  local bound_driver modalias
  : >"$destination"
  for interface_path in /sys/bus/usb/devices/*:*; do
    [[ -d $interface_path ]] || continue
    interface_number=unknown
    interface_class=unknown
    interface_subclass=unknown
    interface_protocol=unknown
    bound_driver=none
    modalias=unknown
    [[ -r $interface_path/bInterfaceNumber ]] && interface_number=$(<"$interface_path/bInterfaceNumber")
    [[ -r $interface_path/bInterfaceClass ]] && interface_class=$(<"$interface_path/bInterfaceClass")
    [[ -r $interface_path/bInterfaceSubClass ]] && interface_subclass=$(<"$interface_path/bInterfaceSubClass")
    [[ -r $interface_path/bInterfaceProtocol ]] && interface_protocol=$(<"$interface_path/bInterfaceProtocol")
    [[ -L $interface_path/driver ]] && bound_driver=$(basename "$(readlink -f "$interface_path/driver")")
    [[ -r $interface_path/modalias ]] && modalias=$(<"$interface_path/modalias")
    printf 'interface=%s number=%s class=%s subclass=%s protocol=%s driver=%s modalias=%s\n' \
      "${interface_path##*/}" "$interface_number" "$interface_class" "$interface_subclass" \
      "$interface_protocol" "$bound_driver" "$modalias" >>"$destination"
  done
}

write_tty_details() {
  local destination=$1 tty_path resolved_path
  : >"$destination"
  while IFS= read -r tty_path; do
    resolved_path=$(readlink -f -- "$tty_path" 2>/dev/null || true)
    printf 'path=%s resolvedPath=%s ' "$tty_path" "${resolved_path:-unknown}" >>"$destination"
    stat -Lc 'mode=%a owner=%U group=%G majorMinor=%t:%T' "$tty_path" >>"$destination" 2>&1 || true
    if command -v udevadm >/dev/null 2>&1; then
      udevadm info --query=property --name="$tty_path" 2>&1 \
        | grep -E '^(DEVPATH|ID_BUS|ID_VENDOR_ID|ID_MODEL_ID|ID_SERIAL|ID_SERIAL_SHORT|ID_USB_DRIVER|ID_USB_INTERFACE_NUM|ID_PATH|DRIVER)=' \
        >>"$destination" || true
    fi
    printf '\n' >>"$destination"
  done < <(list_tty_paths | sort -u)
}

capture_snapshot() {
  local prefix=$1
  capture "$prefix-date" date --iso-8601=seconds
  if command -v lsusb >/dev/null 2>&1; then
    if lsusb -nn >/dev/null 2>&1; then
      capture "$prefix-lsusb" lsusb -nn
    else
      capture "$prefix-lsusb" lsusb
      printf '\nnote: lsusb -nn is unsupported; captured portable lsusb output instead\n' \
        >>"$session_dir/$prefix-lsusb.log"
    fi
    capture "$prefix-lsusb-tree" lsusb -t
  else
    printf 'lsusb is unavailable\n' >"$session_dir/$prefix-lsusb.log"
  fi
  list_usb_nodes >"$session_dir/$prefix-usb-sysfs.log" 2>&1 || true
  write_usb_interface_details "$session_dir/$prefix-usb-interfaces.log"
  list_tty_paths | sort -u >"$session_dir/$prefix-tty-paths.log"
  write_tty_details "$session_dir/$prefix-tty-details.log"
  capture_shell "$prefix-loaded-serial-drivers" \
    'lsmod 2>/dev/null | grep -E "^(cdc_acm|cp210x|ch341|ftdi_sio|pl2303|usbserial)[[:space:]]" || true'
}

start_monitor() {
  local name=$1
  shift
  if command -v stdbuf >/dev/null 2>&1; then
    stdbuf -oL -eL "$@" >"$session_dir/$name.log" 2>&1 &
  else
    "$@" >"$session_dir/$name.log" 2>&1 &
  fi
  monitor_pids+=("$!")
  printf '%s pid=%s command=' "$name" "$!" >>"$session_dir/monitor-processes.log"
  printf ' %q' "$@" >>"$session_dir/monitor-processes.log"
  printf '\n' >>"$session_dir/monitor-processes.log"
}

stop_monitors() {
  local monitor_pid attempt
  for monitor_pid in "${monitor_pids[@]}"; do
    kill -TERM "$monitor_pid" 2>/dev/null || true
  done
  for attempt in {1..20}; do
    local any_running=false
    for monitor_pid in "${monitor_pids[@]}"; do
      if kill -0 "$monitor_pid" 2>/dev/null; then
        any_running=true
      fi
    done
    [[ $any_running == false ]] && break
    sleep 0.1
  done
  for monitor_pid in "${monitor_pids[@]}"; do
    if kill -0 "$monitor_pid" 2>/dev/null; then
      kill -KILL "$monitor_pid" 2>/dev/null || true
    fi
    wait "$monitor_pid" 2>/dev/null || true
  done
  monitor_pids=()
}

finalize() {
  local -a artifact_paths
  [[ $finalized == false ]] || return
  finalized=true
  trap '' INT TERM
  stop_monitors
  capture_snapshot after
  if command -v journalctl >/dev/null 2>&1; then
    capture kernel-journal-window journalctl -k -b --since "$started_at" --no-pager -o short-iso-precise
    capture gateway-journal-window journalctl -u neurobridge.service -b --since "$started_at" --no-pager -o short-iso-precise
  fi
  if command -v systemctl >/dev/null 2>&1; then
    capture usbguard-status systemctl status usbguard.service --no-pager -l
    capture udev-service-status systemctl status systemd-udevd.service --no-pager -l
  fi
  {
    printf 'status=%s\n' "$result_status"
    printf 'exitCode=%s\n' "$result_code"
    printf 'startedAt=%s\n' "$started_at"
    printf 'finishedAt=%s\n' "$(date --iso-8601=seconds)"
    printf 'detectionMode=%s\n' "$detection_mode"
    if [[ $detection_mode == plug_cycle ]]; then
      printf 'timeoutSeconds=%s\n' "$timeout_seconds"
    else
      printf 'timeoutSeconds=not_applicable\n'
    fi
    printf 'artifactOwnerUid=%s\n' "$artifact_uid"
    printf 'artifactOwnerGid=%s\n' "$artifact_gid"
    printf 'payloadCollected=false\n'
    printf 'usbDevices:\n'
    [[ -n $result_usb_file ]] && sed 's/^/  /' "$result_usb_file" 2>/dev/null || true
    printf 'ttyPaths:\n'
    [[ -n $result_tty_file ]] && sed 's/^/  /' "$result_tty_file" 2>/dev/null || true
  } >"$summary_file"
  chown -R "$artifact_uid:$artifact_gid" "$session_dir" 2>>"$console_log" || \
    log "WARNING：无法将日志目录交给执行 sudo 的用户，目录仍已保留。"
  if ! tar -C "$output_dir" -czf "$archive_path" "$session_name" 2>>"$console_log"; then
    log "WARNING：日志压缩失败，请保留并使用上述日志目录。"
  fi
  if [[ -f $archive_path ]]; then
    if ! (
      cd "$output_dir" || exit 1
      sha256sum "${archive_path##*/}" >"${checksum_path##*/}"
    ); then
      log "WARNING：压缩包已保存，但 SHA-256 校验文件生成失败。"
    fi
    artifact_paths=("$archive_path")
    [[ -f $checksum_path ]] && artifact_paths+=("$checksum_path")
    chmod 0600 "${artifact_paths[@]}" 2>/dev/null || true
    chown "$artifact_uid:$artifact_gid" "${artifact_paths[@]}" 2>/dev/null || \
      log "WARNING：无法将压缩包交给执行 sudo 的用户。"
  fi
  log "日志目录：$session_dir"
  [[ -f $archive_path ]] && log "日志压缩包：$archive_path"
  [[ -f $checksum_path ]] && log "校验文件：$checksum_path"
}

handle_signal() {
  result_status=interrupted
  result_code=130
  log "操作已中断；正在保存已经产生的过程日志。"
  finalize
  exit 130
}

trap handle_signal INT TERM
trap 'stop_monitors' EXIT

cat >"$session_dir/README.txt" <<'EOF'
NeuroBridge USB serial diagnosis

Collected: current or before/after USB and TTY snapshots, kernel events, udev
events, gateway service journal, loaded serial drivers, result, and console log.

Not collected: serial port contents, handshake bytes, EEG/HR data, recordings,
gateway configuration contents, process environments, passwords, or tokens.

USB serial numbers and host/device metadata may appear. Inspect the archive and
share it only through the approved internal project channel.
EOF

log "NeuroBridge USB/串口一键识别开始。"
log "全过程日志将保存到：$session_dir"
if [[ $detection_mode == current ]]; then
  log "检测模式：直接检查当前已连接设备，不需要拔插 USB。"
  capture_snapshot current
  result_usb_file="$session_dir/current-usb-sysfs.log"
  result_tty_file="$session_dir/current-tty-paths.log"
  if [[ -s $result_tty_file ]]; then
    result_status=current_tty_detected
    result_code=0
    log "成功：检测到当前串口候选；目标设备将在网关启动后通过固定握手确认："
    sed 's/^/  /' "$result_tty_file" | tee -a "$console_log"
    log "下一步执行：sudo ./linux/setup-kylin-serial.sh"
  else
    result_status=current_tty_not_detected
    result_code=2
    log "未检测到当前 ttyACM/ttyUSB/by-id 串口节点；已保存当前 USB、接口、驱动和内核快照。"
    log "先检查接线、供电和 USB 数据线；如需观察插入瞬间，再执行：sudo ./linux/diagnose-kylin-usb-serial.sh --plug-cycle --timeout ${timeout_seconds}"
  fi
  finalize
  exit "$result_code"
fi

log "检测模式：USB 拔插过程监控。"
if [[ $prompt == true ]]; then
  log "操作提示：先拔下耳机 USB；确认已拔下后按 Enter。"
  IFS= read -r _unused_input || true
else
  log "非交互模式：立即记录当前状态为插入前基线；现在应保持耳机 USB 未连接。"
fi

capture_snapshot before
cp -- "$session_dir/before-usb-sysfs.log" "$session_dir/baseline-usb-nodes.log"
cp -- "$session_dir/before-tty-paths.log" "$session_dir/baseline-tty-paths.log"
: >"$session_dir/new-usb-nodes.log"
: >"$session_dir/new-tty-paths.log"
result_usb_file="$session_dir/new-usb-nodes.log"
result_tty_file="$session_dir/new-tty-paths.log"

if command -v journalctl >/dev/null 2>&1; then
  start_monitor kernel-journal-follow journalctl -k -f --since now --no-pager -o short-iso-precise
  start_monitor gateway-journal-follow journalctl -u neurobridge.service -f --since now --no-pager -o short-iso-precise
fi
command -v udevadm >/dev/null 2>&1 && start_monitor udev-monitor udevadm monitor --kernel --udev --property
command -v dmesg >/dev/null 2>&1 && start_monitor dmesg-follow dmesg --follow --time-format iso

log "请现在插入耳机 USB；脚本将在 ${timeout_seconds} 秒内等待系统生成串口节点。"
wait_started_epoch=$(date +%s)
last_reported=-1
usb_seen=false
tty_seen=false
while true; do
  list_usb_nodes >"$session_dir/current-usb-nodes.log" 2>&1 || true
  list_tty_paths | sort -u >"$session_dir/current-tty-paths.log"
  comm -13 "$session_dir/baseline-usb-nodes.log" "$session_dir/current-usb-nodes.log" \
    >"$session_dir/new-usb-nodes.log" || true
  comm -13 "$session_dir/baseline-tty-paths.log" "$session_dir/current-tty-paths.log" \
    >"$session_dir/new-tty-paths.log" || true
  [[ -s $session_dir/new-usb-nodes.log ]] && usb_seen=true
  if [[ -s $session_dir/new-tty-paths.log ]]; then
    tty_seen=true
    break
  fi
  now_epoch=$(date +%s)
  elapsed=$((now_epoch - wait_started_epoch))
  (( elapsed >= timeout_seconds )) && break
  remaining=$((timeout_seconds - elapsed))
  if (( elapsed == 0 || elapsed / 5 > last_reported / 5 )); then
    log "等待中：剩余 ${remaining} 秒，usbDetected=$usb_seen，ttyDetected=$tty_seen"
    last_reported=$elapsed
  fi
  sleep 1
done

if [[ $tty_seen == true ]]; then
  result_status=tty_detected
  result_code=0
  log "成功：检测到新的串口节点："
  sed 's/^/  /' "$session_dir/new-tty-paths.log" | tee -a "$console_log"
  log "下一步执行：sudo ./linux/setup-kylin-serial.sh"
elif [[ $usb_seen == true ]]; then
  result_status=usb_detected_tty_timeout
  result_code=2
  log "超时：已经检测到新 USB 设备，但 ${timeout_seconds} 秒内没有生成 ttyACM/ttyUSB/by-id 串口节点。"
  log "优先查看 after-lsusb-tree.log、after-usb-interfaces.log、kernel-journal-window.log、udev-monitor.log 和 dmesg-follow.log，确认接口类型及驱动绑定。"
else
  result_status=usb_detection_timeout
  result_code=3
  log "超时：${timeout_seconds} 秒内没有检测到新的 USB 设备，也没有生成新的串口节点。"
  log "优先检查是否接到 USB DEVICE/PC 上行接口、USB 线是否支持数据、设备供电、物理 USB 口和接触状态。"
fi

finalize
exit "$result_code"
