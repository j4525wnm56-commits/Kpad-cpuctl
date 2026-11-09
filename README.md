# K Pad 调频 (kpad_cpuctl)

为 **REDMI K Pad（turner / 天玑 9400+ / MTK mt6991 / Mali-G925）** 从骁龙平台的同款模块移植而来
（上游仓库见文末「来源」）。

不是改几个常量就完事——两平台的调频子系统几乎没有一处对得上，所以 CPU/GPU/温度三条链
都重写了，档位表也从"写死 kHz"改成了"按频率百分比生成"。

---

## 一、平台差异对照（为什么必须重写）

| 维度 | 骁龙机型 (SM8850) | K Pad (turner / mt6991) | 处理方式 |
|---|---|---|---|
| CPU 簇 | 2 簇：`policy0`(cpu0-5)、`policy6`(cpu6-7) | **3 簇**：`policy0`(0-3)、`policy4`(4-6)、`policy7`(7) | 运行时扫描 `cpufreq/policy*` 自动发现，簇数不再写死 |
| 频率表 | kHz 绝对值写死 | 2.4G / 3.3G / 3.73G 三套标称 | 档位改成「占标称频率的百分比」，再用 `scaling_available_frequencies` 吸附到真实档 |
| 调频驱动 | `qcom-cpufreq-hw` + `walt` | `mtk-cpufreq-hw` + `sugov_ext` | 保留系统 gov（`sugov_ext`），只动 min/max，不切 governor |
| 频率上限 | 可写到 `cpuinfo_max` | **有一个动态上限**：小米 `miperf2`/`joyose` 注册的 freq QoS，1.2G~3.3G 之间随前台应用跳；超写会被静默钳回 | 写目标后回读记 `eff`，被钳就标注「被系统限制」；守护周期重写，上限一放开自动跟上 |
| GPU | `kgsl-3d0`，`pwrlevel` 0-17 | **Mali devfreq** `48000000.mali`，50 档，无 kgsl | 走 `cooling_device(type=devfreq-*.mali)` 的 `cur_state`，它天然就是"降序频点表下标" |
| GPU 温度 | `kgsl-3d0/temp` | `/sys/kernel/thermal/gpu_info` 第一字段（m℃） | 单独解析 |
| CPU 温度 | `thermal_zone` 里 `cpu-[01]-*` | `cpu-little-*` / `cpu-medium-*` / `cpu-big-*` / `cpu-dsu*` | 按 `cpu-*` 前缀聚合取最大 |
| 电池温度 | `battery/temp`（m℃） | `battery/temp` 是 **0.1℃**（273 = 27.3℃） | 小于 1000 自动 ×100 归一 |
| 选频器 | 假设 `available_frequencies` 升序并用 `break` | 实际是**降序**（3730000 打头） | 扫完再比，不 break（原版这段在本机会直接返回最高频） |

## 二、实测结论（2026-10-30，这台机器上跑出来的）

```
设备    : 25079RPDCC / turner      Android 16 (SDK 36) / HyperOS 3.1 内测
簇数    : 3  (0 4 7)
  policy0  能效核  cpu 0-3     339000 - 2400000
  policy4  性能核  cpu 4-6     622000 - 3300000
  policy7  超大核  cpu 7       798000 - 3730000
GPU     : /sys/class/devfreq/48000000.mali  (50 档, 顶 1612MHz)
GPU温控 : cooling_device3 (devfreq-48000000.mali, 0..49)
```

关键行为（都实测过，不是推测）：

1. **写 `scaling_max_freq` 立刻生效、且能保持**（连续 14s 采样不被打回）。
2. **超过当前动态上限的写入会被静默钳回**（返回码 0，值不变）。
3. 动态上限**跟着前台应用走**：无前台应用时 p4 只有 1.2G、p7 只有 798M；
   拉起一个前台 App 后立刻放开到 1.8G / 1.7G。**跟温度无关**（测试时 SoC 38℃，`Thermal Status: 0`，
   `low_power=0`，写 `sports_mode=1` 也无效）。
4. 因此模块的策略是「**写目标，不硬顶**」：目标值写好，系统上限一放开就自动吃到；
   被钳时 `status` 会标 `← 被系统限制(目标 X)`。
5. cpu0-7 **全部**有 `online` 节点，限核真能用（cpu7 实测关得掉、开得回来）。

### HUD 读数准确性（2026-11-10 复核，v1.3.1 修）

排查"数据显示不准"时逐项对照了 HUD 输出与底层真值，三个都是**测量方法**问题，不是节点不存在：

