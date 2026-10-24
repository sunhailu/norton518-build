#!/bin/bash
# 在 Linux（或 GitHub Actions）里编译 Norton Core 518 的 OpenWrt 6.x 固件，
# 并打包成可直接 TFTP 刷写的 .img（沿用原厂引导链 + flash.scr）
set -euxo pipefail

CI_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${SRC:-$CI_DIR/openwrt}"
REPO="${REPO:-https://github.com/VIKINGYFY/immortalwrt.git}"
BRANCH="${BRANCH:-owrt}"
JOBS="${JOBS:-$(nproc)}"
OUT="$CI_DIR/out"
mkdir -p "$OUT"

echo "==> 源码: $REPO ($BRANCH)"
if [ ! -d "$SRC/.git" ]; then
	git clone --depth 1 -b "$BRANCH" "$REPO" "$SRC"
fi
cd "$SRC"
echo "==> 源码提交: $(git log -1 --oneline)"

echo "==> 注入 norton_core-518 设备支持"
python3 "$CI_DIR/inject.py" .

echo "==> 自检：注入幂等 + 刷机包重打包一致性"
python3 "$CI_DIR/inject.py" . >/dev/null
python3 "$CI_DIR/pack_img.py" --template "$CI_DIR/template.img" --self-test \
	| tail -n 1

echo "==> feeds"
./scripts/feeds update -a
./scripts/feeds install -a

echo "==> 生成 .config"
cp "$CI_DIR/config.seed" .config
make defconfig

for dev in norton_core-518 norton_core-518-alt; do
	if ! grep -q "CONFIG_TARGET_ipq806x_generic_DEVICE_${dev}=y" .config; then
		echo "!! 设备档案 ${dev} 没被选中，检查 DTS/镜像定义"
		exit 1
	fi
done
grep -i 'norton' .config || true

echo "==> 下载源码包"
make download -j"$JOBS" || make download -j1 V=s

echo "==> 开始编译 (jobs=$JOBS)"
make -j"$JOBS" || make -j1 V=s

echo "==> 产物:"
ls -lh bin/targets/ipq806x/generic/

STAMP=$(date +%Y%m%d)

for dev in norton_core-518 norton_core-518-alt; do
	SUP=$(ls bin/targets/ipq806x/generic/*-"${dev}"-squashfs-sysupgrade.bin 2>/dev/null | head -1 || true)
	if [ -z "$SUP" ]; then
		echo "!! 找不到 ${dev} 的 sysupgrade.bin"
		continue
	fi
	echo "==> [${dev}] 从 $(basename "$SUP") 取 kernel 与 rootfs"
	WORK="$OUT/$dev"
	mkdir -p "$WORK"
	python3 - "$SUP" "$WORK" <<'PY'
import sys, tarfile, os
sup, out = sys.argv[1], sys.argv[2]
f = tarfile.open(sup)          # sysupgrade-tar 是未压缩 tar
pre = [m for m in f.getnames() if m.endswith('/kernel')][0].rsplit('/', 1)[0]
for name, dst in (('kernel', 'kernel.itb'), ('root', 'rootfs.squashfs')):
    p = pre + '/' + name
    with f.extractfile(p) as src, open(os.path.join(out, dst), 'wb') as d:
        d.write(src.read())
    print('  %s -> %s (%d bytes)' % (p, dst, os.path.getsize(os.path.join(out, dst))))
PY

	suffix=""
	[ "$dev" = "norton_core-518-alt" ] && suffix="-alt"
	IMG="$OUT/norton_core-518-emmc-openwrt6x${suffix}-${STAMP}.img"
	echo "==> [${dev}] 打包 -> $(basename "$IMG")"
	python3 "$CI_DIR/pack_img.py" \
		--template "$CI_DIR/template.img" \
		--kernel "$WORK/kernel.itb" \
		--rootfs "$WORK/rootfs.squashfs" \
		--out "$IMG"
done

ls -lh "$OUT"/*.img

cat > "$OUT/刷机说明.txt" <<'TXT'
Norton Core 518 · OpenWrt 6.x 刷机说明
=====================================

1) 串口 115200 接好，上电狂敲回车进 U-Boot；
2) 电脑设 192.168.1.10/24，tftpd64 指向本目录；
3) 串口里执行：

   setenv serverip 192.168.1.10
   setenv ipaddr 192.168.1.2
   tftpboot <固件文件名.img>
   imgaddr=0x44000000 && source $imgaddr:script
   reset

先刷 norton_core-518-emmc-openwrt6x-*.img（交换机地址 0，按原厂 5.10 移植推定）；
若 dmesg 里看不到 qca8k / 网口不通，改刷 ...-alt-*.img（地址 0x10）。

救砖：无论刷坏什么，只要能进 U-Boot，就 tftpboot 原厂
ntbsubootemmc-ipq806x-single.img 再 source $imgaddr:script 即可回来。
TXT

echo "==> 完成"
