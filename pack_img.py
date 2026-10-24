#!/usr/bin/env python3
"""
把 OpenWrt 6.x 的编译产物（kernel FIT + squashfs rootfs）塞回 Norton Core 518 的
eMMC 一键刷机包，产出可直接 TFTP 刷写的 .img。

这个镜像的 FIT 是非标准“内联数据”格式（data 属性里直接存整块数据，而不是偏移量），
所以重打包只需要：换数据 → 改 hash → 改 flash.scr 里的扇区数 → 重算头部长度。

用法:
  python3 pack_img.py --template ntbsubootemmc-ipq806x-single.img \
      --kernel build/kernel.itb --rootfs build/rootfs.squashfs \
      --out norton_core-518-emmc-6x-1.img

自检（不换数据，重建后重新解析并与原文件逐组件比对）:
  python3 pack_img.py --template ntbsubootemmc-ipq806x-single.img --self-test
"""
import argparse
import re
import struct
import sys
import zlib

BE32 = lambda b, o: struct.unpack_from(">I", b, o)[0]
ALIGN4 = lambda n: (n + 3) & ~3


class Fit:
    def __init__(self, blob):
        self.raw = blob
        if BE32(blob, 0) != 0xD00DFEED:
            raise SystemExit("not a FIT/DTB")
        (self.totalsize, self.off_struct, self.off_strings, self.off_rsvp,
         self.version, self.last_comp, self.boot_cpuid,
         self.size_strings, self.size_struct) = struct.unpack_from(">9I", blob, 4)
        self.nodes = []
        self._parse()

    def _parse(self):
        p = self.off_struct
        ostr = self.off_strings
        stack = []
        while True:
            tok = BE32(self.raw, p)
            p += 4
            if tok == 1:
                end = self.raw.index(b"\0", p)
                name = self.raw[p:end].decode("latin-1")
                p = ALIGN4(end + 1)
                stack.append((name, []))
            elif tok == 2:
                name, props = stack.pop()
                path = "/" + "/".join([s[0] for s in stack] + [name])
                self.nodes.append((path.replace("//", "/"), props))
            elif tok == 3:
                ln, nameoff = struct.unpack_from(">II", self.raw, p)
                p += 8
                end = self.raw.index(b"\0", ostr + nameoff)
                nm = self.raw[ostr + nameoff:end].decode("latin-1")
                val = self.raw[p:p + ln]
                p = ALIGN4(p + ln)
                stack[-1][1].append((nm, val))
            elif tok == 4:
                continue
            elif tok == 9:
                break
            else:
                raise SystemExit("bad FDT token %d" % tok)

    # ---------------- helpers ----------------
    def props(self, path):
        for p, pr in self.nodes:
            if p == path:
                return dict(pr)
        return None

    def set_prop(self, path, name, value):
        for p, pr in self.nodes:
            if p != path:
                continue
            for i, (n, _) in enumerate(pr):
                if n == name:
                    pr[i] = (n, value)
                    return
            pr.append((name, value))
            return
        raise SystemExit("node not found: %s" % path)

    def images(self):
        out = []
        for path, pr in self.nodes:
            d = dict(pr)
            if "data" not in d:
                continue
            out.append((path,
                        d.get("description", b"").rstrip(b"\0").decode("latin-1"),
                        d["data"]))
        return out

    def update_crc(self, path):
        crc = zlib.crc32(self.props(path)["data"]) & 0xFFFFFFFF
        h = path + "/hash@1"
        if self.props(h) is not None:
            self.set_prop(h, "value", struct.pack(">I", crc))

    # ---------------- serialize ----------------
    def serialize(self):
        strtab = bytearray()
        stroff = {}
        sb = bytearray()
        for path, pr in self.nodes:
            parts = [x for x in path.split("/") if x]
            name = parts[-1] if parts else ""
            sb += struct.pack(">I", 1)
            sb += name.encode("latin-1") + b"\0"
            sb += b"\0" * ((-len(sb)) % 4)
            for n, v in pr:
                if n not in stroff:
                    stroff[n] = len(strtab)
                    strtab += n.encode("latin-1") + b"\0"
                sb += struct.pack(">III", 3, len(v), stroff[n])
                sb += v
                sb += b"\0" * ((-len(v)) % 4)
            sb += struct.pack(">I", 2)
        sb += struct.pack(">I", 9)

        off_struct = 0x38
        off_strings = off_struct + len(sb)
        off_strings = ALIGN4(off_strings)
        pad = off_strings - (off_struct + len(sb))
        size_strings = len(strtab)
        totalsize = ALIGN4(off_strings + size_strings)

        buf = bytearray(totalsize)
        struct.pack_into(">10I", buf, 0,
                         0xD00DFEED, totalsize, off_struct, off_strings, self.off_rsvp,
                         self.version, self.last_comp, self.boot_cpuid,
                         size_strings, len(sb))
        buf[off_struct:off_struct + len(sb)] = sb
        buf[off_strings:off_strings + size_strings] = strtab
        return bytes(buf)