| 现象 | 根因 | 实测证据 | 修法 |
|---|---|---|---|
| 面板 50Hz，HUD 长期显示 120Hz | `REF_SHOWN` 只在 `sample_app_fps` 走完那次 SurfaceFlinger dump 后才赋值，该函数有 3 处提前 `return`（`tick % period != 0` 最常见），绝大多数轮次根本到不了那一行 | 同一时刻 `dynamic_fps=50`（连采 5 次全 50）、SF `activeMode vsyncRate=50.00 Hz`、`mActiveModeId=6`（fps=50），HUD 却写 120；单独跑 `once`（新进程，无历史状态）立即输出 50 | 在 `sample_refresh()` 里每轮跟随 `dynamic_fps`（260µs） |
| 满载 45℃，HUD 显示 25℃ | 温度 30 秒才全读一次，注释理由"温度变化很慢（5s 差 0.1~1℃）"是**空载**结论 | 6 线程满载：真实温度 +0s 33.9℃ → +2s 41.5℃ → +6s 45.0℃，HUD 到 +6s 才跳到 44.0℃；此前 30 秒间隔意味着连续 6 轮少报 20℃ | `TEMP_INTERVAL` 默认 30 → 5；全读仅 1.34ms/轮，而整轮已 45ms |
| 出现 "88/50Hz" 这种帧率大于刷新率 | `APP_FPS` 是 hold 值（最长可挂十几秒），刷新率却是本轮实测 | — | `sample()` 里加 `FPS ≤ REF` 钳制，超过即归 0（显示 "-"），比钳到刷新率诚实 |

顺带排除的两个**不是** bug：

- **中核/超大核平时显示 min_freq 是准的**。同一簇内各核 `cpuN/cpufreq/scaling_cur_freq` 会互相矛盾（集群共用一个时钟域，物理上不可能不同频），说明单核节点是过期缓存，**policy 级才准**。加 6 线程满载后 HUD 立刻给出 小核 1.50G / 中核 1.80G / 超大核 1.70G，与 policy 真值完全一致。
- **`read` 与 `cat` 读同一节点结果不同，不是解析 bug**。`cat` 要 fork+exec，这个动作本身就把簇频率推上去：同一节点连读 10 次，`read` 得 8 次 622000（min），`cat` 得 7 次 1800000。HUD 用内建 `read`（不 fork）读到的是真正的静息值。

## 三、用法

```sh
P="sh /data/adb/modules/kpad_cpuctl/bin/cpuctl"

$P info                  # 拓扑 + 各簇系统上限
$P status                # 当前档位/频率/温度/功耗
$P list                  # 9 个档位一览
$P apply balanced        # 切档：stock floor eco saver balanced boost light heavy max
$P set 4 622000 1500000  # 单簇自定义（簇 ID 用 $P info 里的 policy 号）
$P setall 0:400000:1500000 4:622000:1800000 7:798000:1700000 20
$P gpu 10                # GPU 档位 0(1612MHz)..49(338MHz)
$P core 7 off            # 限核
$P cores 11111100        # 批量限核
$P watch on              # 守护，每 10s 重新压实（对抗系统改频）
$P temp | $P power
$P backup | $P restore
$P web start             # WebUI，浏览器开 http://127.0.0.1:8088/
```

WebUI：模块「操作」按钮会直接拉起浏览器；界面里三簇卡片、GPU 滑块、9 档位、核心开关、
温度让权上限都能点。

## 四、解锁 165Hz

面板本身支持 `60 / 90 / 120 / 144 / 165`（`dumpsys display` 的 `supportedRefreshRates` 实测），
出厂却把**三个键一起**压在 120：

| 键 | 作用 |
|---|---|
| `system/peak_refresh_rate` | AOSP 的刷新率上限 |
| `secure/user_refresh_rate` | AOSP 的"用户选择" |
| `secure/miui_refresh_rate` | 小米自己记的"用户选的档" |

少写一个就会被另一个压回去，所以 `cpuctl refresh <Hz>` 是一起写的。

```sh
$P refresh              # 看当前上限 / 面板支持 / SF vsync / 面板实时值
$P refresh 165          # 解锁到 165
$P refresh 144          # 降到 144
$P refresh auto         # 还原到模块接管前的出厂值（本机 120）
$P refresh lock on|off  # 可选守护，见下
```

**实测结果**（改完后逐场景验证）：桌面、系统设置、浏览器三者 `SF vsyncRate=165.00 Hz`；
游戏内实际渲染到 **150+ fps**（改之前被压在 120）。

两点必须讲清楚，否则会和预期不符：

