#!/usr/bin/env python3
"""
把 norton_core-518 的设备支持注入到 OpenWrt/ImmortalWrt 的 ipq806x 目标树。

用法:  python3 inject.py <源码树根目录>

幂等：重复执行不会重复插入（检测到已存在则跳过）。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def insert_before(path, anchor, text, tag):
    s = read(path)
    if tag in s:
        print("  skip (already present): %s in %s" % (tag, os.path.basename(path)))
        return
    if anchor not in s:
        raise SystemExit("!! anchor not found in %s:\n%r" % (path, anchor[:120]))
    s = s.replace(anchor, text + anchor, 1)
    write(path, s)
    print("  patched %s (+%s)" % (os.path.basename(path), tag))


def insert_after(path, anchor, text, tag):
    s = read(path)
    if tag in s:
        print("  skip (already present): %s in %s" % (tag, os.path.basename(path)))
        return
    if anchor not in s:
        raise SystemExit("!! anchor not found in %s:\n%r" % (path, anchor[:120]))
    s = s.replace(anchor, anchor + text, 1)
    write(path, s)
    print("  patched %s (+%s)" % (os.path.basename(path), tag))


DEVICE_MK = """
define Device/norton_core-518
\t$(call Device/FitImage)
\tDEVICE_VENDOR := Norton
\tDEVICE_MODEL := Core 518
\tDEVICE_DTS := qcom-ipq8065-norton-core-518
\tSOC := qcom-ipq8065
\tBOARD_NAME := norton_core-518
\tSUPPORTED_DEVICES += norton,core-518
\tDEVICE_PACKAGES := ath10k-firmware-qca9984-ct \\
\t\tkmod-usb3 kmod-usb-dwc3-qcom kmod-phy-qcom-ipq806x-usb \\
\t\t-kmod-ata-ahci -kmod-ata-ahci-platform \\
\t\t-kmod-usb-ledtrig-usbport
endef
TARGET_DEVICES += norton_core-518

# 备选变体：交换机 MDIO 地址 0x10（若默认的 0 认不到 qca8k 就用这个）
define Device/norton_core-518-alt
\t$(call Device/FitImage)
\tDEVICE_VENDOR := Norton
\tDEVICE_MODEL := Core 518 (switch addr 10)
\tDEVICE_DTS := qcom-ipq8065-norton-core-518-alt
\tSOC := qcom-ipq8065
\tBOARD_NAME := norton_core-518
\tSUPPORTED_DEVICES += norton,core-518
\tDEVICE_PACKAGES := ath10k-firmware-qca9984-ct \\
\t\tkmod-usb3 kmod-usb-dwc3-qcom kmod-phy-qcom-ipq806x-usb \\
\t\t-kmod-ata-ahci -kmod-ata-ahci-platform \\
\t\t-kmod-usb-ledtrig-usbport
endef
TARGET_DEVICES += norton_core-518-alt
"""

IFACE_CASE = """\tnorton,core-518)
\t\tucidef_set_interfaces_lan_wan "lan1 lan2 lan3 lan4" "wan"
\t\t;;
"""

MAC_CASE = """\tnorton,core-518)
\t\thw_mac_addr=$(mmc_get_mac_binary "0:ART" 0x0)
\t\t[ -n "$hw_mac_addr" ] && {
\t\t\tucidef_set_interface_macaddr "lan" "$hw_mac_addr"
\t\t\tucidef_set_interface_macaddr "wan" "$(macaddr_add $hw_mac_addr 1)"
\t\t}
\t\t;;
"""

UPGRADE_CASE = """\tnorton,core-518)
\t\tkernelname="0:HLOS"
\t\trootfsname="rootfs"
\t\tmmc_do_upgrade "$1"
\t\t;;
"""


def main(root):
    tgt = os.path.join(root, "target/linux/ipq806x")
    if not os.path.isdir(tgt):
        raise SystemExit("!! not an ipq806x tree: %s" % tgt)

    # 1. DTS（主用 0x10 + 备选 0x0）
    dts_src = os.path.join(FILES, "dts/qcom-ipq8065-norton-core-518.dts")
    dts_txt = read(dts_src)
    dts_dst = os.path.join(tgt, "dts/qcom-ipq8065-norton-core-518.dts")
    write(dts_dst, dts_txt)
    print("  copied dts ->", dts_dst)

    alt = dts_txt.replace(
        '\tswitch@0 {\n\t\tcompatible = "qca,qca8337";\n\t\t#address-cells = <1>;\n'
        '\t\t#size-cells = <0>;\n\t\treg = <0>;',
        '\tswitch@10 {\n\t\tcompatible = "qca,qca8337";\n\t\t#address-cells = <1>;\n'
        '\t\t#size-cells = <0>;\n\t\treg = <0x10>;')
    if alt == dts_txt:
        raise SystemExit("!! switch address replacement failed")
    dts_alt = os.path.join(tgt, "dts/qcom-ipq8065-norton-core-518-alt.dts")
    write(dts_alt, alt)
    print("  copied dts ->", dts_alt)

    # 2. image/generic.mk
    mk = os.path.join(tgt, "image/generic.mk")
    if "Device/norton_core-518" not in read(mk):
        write(mk, read(mk).rstrip() + "\n" + DEVICE_MK)
        print("  patched generic.mk (+Device/norton_core-518)")
    else:
        print("  skip generic.mk")

    # 3. board.d/02_network
    net = os.path.join(tgt, "base-files/etc/board.d/02_network")
    insert_before(net,
                  '\t*)\n\t\techo "Unsupported hardware. Network interfaces not intialized"',
                  IFACE_CASE, "norton,core-518)")
    insert_before(net,
                  "\tesac\n}\n\nboard_config_update",
                  MAC_CASE, "mmc_get_mac_binary")

    # 4. lib/upgrade/platform.sh  (+ mmc.sh helper)
    plat = os.path.join(tgt, "base-files/lib/upgrade/platform.sh")
    s = read(plat)
    if "/lib/upgrade/mmc.sh" not in s:
        s = s.replace("RAMFS_COPY_DATA='/etc/fw_env.config /var/lock/fw_printenv.lock'\n",
                      "RAMFS_COPY_DATA='/etc/fw_env.config /var/lock/fw_printenv.lock'\n"
                      "\n. /lib/upgrade/mmc.sh\n", 1)
        # mmc.sh 需要 losetup / mkfs.ext4 在升级用的 ramdisk 里
        s = s.replace("RAMFS_COPY_BIN='fw_printenv fw_setenv'",
                      "RAMFS_COPY_BIN='fw_printenv fw_setenv losetup mkfs.ext4'", 1)
        write(plat, s)
        print("  patched platform.sh (source mmc.sh)")
    shutil.copyfile(os.path.join(FILES, "base-files/lib/upgrade/mmc.sh"),
                    os.path.join(tgt, "base-files/lib/upgrade/mmc.sh"))
    print("  copied lib/upgrade/mmc.sh")
    insert_before(plat,
                  "\taruba,ap-32x)\n",
                  UPGRADE_CASE, 'kernelname="0:HLOS"')

    print("OK: norton_core-518 injected")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
