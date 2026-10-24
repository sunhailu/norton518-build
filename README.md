# norton-core-518 · OpenWrt 6.x 云端编译套件

给 **Norton Core 518**（Symantec 诺顿球，IPQ8065 + eMMC）做 OpenWrt 6.x 固件，
并打包成**和原厂一样的一键刷机 `.img`**（TFTP + `source $imgaddr:script`）。

不需要本机装 Linux：整套跑在 GitHub Actions 上，编完直接下载 `.img` 去刷。

---

## 1. 目录说明

```
.
├── .github/workflows/build.yml     # GitHub Actions 流水线
├── build.sh                        # 主流程：拉源码 → 注入设备 → 编译 → 打包
├── inject.py                       # 把 norton_core-518 支持注入 ipq806x 目标树
├── pack_img.py                     # 把 kernel/rootfs 塞回刷机包（重新算 CRC/扇区数）
├── config.seed                     # .config 种子（设备 + LuCI + 常用包）
├── template.img                    # 刷机包骨架＝原厂 ntbsubootemmc 包（引导链+GPT+flash.scr）
└── files/
    ├── dts/qcom-ipq8065-norton-core-518.dts      # 主用（交换机 MDIO 0x10）
    └── base-files/lib/upgrade/mmc.sh
```

> `inject.py` 会由上面那份 DTS 自动派生出 `-alt` 变体（只把交换机地址改成 `0x10`）。
> 一次编译同时产出两个 `.img`：内核完全一样，只差设备树里这一个地址。
> 先刷主用那个（地址 0）；`dmesg` 里没有 `qca8k` 就刷 `-alt`。

`inject.py` 会自动改这几处（可重复执行，幂等）：

| 文件 | 改动 |
|---|---|
| `target/linux/ipq806x/dts/` | 新增 `qcom-ipq8065-norton-core-518.dts` |
| `target/linux/ipq806x/image/generic.mk` | 追加 `Device/norton_core-518` |
| `.../base-files/etc/board.d/02_network` | 接口 `lan1-4`/`wan` + MAC 从 `0:ART` 读 |
| `.../base-files/lib/upgrade/platform.sh` | `mmc_do_upgrade` 写 `0:HLOS` / `rootfs` |
| `.../base-files/lib/upgrade/mmc.sh` | 补回 6.x 已删除的 eMMC 升级脚本 |

---

## 2. 怎么跑（GitHub 网页版，零命令行）

1. 新建一个**私有仓库**（例如 `norton518-build`），不要勾选任何模板文件。
2. 把本目录的**所有文件**（含 `.github/`）上传：
   - 网页 → `Add file` → `Upload files` → 把解压后的所有内容拖进去 → `Commit changes`
   - ⚠️ `.github/workflows/build.yml` 必须保留目录结构，否则 Actions 不会出现
3. 仓库 → `Settings` → `Actions` → `General` → 允许 Actions 运行（私有库默认允许）
4. 仓库 → `Actions` → 左侧 `build-norton-core-518` → `Run workflow` → 绿色按钮
5. 等 40–90 分钟（首次要下全套源码），成功后在该次运行页面底部 **Artifacts**
   下载 `norton-core-518-images`，里面有：
   - `norton_core-518-emmc-openwrt6x-YYYYMMDD.img` ← **先刷这个**（交换机地址 0）
   - `norton_core-518-emmc-openwrt6x-alt-YYYYMMDD.img` ← 网口认不到时的备选（地址 0x10）
   - `norton_core-518/{kernel.itb,rootfs.squashfs}`、`norton_core-518-alt/...`（排查用）

> 一次编译同时产出两个变体：**内核完全相同，只差设备树里交换机的 MDIO 地址**。
> 先刷第一个，`dmesg` 里能看到 `qca8k` 就说明网口通了；看不到就刷 `-alt` 那个。

> 私有仓库的 Actions 每月有免费额度（2000 分钟）；公开仓库免费不限。编一次约 60–90 分钟。

## 3. 本地 Linux 跑法

```bash
sudo apt install -y build-essential clang flex bison g++ gawk gcc-multilib \
  g++-multilib gettext git libncurses-dev libssl-dev python3 python3-distutils \
  rsync unzip zlib1g-dev file wget
bash build.sh
```

产物同样在 `out/`。

---

## 4. 产物怎么刷

和刷原厂那套**完全一样的流程**（见 `刷机指南-A-现有镜像.md` 第 4 节）：

```
setenv serverip 192.168.1.10
setenv ipaddr 192.168.1.2
tftpboot norton_core-518-emmc-openwrt6x-YYYYMMDD.img
imgaddr=0x44000000 && source $imgaddr:script
```

它会重写 GPT、引导、kernel(0:HLOS) 和 rootfs，然后 `reset`。

---

## 5. 已知风险（务必先看）

这是**从原厂二进制逆向重建**的移植，不是官方支持，第一次刷有不确定性：

| 项 | 状态 | 说明 |
|---|---|---|
| 引导 / eMMC / 串口 | 依据原厂 DTB，把握较大 | kernel 放在 `0:HLOS`，rootfs 放 `rootfs`，rootfs_data 走 loop |
| 交换机（网口） | **待验证（已备两版）** | 原厂用 `qca,ar8327-initvals`（swconfig 时代）。6.x 的 ipq806x 已完全 DSA 化（`CONFIG_NET_DSA_QCA8K=y`，02_network 里已无 `ucidef_add_switch`），所以只能走 `qca8k`。地址按原厂 5.10 移植的 DTB 定为 `0`——那份内核里 ar8216/swconfig 和 qca8k 都编了，而 DTS 用的是 `ethernet-phy@0`，说明交换机就挂在 mdio 0；同时编了 `-alt`（`0x10`）兜底 |
| 无线 QCA9984 ×2 | **待验证** | 校准数据在 `0:ART`（0x1000 / 0x5000 各 0x2f20）。第一版先走固件自带 BDF，能起但不保证最优；确认能起后再补 `11-ath10k-caldata` 的 norton 分支 |
| 内存大小 | 992 MB（按原厂 DTB） | 若启动时 reserved-memory 报错，需要改 `memory@0` |
| LED | 未配置 | 原厂 DTB 里没有可用 LED 节点，先不管 |

**刷之前务必留好退路**：先在串口里确认能进 U-Boot（`=>` 或 `IPQ806x#`），
这样任何情况下都能 `tftpboot` 刷回 `ntbsubootemmc-ipq806x-single.img` 或
`norton_core-518-ipq806x-emmc-qsdk-1.img`。

---

## 6. 下一步可以做的事

1. 第一次编译成功后，把 Actions 里的 `dmesg`（串口）贴出来，我按报错改 DTS；
2. 网口起来后再处理无线校准（把 `11-ath10k-caldata` 的 norton 分支补上）；
3. 之后想加插件，只改 `config.seed` 即可，不用动设备树。
