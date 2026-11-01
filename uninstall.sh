#!/system/bin/sh
# 卸载: 停守护 / 停 WebUI / 还原出厂 / 清状态目录
MODDIR=${0%/*}
[ -d "$MODDIR" ] || MODDIR=/data/adb/modules/kpad_cpuctl
STATE=/data/adb/kpad_cpuctl
BB=/data/adb/ksu/bin/busybox
[ -x "$BB" ] || BB=/data/adb/magisk/busybox
[ -x "$BB" ] || BB=busybox

# 1) 停守护
echo 0 > "$STATE/watch_enabled" 2>/dev/null
p=$(cat "$STATE/watch.lock/pid" 2>/dev/null)
[ -n "$p" ] && kill "$p" 2>/dev/null
sleep 1
[ -n "$p" ] && kill -9 "$p" 2>/dev/null

# 2) 停 WebUI
p=$(cat "$STATE/web.pid" 2>/dev/null)
[ -n "$p" ] && kill "$p" 2>/dev/null
ps -A -o PID,ARGS 2>/dev/null | grep -F "httpd -f -p 127.0.0.1:" | grep -v grep |
while read -r pid line; do kill -9 "$pid" 2>/dev/null; done

# 3) 还原出厂频率(有快照按快照, 没有就回最小值/天花板)
if [ -x "$MODDIR/bin/cpuctl" ]; then
    sh "$MODDIR/bin/cpuctl" restore >/dev/null 2>&1
fi

# 4) 清状态目录(保留备份, 方便重装后还原)
rm -rf "$STATE/watch.lock" "$STATE/web.pid" "$STATE/web_port" "$STATE/throttled" \
       "$STATE/boot_id" "$STATE"/ceil.p* "$STATE"/tz_*.list "$STATE"/gpu_cdev 2>/dev/null

echo "kpad_cpuctl 已卸载, 频率已尝试还原出厂"