def patch_scr(scr, nodename, sectors):
    """把 imxtract $imgaddr <nodename> 之后那条 mmc write 的扇区数改掉"""
    lines = scr.split("\n")
    out, n, pending = [], 0, False
    for line in lines:
        if ("imxtract $imgaddr " + nodename) in line:
            pending = True
            out.append(line)
            continue
        if pending and "mmc write $fileaddr" in line:
            m = re.match(r"(.*mmc write \$fileaddr\s+0x[0-9a-fA-F]+\s+)([0-9a-fA-F]+)(.*)$", line)
            if m:
                line = "%s%x%s" % (m.group(1), sectors, m.group(3))
                n += 1
                pending = False
        out.append(line)
    return "\n".join(out), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--kernel")
    ap.add_argument("--rootfs")
    ap.add_argument("--out")
    ap.add_argument("--kernel-size", type=lambda s: int(s, 0), default=8388608)
    ap.add_argument("--rootfs-align", type=lambda s: int(s, 0), default=65536)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    raw = open(a.template, "rb").read()
    fit = Fit(raw)

    print("template: %s (%d bytes)" % (a.template, len(raw)))
    for path, desc, data in fit.images():
        print("  %-60s %9d  crc=%08x" % (desc[:60] or path, len(data),
                                          zlib.crc32(data) & 0xFFFFFFFF))

    if a.self_test:
        out = fit.serialize()
        f2 = Fit(out)
        ok = len(f2.images()) == len(fit.images())
        for (p1, d1, b1), (p2, d2, b2) in zip(fit.images(), f2.images()):
            ok = ok and d1 == d2 and b1 == b2
        print("self-test: %d bytes (orig %d), reparse %s"
              % (len(out), len(raw), "OK" if ok else "MISMATCH"))
        return 0 if ok else 1

    kern = open(a.kernel, "rb").read()
    root = open(a.rootfs, "rb").read()
    if len(kern) > a.kernel_size:
        raise SystemExit("kernel too big: %d > %d" % (len(kern), a.kernel_size))
    kern_p = kern + b"\0" * (a.kernel_size - len(kern))
    root_p = root + b"\0" * ((-len(root)) % a.rootfs_align)

    imgs = fit.images()
    knode = next((p for p, d, _ in imgs if "mmcblk0p11-kernel" in d), None)
    rnode = next((p for p, d, _ in imgs if "mmcblk0p12-rootfs" in d), None)
    if not knode or not rnode:
        raise SystemExit("kernel/rootfs components not found in template")

    fit.set_prop(knode, "data", kern_p)
    fit.set_prop(rnode, "data", root_p)
    fit.update_crc(knode)
    fit.update_crc(rnode)

    spath = next((p for p, pr in fit.nodes
                  if any(n == "type" and v.rstrip(b"\0") == b"script" for n, v in pr)), None)
    if spath:
        scr = fit.props(spath)["data"].decode("latin-1")
        for node, data in ((knode, kern_p), (rnode, root_p)):
            nodename = node.split("/")[-1]
            sectors = len(data) // 512
            scr, n = patch_scr(scr, nodename, sectors)
            print("  %-38s %8d sectors (0x%x), patched %d write(s)"
                  % (nodename, sectors, sectors, n))
        fit.set_prop(spath, "data", scr.encode("latin-1"))
        fit.update_crc(spath)

    out = fit.serialize()
    open(a.out, "wb").write(out)
    print("wrote %s (%d bytes)" % (a.out, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
