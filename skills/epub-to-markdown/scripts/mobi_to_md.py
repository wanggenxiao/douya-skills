# -*- coding: utf-8 -*-
"""MOBI -> Markdown 转换器（纯标准库，零依赖）。

原理：mobi = PalmDB 容器 + PalmDOC LZ77 压缩的 HTML。
解压后就是结构化文本，和 epub 一样零识别误差、确定性输出。
HTML 部分复用 epub_to_md.py 的 MDParser。

★ 混合文件（MOBI6+KF8 双区）优先读 KF8 区（2026-09-27 攻克）：
z-lib 常见混合包，MOBI6 旧区是给上古设备的降级副本（可能有乱码、无标题标签），
真实阅读器显示的是 KF8 区（标准 XHTML，有 h1-h6，文字无损）。
不读 KF8 区会出现"以为自己转对了，其实读的是降级区"的隐形错字。

支持：compression=1(无压缩)/2(PalmDOC LZ77)，UTF-8/CP1252。
不支持：HUFF/CDIC(compression=17480)、带 DRM 的 mobi（遇到会直接报错说明）。

用法：
  python mobi_to_md.py <book.mobi> [-o <输出目录>]

默认输出：<mobi所在目录>/<书名>_mobi/<书名>.md + images/
"""
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")  # Windows 中文控制台(GBK)打印书名会崩
except Exception:
    pass
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from epub_to_md import MDParser, norm_space  # noqa: E402

IMG_MAGIC = [
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG", ".png"),
    (b"GIF8", ".gif"),
    (b"BM", ".bmp"),
    (b"<svg", ".svg"),
]


def palmdoc_decompress(data):
    """PalmDOC LZ77 解压（标准算法，与 calibre 实现逐字节一致）。"""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if c == 0:
            out.append(0)
        elif c <= 8:
            out += data[i:i + c]
            i += c
        elif c <= 0x7F:
            out.append(c)
        elif c <= 0xBF:
            if i >= n:
                break
            c2 = data[i]
            i += 1
            distance = ((c & 0x3F) << 5) | (c2 >> 3)
            length = (c2 & 0x07) + 3
            if distance > len(out):
                continue  # 容错：引用超出已解压内容则跳过
            for _ in range(length):
                out.append(out[-distance])
        else:
            out.append(0x20)
            out.append(c ^ 0x80)
    return bytes(out)


