#!/system/bin/sh
# WebUI 后端接口 (busybox httpd CGI)
# 移植说明: 原版硬编码 p0/p6 两簇 + kgsl, 这里改成输出通用 clusters 数组,
# 前端按数组渲染, 簇数变化不用改代码。所有参数走白名单校验, 不拼接命令。

MODDIR=/data/adb/modules/kpad_cpuctl
[ -d "$MODDIR" ] || MODDIR=${0%/webroot/cgi-bin/api.cgi}
CP="$MODDIR/bin/cpuctl"
STATE=/data/adb/kpad_cpuctl
CFG=/sys/devices/system/cpu/cpufreq
CPUX=/sys/devices/system/cpu
BATT=/sys/class/power_supply/battery
KTH=/sys/kernel/thermal
PATH=/system/bin:/system/xbin:/data/adb/ksu/bin:/data/adb/magisk:$PATH
export PATH

BB=/data/adb/ksu/bin/busybox
[ -x "$BB" ] || BB=/data/adb/magisk/busybox
[ -x "$BB" ] || BB=busybox

. "$MODDIR/bin/profiles.sh" 2>/dev/null

get() { cat "$1" 2>/dev/null; }
is_num() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

# ---- GPU 节点解析(与 cpuctl 共用缓存) ----
GPU=""; GCDEV=""
resolve_gpu() {
    local d t name
    if [ -s "$STATE/gpu_cdev" ]; then
        IFS='|' read -r GCDEV GPU < "$STATE/gpu_cdev"
        [ -d "$GCDEV" ] && [ -d "$GPU" ] && return 0
    fi
    for d in /sys/class/thermal/cooling_device*; do
        t=$(get "$d/type")
        case "$t" in
            devfreq-*.mali)
                name=${t#devfreq-}
                [ -d "/sys/class/devfreq/$name" ] && { GCDEV=$d; GPU=/sys/class/devfreq/$name; return 0; } ;;
        esac
    done
    for d in /sys/class/devfreq/*; do
        case "${d##*/}" in *mali*) GPU=$d; return 0 ;; esac
    done
    return 1
}
resolve_gpu

qs="${QUERY_STRING:-}"
getv() {
    local pair
    local IFS='&'
    for pair in $qs; do
        case "$pair" in "$1="*) printf '%s' "${pair#*=}"; return 0 ;; esac
    done
}

jesc()  { JSTR=${1//\\/\\\\}; JSTR=${JSTR//\"/\\\"}; }
# cpuctl 的输出是多行(带换行), 直接塞进 JSON 会产生控制字符 -> 前端 JSON.parse 报错。
# 【坑】mksh 的 ${x//$'\n'/} 在本机不生效(实测三种写法都不行), 只能用 tr 压平。
jflat() { JSTR=$(printf '%s' "$1" | tr '\n\r\t' '   '); JSTR=${JSTR//\\/\\\\}; JSTR=${JSTR//\"/\\\"}; }
jarrv() { local out="" x; for x in $1; do is_num "$x" || continue; [ -z "$out" ] && out="$x" || out="$out,$x"; done; JARR="[$out]"; }

header() {
    printf 'Content-Type: application/json; charset=utf-8\r\n'
    printf 'Cache-Control: no-store\r\n'
    printf '\r\n'
}

# ---- 簇列表: 与 cpuctl 同一套发现逻辑 ----
detect_clusters() {
    local p id
    CL_IDS=""
    for p in "$CFG"/policy[0-9]*; do
        [ -d "$p" ] || continue
        id=${p##*/policy}
        case "$id" in ''|*[!0-9]*) continue ;; esac
        CL_IDS="$CL_IDS $id"
    done
    CL_IDS=${CL_IDS# }
}

cpus_span() {
    local first="" last="" c
    for c in $(get "$CFG/policy$1/related_cpus"); do
        [ -z "$first" ] && first=$c
        last=$c
    done
    [ -z "$first" ] && first=$1
    [ "$first" = "$last" ] && echo "$first" || echo "$first-$last"
}

cluster_name() {
    case "$CL_N" in
        1) echo "全核心" ;;
        2) [ "$1" = "0" ] && echo "性能核" || echo "超大核" ;;
        *) case "$1" in 0) echo "能效核" ;; 1) echo "性能核" ;; 2) echo "超大核" ;; *) echo "簇$1" ;; esac ;;
    esac
}

