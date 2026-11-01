#!/system/bin/sh
# 开机自启: 等系统启动完成 -> 按保存的档位武装 -> 起守护 -> 起 HUD -> 起 WebUI
#
# 注意(临时 root 场景): 本机 BL 是锁定的, KernelSU 以 LKM 方式 late-load,
# 重启后 root 会消失, 这时 KSU 不会在 post-fs-data 阶段跑本脚本。
# 这种情况下请用外部 arm.sh: adb shell su -c 'sh /data/adb/modules/kpad_cpuctl/service.sh'
MODDIR=${0%/*}
[ -d "$MODDIR" ] || MODDIR=/data/adb/modules/kpad_cpuctl
STATE=/data/adb/kpad_cpuctl
PKG=com.langrm.kpadhud

i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ "$i" -lt 150 ]; do
    sleep 2
    i=$((i + 1))
done

# 首次安装时拍一张出厂快照(只在没有快照时拍)
if [ ! -f /data/adb/kpad_cpuctl/backup/stock.conf ]; then
    sh "$MODDIR/bin/cpuctl" backup >/dev/null 2>&1
fi

sh "$MODDIR/bin/cpuctl" boot >/dev/null 2>&1 &

# ---------------- 状态栏 HUD ----------------
# 采样守护(上次开了就继续开)
if [ "$(cat "$STATE/hud_enabled" 2>/dev/null)" = "1" ]; then
    sleep 3
    # 走 cpuctl 而不是直接 hud_run.sh: 它会先清陈旧锁再拉起
    sh "$MODDIR/bin/cpuctl" hud on >/dev/null 2>&1
fi

# 悬浮窗 App: 模块自带了一份 APK。没装 -> 装; 已装但内容不同(md5) -> 覆盖成模块里这份。
# 用 md5 而不是 versionCode: 手机上拿不到 APK 的 versionCode(没有 aapt), 但 md5 一条命令就够,
# 20KB 的文件算一次代价可以忽略。
if [ -f "$MODDIR/KPadHUD.apk" ]; then
    NEED=0
    if ! pm list packages 2>/dev/null | grep -q "$PKG"; then
        NEED=1
    else
        CUR=$(dumpsys package "$PKG" 2>/dev/null | sed -n 's/.*codePath=\([^ ]*\).*/\1/p' | head -1)
        # codePath 是目录(/data/app/~~xxx/包名-yyy)，真正装的文件是它下面的 base.apk
        [ -n "$CUR" ] && [ -d "$CUR" ] && CUR="$CUR/base.apk"
        if [ -n "$CUR" ] && [ -f "$CUR" ]; then
            [ "$(md5sum "$CUR" 2>/dev/null | cut -d' ' -f1)" = \
              "$(md5sum "$MODDIR/KPadHUD.apk" 2>/dev/null | cut -d' ' -f1)" ] || NEED=1
        fi
    fi
    if [ "$NEED" = "1" ]; then
        # 【坑】pm install 不能直接读 /data/adb/modules/ 下的文件:
        #   SELinux 上 system_server 无权访问那个目录(实测报
        #   "System server has no access to read file context u:object_r:adb_data_file"),
        #   必须先拷到 /data/local/tmp/ 再装。
        cp "$MODDIR/KPadHUD.apk" /data/local/tmp/kpadhud_install.apk 2>/dev/null
        pm install -r -g /data/local/tmp/kpadhud_install.apk >/dev/null 2>&1
        rm -f /data/local/tmp/kpadhud_install.apk
        sleep 2
    fi
fi

if pm list packages 2>/dev/null | grep -q "$PKG"; then
    # appops 必须每次补: 重装 APK 会把它重置成 ignore, 窗口在但不显示(实测 mAppOpVisibility=false)
    appops set "$PKG" SYSTEM_ALERT_WINDOW allow 2>/dev/null
    # 【坑】不能写 "$PKG/.HudService": 那会解析成 com.langrm.kpadhud.HudService,
    #   但类实际在 com.langrm.k90hud 包下(只换了 applicationId, 源码包名没动)。
    #   写成简写的话启动静默失败(重定向到 /dev/null 了), 表现就是"重启后悬浮窗不见了"。
    am start-foreground-service -n "$PKG/com.langrm.k90hud.HudService" >/dev/null 2>&1
    # 那条 ONGOING 的"正在其他应用的上层显示内容"通知划不掉, 只能 snooze(hud.sh 里会每 50 分钟续)
    cmd notification snooze --for 3600000 \
        "0|android|0|com.android.server.wm.AlertWindowNotification - $PKG|1000" >/dev/null 2>&1
fi
