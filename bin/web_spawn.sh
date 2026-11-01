#!/system/bin/sh
# 独立启动 httpd（busybox 提供 httpd，本机 /data/adb/ksu/bin/busybox 自带该 applet）。
# 必须在独立脚本里启动: 若在 cpuctl 的函数体内直接写后台启动(&),
# mksh 会把整个调用上下文 fork 成子 shell, 导致守护进程自我复制。
MODDIR=/data/adb/modules/kpad_cpuctl
STATE=/data/adb/kpad_cpuctl
BB=/data/adb/ksu/bin/busybox
[ -x "$BB" ] || BB=/data/adb/magisk/busybox
[ -x "$BB" ] || BB=busybox

PORT="$1"
[ -n "$PORT" ] || PORT=8088
mkdir -p "$STATE"

# 模块目录被整体替换后(升级), 旧 httpd 的 cwd 会变成 "...(deleted)",
# 端口仍被占但所有 CGI 返回 404。起新的之前先把这种残留进程清掉。
OLDPID=$(cat "$STATE/web.pid" 2>/dev/null)
if [ -n "$OLDPID" ] && [ -d "/proc/$OLDPID" ]; then
    OLDCWD=$(readlink "/proc/$OLDPID/cwd" 2>/dev/null)
    if [ "$OLDCWD" != "$MODDIR/webroot" ]; then
        kill "$OLDPID" 2>/dev/null
        sleep 1
        [ -d "/proc/$OLDPID" ] && kill -9 "$OLDPID" 2>/dev/null
        rm -f "$STATE/web.pid"
    fi
fi

nohup "$BB" httpd -f -p "127.0.0.1:$PORT" -h "$MODDIR/webroot" -c "$MODDIR/httpd.conf" >/dev/null 2>&1 &
echo $! > "$STATE/web.pid"
echo "$PORT" > "$STATE/web_port"