# 天花板(读完 cpuctl 缓存的 "值 时间戳"; 前端只展示, 不参与判断)
ceiling_cached() {
    local cf="$STATE/ceil.p$1" v ts
    [ -s "$cf" ] || { echo 0; return; }
    read -r v ts < "$cf"
    is_num "$v" && echo "$v" || echo 0
}

do_state() {
    local prof watch lim thr msec until now maxleft i=0 id c j="" pj=""
    local t=0 gt=0 bt=0 pmw=0 pma=0 pmv=0 pst=""
    local glev=0 gmax=0 gcur=0 gcap=0 gtop=0 gn=0 rrc=0 rrd=0
    local gbusy=0 gutil="" gb3="" gba=""
    local cores="" n=0

    local hud=0 hudiv=5
    prof=$(get "$STATE/profile"); [ -z "$prof" ] && prof=stock
    watch=$(get "$STATE/watch_enabled"); is_num "$watch" || watch=0
    # 状态栏 HUD
    hud=$(get "$STATE/hud_enabled"); is_num "$hud" || hud=0
    hudiv=$(get "$STATE/hud_interval"); is_num "$hudiv" || hudiv=5
    lim=$(get "$STATE/temp_limit"); is_num "$lim" || lim=60000
    thr=$(get "$STATE/throttled"); is_num "$thr" || thr=0
    msec=$(get "$STATE/max_seconds"); is_num "$msec" || msec=300
    until=$(get "$STATE/max_until")

    # 温度: 复用 cpuctl 建好的 zone 列表, 一次 awk 取最大值
    if [ -s "$STATE/tz_cpu.list" ]; then
        t=$("$BB" awk 'BEGIN{m=0} {v=$1+0; if(v>m)m=v} END{printf "%d", m}' $(sed 's|$|/temp|' "$STATE/tz_cpu.list") 2>/dev/null)
    fi
    is_num "$t" || t=0
    gt=$(get "$KTH/gpu_info"); gt=${gt%%,*}; gt=$(echo "$gt" | tr -dc '0-9')
    is_num "$gt" || gt=0
    bt=$(get "$BATT/temp"); is_num "$bt" || bt=0
    [ "$bt" -lt 1000 ] && bt=$((bt * 100))

    pmw=$("$BB" awk '
        FILENAME ~ "current_now$" { I=$1+0; next }
        FILENAME ~ "voltage_now$" { V=$1+0; next }
        FILENAME ~ "status$"      { ST=$1; next }
        END { mw=(V*I)/1000000000; if(mw<0)mw=-mw; printf "%d|%d|%d|%s\n", mw, I/1000, V/1000, ST }
    ' "$BATT/current_now" "$BATT/voltage_now" "$BATT/status" 2>/dev/null)
    IFS='|' read -r pmw pma pmv pst <<EOF
$pmw
EOF
    is_num "$pmw" || pmw=0
    is_num "$pmv" || pmv=0
    case "$pma" in -*) pma=${pma#-} ;; esac
    is_num "$pma" || pma=0

    now=$(date +%s)
    maxleft=0
    if is_num "$until" && [ "$now" -gt 0 ]; then
        maxleft=$((until - now)); [ "$maxleft" -lt 0 ] && maxleft=0
    fi

    # 簇数组
    detect_clusters
    CL_N=0; for id in $CL_IDS; do CL_N=$((CL_N + 1)); done
    i=0
    for id in $CL_IDS; do
        local cmin cmax ccur cgov cav cname ceil
        cmin=$(get "$CFG/policy$id/scaling_min_freq")
        cmax=$(get "$CFG/policy$id/scaling_max_freq")
        ccur=$(get "$CFG/policy$id/scaling_cur_freq")
        cgov=$(get "$CFG/policy$id/scaling_governor")
        cav=$(get "$CFG/policy$id/scaling_available_frequencies")
        cname=$(cluster_name "$i")
        ceil=$(ceiling_cached "$id")
        is_num "$cmin" || cmin=0
        is_num "$cmax" || cmax=0
        is_num "$ccur" || ccur=0
        is_num "$ceil" || ceil=0
        jesc "$cgov"; local gj=$JSTR
        jarrv "$cav"; local aj=$JARR
        local obj="{\"id\":\"$id\",\"name\":\"$cname\",\"cpus\":\"$(cpus_span "$id")\",\"min\":$cmin,\"max\":$cmax,\"cur\":$ccur,\"ceil\":$ceil,\"gov\":\"$gj\",\"avail\":$aj}"
        if [ -z "$j" ]; then j="$obj"; else j="$j,$obj"; fi
        i=$((i + 1))
    done

    # GPU
    glev=$(get "$GCDEV/cur_state"); is_num "$glev" || glev=0
    local f
    for f in $(get "$GPU/available_frequencies"); do
        is_num "$f" || continue
        [ "$gn" -eq 0 ] && gtop=$((f / 1000000))
        gn=$((gn + 1))
    done
    # 【坑·实测 2026-10-31】本机内核**不暴露真实 GPU 时钟**:
    #   /sys/kernel/thermal/gpu_freq 恒为 338000(最低档) —— 把 ged/gpu_bottom_freq 抬到 1GHz、
    #   devfreq max_freq 放开到 1612MHz、governor 切 performance, 它都不动;
    #   devfreq cur_freq 恒为 26MHz(dummy governor 的镜像, 甚至不在 OPP 表里)。
    #   早先这里拿 gpu_freq 当"实时频率"显示, 永远显示 338MHz, 是假数据。
    #   现在改报**当前上限**(选中的 OPP), 前端标注成 ≤ 以区别于实时值。
    gmax=$("$BB" awk -v lv="$glev" '{for(i=1;i<=NF;i++){if(i-1==lv){printf "%d", $i/1000000; exit}}}' \
        <<EOF
$(get "$GPU/available_frequencies")
EOF
)
    is_num "$gmax" || gmax=0
    gcur=$gmax          # 读不到真实时钟 -> 报当前上限(前端标 ≤)
    gcap=1

    # 核心
    n=0
    while [ "$n" -lt 8 ]; do
        p=$CPUX/cpu$n/online
        if [ -e "$p" ]; then cores="$cores$(get "$p")"; else cores="${cores}1"; fi
        n=$((n + 1))
    done
    [ -z "$cores" ] && cores="11111111"

    # 档位表 -> JSON (静态, 用 profiles.sh 的 mtime 做失效)
    PJ="$STATE/profiles.json"
    if [ ! -s "$PJ" ] || [ "$MODDIR/bin/profiles.sh" -nt "$PJ" ]; then
        profiles_dump | awk -F'|' '
            BEGIN { printf "[" }
            {
                if (NR>1) printf ","
                printf "{\"k\":\"%s\",\"n\":\"%s\",\"d\":\"%s\",\"lim\":\"%s/%s %s/%s %s/%s\",\"gpu\":\"%s\"}", $1,$2,$3,$4,$5,$6,$7,$8,$9,$10
            }
            END { printf "]" }' > "$PJ" 2>/dev/null
    fi
    [ -s "$PJ" ] && read -r pj < "$PJ"
    [ -z "$pj" ] && pj='[]'

    # 刷新率: peak=当前上限, dyn=面板实时值(静止会自己降到 48/50, 属正常)
    rrc=$(settings get system peak_refresh_rate 2>/dev/null)
    case "$rrc" in ''|null|*[!0-9]*) rrc=0 ;; esac
    rrd=$(get /sys/class/mi_display/disp-DSI-0/dynamic_fps)
    is_num "$rrd" || rrd=0

    jesc "$prof";   local profj=$JSTR
    jesc "$pst";    local pstj=$JSTR

    header
    printf '{'
    printf '"ok":1,'
    printf '"profile":"%s",' "$profj"
    printf '"watch":%s,"throttled":%s,"limit":%s,"maxSeconds":%s,"maxLeft":%s,' \
        "$watch" "$thr" "$lim" "$msec" "$maxleft"
    printf '"hud":%s,"hudInterval":%s,' "$hud" "$hudiv"
    printf '"temp":%s,"gpuTemp":%s,"battTemp":%s,' "$t" "$gt" "$bt"
    printf '"refresh":{"cur":%s,"dyn":%s,"lock":%s},' "$rrc" "$rrd" \
        "$([ -f "$STATE/refresh_lock" ] && echo 1 || echo 0)"
    printf '"power":{"mw":%s,"ma":%s,"mv":%s,"st":"%s"},' "$pmw" "$pma" "$pmv" "$pstj"
    printf '"clusters":[%s],' "$j"
    # GPU 占用率: /proc/mtk_mali/gpu_utilization -> "ACTIVE=95 3D/TA/COMPUTE=95/3/2 ..."
    gbusy=0
    gutil=$(get /proc/mtk_mali/gpu_utilization)
    case "$gutil" in
        *3D/TA/COMPUTE=*)
            gb3=${gutil#*3D/TA/COMPUTE=}; gb3=${gb3%%/*}
            gba=${gutil#*ACTIVE=}; gba=${gba%% *}
            is_num "$gb3" || gb3=0
            is_num "$gba" || gba=0
            if [ "$gb3" -gt 0 ]; then gbusy=$gb3; else gbusy=$gba; fi
            ;;
    esac
    printf '"gpu":{"level":%s,"count":%s,"topMhz":%s,"curMhz":%s,"maxMhz":%s,"busyPct":%s,"curIsCap":%s},' "$glev" "$gn" "$gtop" "$gcur" "$gmax" "$gbusy" "$gcap"
    printf '"cores":[%s,%s,%s,%s,%s,%s,%s,%s],' \
        "${cores:0:1}" "${cores:1:1}" "${cores:2:1}" "${cores:3:1}" \
        "${cores:4:1}" "${cores:5:1}" "${cores:6:1}" "${cores:7:1}"
    printf '"profiles":%s' "$pj"
    printf '}'
}

is_mask() {
    local m="$1"
    [ ${#m} -eq 8 ] || return 1
    case "$m" in *[!01]*) return 1 ;; esac
    return 0
}

do_action() {
    local act msg
    act=$(getv action)
    case "$act" in
        apply)
            local prof
            prof=$(getv profile)
            profiles_dump | grep -q "^$prof|" || { header; printf '{"ok":0,"msg":"未知档位"}'; return 0; }
            msg=$(sh "$CP" apply "$prof" 2>&1)
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        set)
            local pol mn mx
            pol=$(getv cluster); mn=$(getv min); mx=$(getv max)
            is_num "$pol" || { header; printf '{"ok":0,"msg":"簇参数非法"}'; return 0; }
            is_num "$mn" && is_num "$mx" || { header; printf '{"ok":0,"msg":"频率必须是数字"}'; return 0; }
            [ ${#mn} -le 8 ] && [ ${#mx} -le 8 ] || { header; printf '{"ok":0,"msg":"数值越界"}'; return 0; }
            msg=$(sh "$CP" set "$pol" "$mn" "$mx" 2>&1)
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        gpu)
            local lv
            lv=$(getv level)
            is_num "$lv" || { header; printf '{"ok":0,"msg":"GPU 档位非法"}'; return 0; }
            [ "$lv" -le 60 ] || { header; printf '{"ok":0,"msg":"GPU 档位越界"}'; return 0; }
            msg=$(sh "$CP" gpu "$lv" 2>&1)
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        core)
            local n v
            n=$(getv cpu); v=$(getv value)
            is_num "$n" && [ "$n" -le 7 ] || { header; printf '{"ok":0,"msg":"核心编号 0-7"}'; return 0; }
            case "$v" in 0|1) ;; *) header; printf '{"ok":0,"msg":"参数非法"}'; return 0 ;; esac
            [ "$n" = "0" ] && [ "$v" = "0" ] && { header; printf '{"ok":0,"msg":"cpu0 是引导核"}'; return 0; }
            msg=$(sh "$CP" core "$n" "$v" 2>&1)
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        cores)
            local m
            m=$(getv mask)
            is_mask "$m" || { header; printf '{"ok":0,"msg":"掩码必须是 8 位 0/1"}'; return 0; }
            msg=$(sh "$CP" cores "$m" 2>&1)
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        hud)
            local v
            v=$(getv value)
            case "$v" in
                0) msg=$(sh "$CP" hud off 2>&1) ;;
                1) msg=$(sh "$CP" hud on 2>&1) ;;
                *) header; printf '{"ok":0,"msg":"参数非法"}'; return 0 ;;
            esac
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        hud_interval)
            local v
            v=$(getv value)
            case "$v" in ''|*[!0-9]*) header; printf '{"ok":0,"msg":"间隔非法"}'; return 0 ;; esac
            echo "$v" > "$STATE/hud_interval"
            header; printf '{"ok":1,"msg":"HUD 间隔已设为 %s 秒"}' "$v"
            ;;
        watch)
            local v
            v=$(getv value)
            case "$v" in
                0) msg=$(sh "$CP" watch off 2>&1) ;;
                1) msg=$(sh "$CP" watch on 2>&1) ;;
                *) header; printf '{"ok":0,"msg":"参数非法"}'; return 0 ;;
            esac
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        temp_limit)
            local v
            v=$(getv value)
            is_num "$v" || { header; printf '{"ok":0,"msg":"温度必须是数字"}'; return 0; }
            { [ "$v" -ge 45000 ] && [ "$v" -le 85000 ]; } || { header; printf '{"ok":0,"msg":"温度上限 45-85 度"}'; return 0; }
            echo "$v" > "$STATE/temp_limit"
            header; printf '{"ok":1,"msg":"温度上限已设为 %s℃"}' "$((v / 1000))"
            ;;
        refresh)
            local hz
            hz=$(getv value)
            is_num "$hz" || { header; printf '{"ok":0,"msg":"刷新率必须是数字"}'; return 0; }
            { [ "$hz" -ge 48 ] && [ "$hz" -le 165 ]; } || { header; printf '{"ok":0,"msg":"刷新率 48-165"}'; return 0; }
            msg=$(sh "$CP" refresh "$hz" 2>&1)
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        refresh_lock)
            local v
            v=$(getv value)
            case "$v" in 0|1) ;; *) header; printf '{"ok":0,"msg":"参数非法"}'; return 0 ;; esac
            if [ "$v" = "1" ]; then
                msg=$(sh "$CP" refresh lock on 2>&1)
            else
                msg=$(sh "$CP" refresh lock off 2>&1)
            fi
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        backup)
            sh "$CP" backup >/dev/null 2>&1
            header; printf '{"ok":1,"msg":"已重新备份当前配置"}'
            ;;
        restore)
            msg=$(sh "$CP" restore 2>&1)
            header; printf '{"ok":1,"msg":"%s"}' "$(jflat "$msg"; printf '%s' "$JSTR")"
            ;;
        *)
            header; printf '{"ok":0,"msg":"未知动作"}'
            ;;
    esac
}

act=$(getv action)
case "$act" in
    ""|state) do_state ;;
    *)        do_action ;;
esac