class MobiBook:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        hdr = self.data[:78]
        if len(hdr) < 78:
            raise ValueError("文件太小，不是 PalmDB")
        self.num_records = struct.unpack(">H", hdr[76:78])[0]
        self.offsets = []
        pos = 78
        for _ in range(self.num_records):
            off = struct.unpack(">I", self.data[pos:pos + 4])[0]
            self.offsets.append(off)
            pos += 8
        r0 = self.record(0)
        self.compression = struct.unpack(">H", r0[0:2])[0]
        self.text_length = struct.unpack(">I", r0[4:8])[0]
        self.num_text_records = struct.unpack(">H", r0[8:10])[0]
        self.encoding = struct.unpack(">I", r0[28:32])[0]
        if r0[16:20] != b"MOBI":
            raise ValueError("缺少 MOBI 头，可能是旧 PalmDOC 或加密文件")
        if self.compression == 17480:
            raise ValueError("HUFF/CDIC 压缩暂不支持")
        if self.compression not in (1, 2):
            raise ValueError("未知压缩类型 %d（可能带 DRM）" % self.compression)
        # MOBI 头内偏移：96=first image index（r0[112:116]），192=KF8 boundary（r0[208:212]）
        self.first_image_index = struct.unpack(">I", r0[112:116])[0]
        self.kf8_boundary = (struct.unpack(">I", r0[208:212])[0]
                             if len(r0) >= 212 else 0xFFFFFFFF)
        # extra record data flags 在【记录偏移 0xF2=242】（kindleunpack 实证），
        # 不是 258——258 读到的 0xFFFF 是填充值。
        # flags 语义：bit0=multibyte（末尾有 (末字节&3)+1 个多字节尾巴），
        # 其余每置一个高位 bit 多一个 trailing entry（7bit varint 编码长度）。
        # 两阶段修剪：先按 varint 剥 trailing entries，再剥 multibyte 尾巴。
        # （2026-09-27 教训：只剥 multibyte 会把 trailing entry 当压缩指令解析，
        #   产生 \x00/PUA 乱码，实测《链接》KF8 区 84 处）
        self.flags_m6, self.multibyte_m6, self.trailers_m6 = self._parse_flags(r0)

    @staticmethod
    def _parse_flags(hdr):
        flags = struct.unpack(">H", hdr[242:244])[0] if len(hdr) >= 244 else 0
        multibyte = flags & 1
        trailers, f = 0, flags
        while f > 1:
            if f & 2:
                trailers += 1
            f >>= 1
        return flags, multibyte, trailers

    @staticmethod
    def _trim_trailing(data, multibyte, trailers):
        for _ in range(trailers):
            num = 0
            for v in data[-4:]:
                if v & 0x80:
                    num = 0
                num = (num << 7) | (v & 0x7F)
            if num:
                data = data[:-num]
        if multibyte and data:
            num = (data[-1] & 0x3) + 1
            data = data[:-num]
        return data

    def record(self, i):
        start = self.offsets[i]
        end = self.offsets[i + 1] if i + 1 < self.num_records else len(self.data)
        return self.data[start:end]

    def _find_kf8_header(self):
        """混合文件里找第二个 MOBI 头（KF8 区），返回记录号或 None。"""
        if not (0 < self.kf8_boundary < self.num_records):
            return None
        for r in range(self.kf8_boundary,
                       min(self.kf8_boundary + 16, self.num_records)):
            rec = self.record(r)
            if len(rec) > 40 and rec[16:20] == b"MOBI":
                return r
        return None

    def _decompress_records(self, start, count, compression, multibyte, trailers):
        parts = []
        for i in range(start, min(start + count, self.num_records)):
            rec = self.record(i)
            rec = self._trim_trailing(rec, multibyte, trailers)
            if compression == 2:
                rec = palmdoc_decompress(rec)
            parts.append(rec)
        return b"".join(parts)

    def extract_html(self):
        """优先 KF8 区（真实阅读器显示的版本），退回 MOBI6 旧区。
        每个区用自己头部 0xF2 的 flags 做尾部修剪。"""
        hdr = self._find_kf8_header()
        if hdr is not None:
            rec = self.record(hdr)
            compression = struct.unpack(">H", rec[0:2])[0]
            tlen = struct.unpack(">I", rec[4:8])[0]
            ntr = struct.unpack(">H", rec[8:10])[0]
            _, mb, tr = self._parse_flags(rec)
            self.section = "KF8"
            self.section_text_records = ntr
            raw = self._decompress_records(hdr + 1, ntr, compression, mb, tr)[:tlen]
        else:
            self.section = "MOBI6"
            self.section_text_records = self.num_text_records
            raw = self._decompress_records(
                1, self.num_text_records, self.compression,
                self.multibyte_m6, self.trailers_m6)[:self.text_length]
        enc = "utf-8" if self.encoding == 65001 else "cp1252"
        return raw.decode(enc, errors="ignore")

    def extract_images(self, img_dir):
        """抽出图片记录，返回 {recindex: 文件名}（recindex 从 1 起）。

        recindex 语义 = 第 N 个图片记录。first_image_index 常不可靠
        （实测 z-lib 书填 0），改为从 MOBI6 文本记录之后扫全部记录、认图片 magic。
        ★ 只有图片记录才占号（2026-09-27 教训：非图片记录占号会导致
        全部图片引用整体错位，哥尼斯堡七桥图配成作者照片）。"""
        mapping = {}
        start = (self.first_image_index
                 if 0 < self.first_image_index < self.num_records
                 else self.num_text_records + 1)
        recindex = 1
        for i in range(start, self.num_records):
            rec = self.record(i)
            ext = None
            for magic, e in IMG_MAGIC:
                if rec[:len(magic)] == magic:
                    ext = e
                    break
            if ext:
                name = "img%04d%s" % (recindex, ext)
                with open(os.path.join(img_dir, name), "wb") as f:
                    f.write(rec)
                mapping[recindex] = name
                recindex += 1
        return mapping


