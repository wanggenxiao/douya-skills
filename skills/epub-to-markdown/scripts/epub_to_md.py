# -*- coding: utf-8 -*-
"""EPUB -> Markdown 转换器（纯标准库，零依赖，零 OCR）。

原理：EPUB = zip + XHTML，文字本来结构化，解包重排即可，不需要任何识别。
所以输出是确定性的——不存在 MinerU 那种"看着正常实则错了"的问题，
不需要强制校核流程，抽查一眼排版即可。

PDF 请走 mineru-pdf-to-markdown skill，不要用本脚本。

用法：
  python epub_to_md.py <book.epub> [-o <输出目录>] [--keep-empty-headings]

默认输出：<epub所在目录>/<书名>_epub/<书名>.md + images/
"""
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")  # Windows 中文控制台(GBK)打印书名会崩
except Exception:
    pass
import argparse
import os
import re
import sys
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree as ET

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def norm_space(s):
    return re.sub(r"\s+", " ", s)


class MDParser(HTMLParser):
    """XHTML -> Markdown。处理：标题/段落/列表/引用/粗斜体/图片/脚注上标。"""

    def __init__(self, img_dir_name):
        super().__init__(convert_charrefs=True)
        self.img_dir_name = img_dir_name
        self.out = []            # 已完成的块
        self.buf = []            # 当前行内文本
        self.skip = 0            # script/style 深度
        self.heading = 0         # 当前标题级别（0=不在标题内）
        self.list_stack = []     # [(tag, counter), ...]
        self.quote = 0           # blockquote 深度
        self.sup_buf = None      # 收集 sup（脚注引用）文本

    # ---------- 内部 ----------
    def _flush(self, prefix=""):
        text = norm_space("".join(self.buf)).strip()
        self.buf = []
        if text:
            if self.heading:
                self.out.append("#" * self.heading + " " + text)
            else:
                self.out.append(prefix + text)

    def _emit_line(self, s):
        self._flush()
        if s:
            self.out.append(s)

    # ---------- 标签 ----------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in HEADINGS:
            self._flush()
            self.heading = int(tag[1])
        elif tag == "p":
            self._flush("> " * self.quote if self.quote else "")
        elif tag == "br":
            self.buf.append("\n")
        elif tag == "hr":
            self._emit_line("---")
        elif tag in ("ul", "ol"):
            self._flush()
            self.list_stack.append([tag, 0])
        elif tag == "li":
            self._flush()
            if self.list_stack:
                top = self.list_stack[-1]
                if top[0] == "ol":
                    top[1] += 1
                    self.buf.append("%d. " % top[1])
                else:
                    self.buf.append("- ")
            else:
                self.buf.append("- ")
        elif tag == "blockquote":
            self.quote += 1
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            if src:
                name = os.path.basename(unquote(src))
                if name:
                    self._emit_line("![%s](%s/%s)"
                                    % (os.path.splitext(name)[0],
                                       self.img_dir_name, name))
        elif tag == "sup":
            self.sup_buf = []
        elif tag in ("b", "strong"):
            self.buf.append("**")
        elif tag in ("i", "em"):
            self.buf.append("*")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in HEADINGS:
            self._flush()
            self.heading = 0
        elif tag == "p":
            self._flush("> " * self.quote if self.quote else "")
        elif tag in ("ul", "ol"):
            self._flush()
            if self.list_stack:
                self.list_stack.pop()
        elif tag == "li":
            self._flush()
        elif tag == "blockquote":
            self._flush("> " * self.quote if self.quote else "")
            self.quote = max(0, self.quote - 1)
        elif tag == "sup":
            if self.sup_buf is not None:
                note = norm_space("".join(self.sup_buf)).strip().strip("[]()（）")
                if note:
                    self.buf.append("[^%s]" % note)
                self.sup_buf = None
        elif tag in ("b", "strong"):
            self.buf.append("**")
        elif tag in ("i", "em"):
            self.buf.append("*")

    def handle_data(self, data):
        if self.skip:
            return
        if self.sup_buf is not None:
            self.sup_buf.append(data)
        else:
            self.buf.append(data)


