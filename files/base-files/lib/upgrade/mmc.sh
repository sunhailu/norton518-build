#
# Copyright (C) 2016 lede-project.org
#
# 通用 eMMC 升级脚本（OpenWrt 6.x 已删除 mmc.sh，这里为 norton_core-518 补回）
# 用法：platform.sh 中设置 kernelname / rootfsname 后调用 mmc_do_upgrade "$1"
# 要求 sysupgrade 镜像是 tar 包，内含 sysupgrade-<board>/kernel 与 /root
#

. /lib/functions.sh

mmc_do_upgrade() {
	local tar_file="$1"
	local rootfs=
	local kernel=

	[ -z "$kernel" ] && kernel=$(find_mmc_part ${kernelname})
	[ -z "$rootfs" ] && rootfs=$(find_mmc_part ${rootfsname})

	[ -z "$kernel" ] && echo "Upgrade failed: kernel partition not found! Rebooting..." && reboot -f
	[ -z "$rootfs" ] && echo "Upgrade failed: rootfs partition not found! Rebooting..." && reboot -f

	mmc_do_flash $tar_file $kernel $rootfs

	return 0
}

mmc_do_flash() {
	local tar_file=$1
	local kernel=$2
	local rootfs=$3

	# keep sure its unbound
	losetup --detach-all || {
		echo Failed to detach all loop devices. Skip this try.
		reboot -f
	}

	# use the first found directory in the tar archive
	local board_dir=$(tar tf $tar_file | grep -m 1 '^sysupgrade-.*/$')
	board_dir=${board_dir%/}

	echo "flashing kernel to $kernel"
	tar xf $tar_file ${board_dir}/kernel -O >$kernel

	echo "flashing rootfs to ${rootfs}"
	tar xf $tar_file ${board_dir}/root -O >"${rootfs}"

	# a padded rootfs is needed for overlay fs creation
	local offset=$(tar xf $tar_file ${board_dir}/root -O | wc -c)
	[ $offset -lt 65536 ] && {
		echo Wrong size for rootfs: $offset
		sleep 10
		reboot -f
	}

	# Mount loop for rootfs_data
	local loopdev="$(losetup -f)"
	losetup -o $offset $loopdev $rootfs || {
		echo "Failed to mount looped rootfs_data."
		sleep 10
		reboot -f
	}

	echo "Format new rootfs_data at position ${offset}."
	mkfs.ext4 -F -L rootfs_data $loopdev
	mkdir /tmp/new_root
	mount -t ext4 $loopdev /tmp/new_root && {
		echo "Saving config to rootfs_data at position ${offset}."
		cp -v "$UPGRADE_BACKUP" "/tmp/new_root/$BACKUP_FILE"
		umount /tmp/new_root
	}

	# Cleanup
	losetup -d $loopdev >/dev/null 2>&1
	sync
	umount -a
	reboot -f
}
