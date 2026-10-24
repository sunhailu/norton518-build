#!/usr/bin/env python3
"""把 kernel.itb + rootfs.squashfs 打成 OpenWrt sysupgrade 用的 tar。

结构（与 /lib/upgrade/mmc.sh、default_do_upgrade 兼容）:
    sysupgrade-norton_core_518/
        kernel      <- FIT (kernel+dtb)
        root        <- squashfs（补齐到 64 KiB，供 loop overlay 使用）
        CONTROL     <- fwtool 元数据
"""
import io
import json
import sys
import tarfile
import time

BOARD = "norton_core_518"
COMPATIBLE = "norton,core-518"
ALIGN = 65536


def add(tar, name, data, mode=0o644, isdir=False):
    ti = tarfile.TarInfo(name)
    ti.mtime = int(time.time())
    ti.mode = mode
    if isdir:
        ti.type = tarfile.DIRTYPE
        ti.size = 0
        tar.addfile(ti)
    else:
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))


def main(kernel_path, rootfs_path, out_path):
    kern = open(kernel_path, "rb").read()
    root = open(rootfs_path, "rb").read()
    pad = (-len(root)) % ALIGN
    root_padded = root + b"\0" * pad

    meta = {
        "metadata_version": "1.1",
        "compatible": [COMPATIBLE],
        "supported_devices": [COMPATIBLE],
        "version_number": "6.18",
        "version_code": "6.18",
        "build_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    control = json.dumps(meta, sort_keys=True).encode()

    base = "sysupgrade-%s/" % BOARD
    with tarfile.open(out_path, "w") as tar:
        add(tar, base, b"", isdir=True)
        add(tar, base + "kernel", kern)
        add(tar, base + "root", root_padded)
        add(tar, base + "CONTROL", control)

    print("sysupgrade tar: %s" % out_path)
    print("  kernel %d bytes, root %d (+%d pad), CONTROL %d bytes"
          % (len(kern), len(root), pad, len(control)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
