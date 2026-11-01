# customize.sh —— 由 Magisk / KernelSU 安装器在刷入阶段执行
# 只做: 机型提示 + 拍出厂快照 + 权限修正

ui_print "- kpad_cpuctl $(grep_prop version "$MODPATH/module.prop")"

DEVICE=$(getprop ro.product.device)
MODEL=$(getprop ro.product.model)
ui_print "- 当前设备: $MODEL / $DEVICE"

# 档位是按"各簇可达天花板的百分比"生成的, 换机型也能用;
# 但 GPU 部分假设是 Mali devfreq + cooling_device(type=devfreq-*.mali)。
case "$DEVICE" in
    turner) ;;
    *)
        ui_print "! 本模块为 REDMI K Pad / turner (Dimensity 9400+) 校准"
        ui_print "! 当前设备 ($DEVICE) 的簇数、可达频率或 GPU 节点可能不同"
        ui_print "! 安装不会损坏设备, 但档位可能不理想"
        ui_print "! 3 秒后继续，若要中止请现在断开"
        sleep 3
        ;;
esac

ui_print "- 修正文件权限"
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm_recursive "$MODPATH/bin" 0 0 0755 0755
set_perm "$MODPATH/service.sh"   0 0 0755
set_perm "$MODPATH/action.sh"    0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755
set_perm "$MODPATH/webroot/cgi-bin/api.cgi" 0 0 0755

ui_print "- 安装完成，重启后在管理器点「操作」按钮即可打开界面"