def html_to_md(html_text, img_map):
    # KF8 区图片引用：src="kindle:embed:XXXX?..."，XXXX 是 base36 编码的 recindex
    def repl_embed(m):
        try:
            idx = int(m.group(1), 36)
        except ValueError:
            return ""
        name = img_map.get(idx)
        return ('<img src="%s"/>' % name) if name else ""
    html_text = re.sub(r'<img[^>]*?src=["\']kindle:embed:([0-9A-Za-z]+)[^>]*>',
                       repl_embed, html_text, flags=re.I)

    # MOBI6 区图片引用：recindex 属性（十进制）
    def repl_img(m):
        try:
            idx = int(m.group(1))
        except ValueError:
            return ""
        name = img_map.get(idx)
        return ('<img src="%s"/>' % name) if name else ""
    html_text = re.sub(r'<(?:img|image)[^>]*?recindex=["\']?(\d+)["\']?[^>]*>',
                       repl_img, html_text, flags=re.I)

    # mobi 分页符当分割线
    html_text = re.sub(r"<mbp:pagebreak[^>]*>", "<hr/>", html_text, flags=re.I)
    # KF8 区是完整 XHTML：剥掉 head（含 title/style），避免漏进正文
    html_text = re.sub(r"<head[^>]*>.*?</head>", "", html_text, flags=re.I | re.S)

    p = MDParser("images")
    p.feed(html_text)
    p._flush()
    md = "\n\n".join(x for x in p.out if x)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


# KF8 区 head 里的 <title> 是第一节的节名（如"书名页"），不能当书名
GENERIC_TITLES = {"书名页", "封面", "扉页", "版权页", "cover", "titlepage",
                  "title page", "copyright", "unknown", ""}


def guess_title(path, html_text):
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    if m:
        t = norm_space(m.group(1)).strip()
        if t and t.lower() not in GENERIC_TITLES:
            return t
    return os.path.splitext(os.path.basename(path))[0]


def convert(mobi_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

    book = MobiBook(mobi_path)
    html_text = book.extract_html()
    img_map = book.extract_images(img_dir)
    md = html_to_md(html_text, img_map)

    title = guess_title(mobi_path, html_text)
    base = os.path.splitext(os.path.basename(mobi_path))[0]
    md_path = os.path.join(out_dir, base + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# " + title + "\n\n---\n\n" + md)

    return md_path, {
        "title": title, "chars": len(md), "images": len(img_map),
        "headings": len(re.findall(r"^#{1,6} ", md, re.M)),
        "footnote_refs": len(re.findall(r"\[\^.+?\]", md)),
        "section": book.section,
        "text_records": book.section_text_records,
    }


def main():
    ap = argparse.ArgumentParser(description="MOBI -> Markdown（纯标准库）")
    ap.add_argument("mobi", help="MOBI 文件绝对路径")
    ap.add_argument("-o", "--out", default=None,
                    help="输出目录，默认 <mobi目录>/<书名>_mobi/")
    args = ap.parse_args()

    mobi = os.path.abspath(args.mobi)
    if not os.path.isfile(mobi):
        sys.exit("找不到文件: %s" % mobi)
    base = os.path.splitext(os.path.basename(mobi))[0]
    out = args.out or os.path.join(os.path.dirname(mobi), base + "_mobi")

    try:
        md_path, st = convert(mobi, out)
    except ValueError as e:
        sys.exit("转换失败: %s" % e)

    print("MD: %s" % md_path)
    print("书名: %s" % st["title"])
    print("读取区: %s（文本记录 %d 条，图片 %d 张）"
          % (st["section"], st["text_records"], st["images"]))
    print("md 字数: %d，标题: %d 个，脚注引用: %d 处"
          % (st["chars"], st["headings"], st["footnote_refs"]))
    if st["section"] == "MOBI6":
        print("⚠ 未检测到 KF8 区，读的是 MOBI6 旧区——若发现乱码/错字，")
        print("  大概率是旧区降级所致，请用 Calibre 转 epub 后走 epub_to_md.py")
    if st["chars"] < 2000:
        print("⚠ 字数异常少，可能是 DRM 空壳或解析问题，请抽查 md")


if __name__ == "__main__":
    main()
