#!/system/bin/sh
# 通知发送助手 —— 以 shell(uid 2000) 身份发通知。
#
# 【为什么需要它】Android 16(SDK 36) 上以 root(uid 0) 身份跑 `cmd notification post`,
# 通知会被系统拒绝: 命令返回 0 但通知不出现, 加 -v 才能看到
#   "warning: couldn't find notification after enqueueing"。
# 原因是通知系统不为 uid=0 的包创建 NotificationRecord。
# 改用 uid 2000(shell) 身份发就正常了(实测 Redmi K Pad / Android 16 有效)。
#
# 参数通过环境变量传进来(不用命令行参数), 因为标题/正文里有空格、中文、
# 分隔符 · 和 ℃, 走命令行要处理多层引号嵌套, 极易出错。
#
# 用法:  HUD_NOTIFY_TAG=kpadhud HUD_NOTIFY_TITLE="标题" HUD_NOTIFY_BODY="正文" ./notify.sh
#
# 兼容性: 老系统上 `su 2000` 可能不可用或行为不同, 此时回退到 root 直接发。

TAG=${HUD_NOTIFY_TAG:-kpadhud}
TITLE=${HUD_NOTIFY_TITLE:-}
BODY=${HUD_NOTIFY_BODY:-}

[ -n "$TITLE" ] || exit 0

# 优先走 uid 2000(shell)。su 不会清掉这里的 HUD_NOTIFY_* 环境变量(已实测)。
if su 2000 -c "cmd notification post -S bigtext -t \"\$HUD_NOTIFY_TITLE\" \"\$HUD_NOTIFY_TAG\" \"\$HUD_NOTIFY_BODY\"" >/dev/null 2>&1; then
    exit 0
fi

# 回退: 直接用 root 发(旧系统上可用)
cmd notification post -S bigtext -t "$TITLE" "$TAG" "$BODY" >/dev/null 2>&1
exit 0
