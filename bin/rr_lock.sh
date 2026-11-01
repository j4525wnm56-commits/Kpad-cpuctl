#!/system/bin/sh
# 刷新率上限守护(可选, 默认关闭): 把 peak_refresh_rate 维持在设定的值。
#
# 【什么时候需要它】实测 40 秒内系统**不会**自己改回去, 唯一确认的改写来源是
#   "用户自己在设置里改了刷新率"(而且改完会同时动三个键)。所以这是给
#   "想让 165 一直是 165" 的人用的保险, 不是修 bug。
#
# 【它的副作用, 用之前要知道】它会把**你手动改的值**也顶回去 —— 开了它就等于
#   "锁死在这个上限", 想临时用 120 必须先 lock off。所以默认不开。
#
# 另外不做无限拉锯: 60 秒内纠正超过 MAXFIX 次就停手留标记, 免得反复切 vsync 造成闪烁。
STATE=/data/adb/kpad_cpuctl
INTERVAL=${RR_LOCK_INTERVAL:-10}
MAXFIX=${RR_LOCK_MAXFIX:-6}
FLAG=$STATE/refresh_lock
PIDF=$STATE/refresh_lock.pid
GIVEUP=$STATE/refresh_lock_gaveup

[ -f "$FLAG" ] || exit 0
echo $$ > "$PIDF"
rm -f "$GIVEUP"

fixes=0
win_start=$(date +%s)

while [ -f "$FLAG" ]; do
    want=$(cat "$STATE/refresh_hz" 2>/dev/null)
    case "$want" in ''|*[!0-9]*) want=165 ;; esac

    cur=$(settings get system peak_refresh_rate 2>/dev/null)
    if [ "$cur" != "$want" ]; then
        now=$(date +%s)
        # 每 60 秒重置计数窗口
        if [ $((now - win_start)) -ge 60 ]; then
            fixes=0
            win_start=$now
        fi
        if [ "$fixes" -ge "$MAXFIX" ]; then
            echo "在 60 秒内已纠正 $fixes 次, 系统仍在写回 —— 停止对抗" > "$GIVEUP"
            break
        fi
        settings put system peak_refresh_rate "$want" 2>/dev/null
        settings put secure user_refresh_rate "$want" 2>/dev/null
        settings put secure miui_refresh_rate "$want" 2>/dev/null
        fixes=$((fixes + 1))
        echo "$(date) 被改成 $cur, 已写回 $want (第 $fixes 次)" >> "$STATE/refresh_lock.log"
    fi
    sleep "$INTERVAL"
done

rm -f "$PIDF"