1. **这不是"锁死 165"**。LTPO 面板静止时会自己降到 48/50Hz 省电（`dynamic_fps` 也跟着变），
   这是硬件行为，不该去关。`refresh` 改的是**上限**，只有交互/动画时才是 165。
   强行锁死要用 `min_refresh_rate`，但本机实测那个键**不生效**（设 165 后 vsync 反而掉到 50），
   所以模块不碰它。
2. **系统不会自己把 165 改回 120**（连续观察 40 秒无变化）。唯一确认的改写来源是
   **你自己在系统设置里改刷新率** —— 那个页面会同时写这三个键。
   所以 `refresh lock` 默认**关闭**：它是个"锁死"开关，连你手动改的值也会顶回去，
   想临时用 120 得先 `lock off`。不做无限拉锯（60 秒内纠正 >6 次就停手留标记）。

> 165Hz 的代价是功耗和发热，尤其游戏。想省电直接 `$P refresh 120`。

WebUI 里也有对应控件：「屏幕刷新率」一排 60/90/120/144/165 按钮，下面一个是「锁死上限」开关。
顺手修掉了 WebUI 里另一处假数据 —— GPU 那行原来显示"实时 338 MHz"（就是上面那个不可信的
`gpu_freq` 节点），现在显示 **上限 ≤xxx MHz**，和 HUD 的口径一致。

## 五、临时 root 的注意事项（重要）

这台机器 **BL 锁定 + KernelSU 以 LKM late-load**，重启后 root 会消失（`/data/adb/ksu` 还在，
但内核模块没加载）。因此：

- 重启后 **KSU 不会在 post-fs-data 阶段跑 `service.sh`**，模块不会自动武装；
- 需要重新拿到 root 后跑一次：
  ```sh
  adb shell su -c 'sh /data/adb/modules/kpad_cpuctl/bin/cpuctl arm'
  ```
  或者用仓库里的 `arm.sh`（Mac 侧一键：arm + 起 WebUI + 端口转发）。
- 档位、守护开关、WebUI 端口都写在 `/data/adb/kpad_cpuctl/`，**重启不丢**，arm 后自动按上次档位恢复。

## 六、风险

1. **温度让权上限默认 65℃**（原版 48℃）。MTK 的 `cpu-big-core7` 在满载时能到 80℃+，
   65℃ 会让模块提前「让权给系统温控」，属于保守设定；要更激进可以在界面里 `+5` 调到 85℃。
2. **`max` 档会把 GPU 钉在顶频**（`min_freq` 抬到 1612MHz），只适合短时跑分，
   默认 300 秒自动回落到 `heavy`。别在充电+壳子里长时间跑。
3. **改冷却设备 `cur_state` 会绕过一部分系统散热策略**。档位越高（数值越小）GPU 越热，
   出问题就把档位调回 `stock`：
   `sh /data/adb/modules/kpad_cpuctl/bin/cpuctl restore`。
4. CPU 簇的 `governor` **始终保留 `sugov_ext`**，不切 `performance`
   （MTK 上切 gov 会连带影响调度/频率 QoS，收益不大风险不小）。
5. 模块只吃 `cpufreq` / `devfreq` 两条标准链路，不碰 AVB/verity，不动分区，
   **卸载只需删目录 + restore**（`uninstall.sh` 会做）。

## 七、文件

```
module.prop              模块声明（id=kpad_cpuctl）
bin/cpuctl               核心：拓扑发现 + 档位 + GPU + 温度 + 守护 + WebUI 控制
bin/profiles.sh          9 档位表（百分比制）
bin/watch_run.sh         守护启动器（mkdir 原子锁防多实例）
bin/web_spawn.sh         httpd 启动器
webroot/index.html       界面（三簇动态渲染）
webroot/cgi-bin/api.cgi  JSON 接口（clusters 数组通用化）
service.sh               开机武装（临时 root 下需手动 arm）
action.sh                模块「操作」按钮
uninstall.sh             卸载还原
customize.sh             安装期机型检查
package.sh               打 zip
```

原版保留的坑（移植时沿用，别改回去）：

- 后台进程必须写在独立脚本里启动。在函数体内直接 `&` 会让 mksh 把整个调用上下文 fork 成子 shell，
  守护进程会自我复制。
- `web_spawn.sh` 里要校验旧 httpd 的 `cwd`：模块目录被整体替换后，旧进程还占着端口但所有 CGI 404。
- `httpd.conf` 必须用**空格**分隔，写成冒号会被当成 basic-auth 规则，全站 401。

---

## 来源

上游仓库（骁龙机型版）：<https://github.com/j4525wnm56-commits/K90ProMax-cpuctl>

本模块只针对 K Pad 的平台差异重写了 CPU/GPU/温度/刷新率链路，档位语义与上游一致。