def read_opf(zf):
    """返回 (opf_path, opf_dir, spine_files, title, creator)。"""
    container = ET.fromstring(zf.read("META-INF/container.xml").decode("utf-8"))
    opf_path = container.find(".//c:rootfile", NS).get("full-path")
    opf_dir = os.path.dirname(opf_path)
    opf = ET.fromstring(zf.read(opf_path).decode("utf-8"))

    def meta(tag):
        el = opf.find(".//dc:%s" % tag, NS)
        return (el.text or "").strip() if el is not None and el.text else ""

    manifest = {}
    for item in opf.findall(".//opf:manifest/opf:item", NS):
        manifest[item.get("id")] = item.get("href")

    spine = []
    for ref in opf.findall(".//opf:spine/opf:itemref", NS):
        href = manifest.get(ref.get("idref"))
        if href:
            full = os.path.join(opf_dir, unquote(href)) if opf_dir else unquote(href)
            spine.append(full.replace("\\", "/"))
    return spine, meta("title"), meta("creator")


def decode_xhtml(raw):
    for enc in ("utf-8", "utf-16", "gb18030"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="ignore")


def convert(epub_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

    zf = zipfile.ZipFile(epub_path)
    spine, title, creator = read_opf(zf)

    parts, n_pages = [], 0
    for item in spine:
        try:
            raw = zf.read(item)
        except KeyError:
            continue
        p = MDParser("images")
        p.feed(decode_xhtml(raw))
        p._flush()
        body = "\n\n".join(x for x in p.out if x)
        if body.strip():
            parts.append(body)
            n_pages += 1

    # 抽图片
    n_imgs = 0
    for name in zf.namelist():
        if name.lower().endswith(IMG_EXT):
            base = os.path.basename(name)
            if base:
                with open(os.path.join(img_dir, base), "wb") as f:
                    f.write(zf.read(name))
                n_imgs += 1

    md = "\n\n".join(parts)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"

    base = os.path.splitext(os.path.basename(epub_path))[0]
    md_path = os.path.join(out_dir, base + ".md")

    header = []
    if title:
        header.append("# " + title)
    if creator:
        header.append("> 作者：" + creator)
    if header:
        md = "\n\n".join(header) + "\n\n---\n\n" + md

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    n_head = len(re.findall(r"^#{1,6} ", md, re.M))
    n_notes = len(re.findall(r"\[\^.+?\]", md))
    return md_path, {
        "spine_files": len(spine), "content_files": n_pages,
        "images": n_imgs, "chars": len(md),
        "headings": n_head, "footnote_refs": n_notes,
        "title": title, "creator": creator,
    }


def main():
    ap = argparse.ArgumentParser(description="EPUB -> Markdown（纯标准库）")
    ap.add_argument("epub", help="EPUB 文件绝对路径")
    ap.add_argument("-o", "--out", default=None,
                    help="输出目录，默认 <epub目录>/<书名>_epub/")
    args = ap.parse_args()

    epub = os.path.abspath(args.epub)
    if not os.path.isfile(epub):
        sys.exit("找不到文件: %s" % epub)
    if not zipfile.is_zipfile(epub):
        sys.exit("不是有效的 EPUB（zip）文件: %s" % epub)

    base = os.path.splitext(os.path.basename(epub))[0]
    out = args.out or os.path.join(os.path.dirname(epub), base + "_epub")

    md_path, st = convert(epub, out)
    print("MD: %s" % md_path)
    print("书名: %s / 作者: %s" % (st["title"] or "?", st["creator"] or "?"))
    print("spine: %d 个，含正文: %d 个，图片: %d 张" %
          (st["spine_files"], st["content_files"], st["images"]))
    print("md 字数: %d，标题: %d 个，脚注引用: %d 处" %
          (st["chars"], st["headings"], st["footnote_refs"]))


if __name__ == "__main__":
    main()
