#!/usr/bin/env python3
"""
给 .docx 插入可自动更新的目录（TOC 域）

用法:
    python add_toc.py --docx "方案.docx" [--out "方案_带目录.docx"]
                      [--levels 3] [--title "目 录"]
                      [--auto-style] [--dry-run]

关键说明:
  * Word 的目录是一个"域(field)"，它只收集**用了标题样式**的段落。
    文档里如果标题只是"手动调大字号+加粗"，目录会是空的 —— 这是最高发的坑。
    加 --auto-style 让脚本按编号模式(1 / 1.1 / 1.1.1)自动套上标题样式。
  * 插入后页码是空的，需要在 Word 里更新域：Ctrl+A 然后 F9（或打开时选"是"）。
    本脚本不调用 Word，不依赖装没装 Office。
"""
import argparse
import re
import sys
from pathlib import Path

try:
    import docx
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:
    print("[err] 缺少 python-docx，请先安装：pip install python-docx", file=sys.stderr)
    raise SystemExit(2)

# 1 / 1.1 / 1.1.1 ... 形式的编号（允许全角空格、顿号后缀）
NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[\s、\.．]+\S")
HEADING_STYLE_NAMES = ("Heading", "标题")


def para_is_heading(p) -> bool:
    name = (p.style.name or "")
    return any(name.startswith(h) for h in HEADING_STYLE_NAMES)


def detect_headings(doc):
    """返回 (已用标题样式的段落数, 按编号识别出的候选 [(段落, 级别)])"""
    styled = sum(1 for p in doc.paragraphs if para_is_heading(p))
    candidates = []
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text or len(text) > 60:          # 太长的多半是正文，不是标题
            continue
        if para_is_heading(p):
            continue
        m = NUM_RE.match(text)
        if m:
            level = m.group(1).count(".") + 1
            candidates.append((p, level))
    return styled, candidates


def apply_heading_styles(doc, candidates, max_level):
    applied = 0
    for p, level in candidates:
        if level > max_level:
            continue
        for style_name in (f"Heading {level}", f"标题 {level}"):
            try:
                p.style = doc.styles[style_name]
                applied += 1
                break
            except KeyError:
                continue
    return applied


def make_toc_field(levels: int):
    """构造 TOC 域的 XML 元素序列（begin / instrText / separate / 占位 / end）"""
    elems = []

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    elems.append(begin)

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f' TOC \\o "1-{levels}" \\h \\z \\u '
    elems.append(instr)

    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    elems.append(sep)

    hint = OxmlElement("w:t")
    hint.text = "右键此处选“更新域”，或按 Ctrl+A 再按 F9 生成目录"
    elems.append(hint)

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    elems.append(end)
    return elems


def insert_toc(doc, levels: int, title: str):
    """在正文最前面插入 标题段 + TOC 域 + 分页符"""
    body = doc.element.body
    first = doc.paragraphs[0] if doc.paragraphs else None

    # ⚠ 标题段不能用 Heading 样式，否则“目录”两个字会被收进目录自身
    title_p = doc.add_paragraph()
    title_run = title_p.add_run(title)
    title_run.bold = True
    title_run.font.size = Pt(16)
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    toc_p = doc.add_paragraph()
    run = toc_p.add_run()
    for el in make_toc_field(levels):
        run._r.append(el)

    brk_p = doc.add_paragraph()
    brk_run = brk_p.add_run()
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    brk_run._r.append(br)

    if first is not None:
        for p in (title_p, toc_p, brk_p):
            first._p.addprevious(p._p)
    else:
        for p in (title_p, toc_p, brk_p):
            body.append(p._p)


def main() -> int:
    ap = argparse.ArgumentParser(description="给 docx 插入 TOC 目录域")
    ap.add_argument("--docx", required=True, help="输入 .docx 路径")
    ap.add_argument("--out", default=None, help="输出路径，默认 <原名>_带目录.docx")
    ap.add_argument("--levels", type=int, default=3, help="目录收录到第几级标题，默认 3")
    ap.add_argument("--title", default="目　录", help="目录页标题文字")
    ap.add_argument("--auto-style", action="store_true",
                    help="文档没用标题样式时，按 1/1.1/1.1.1 编号自动套上")
    ap.add_argument("--dry-run", action="store_true", help="只体检不写文件")
    args = ap.parse_args()

    src = Path(args.docx).resolve()
    if not src.exists():
        print(f"[err] 文件不存在: {src}", file=sys.stderr)
        return 2
    if src.suffix.lower() != ".docx":
        print(f"[err] 只支持 .docx（.doc 请先另存为 .docx）: {src}", file=sys.stderr)
        return 2

    doc = docx.Document(str(src))
    styled, candidates = detect_headings(doc)

    print(f"[体检] 已使用标题样式的段落: {styled} 个")
    print(f"[体检] 按编号识别到的疑似标题: {len(candidates)} 个")
    for p, lv in candidates[:10]:
        print(f"         L{lv}  {p.text.strip()[:40]}")
    if len(candidates) > 10:
        print(f"         ...(共 {len(candidates)} 个)")

    if styled == 0 and not candidates:
        print("[warn] 既没有标题样式，也认不出编号标题 —— 插了目录也会是空的。")
        print("       请先在 Word 里给章节标题套上「标题 1/2/3」样式，再跑一次。")
        if not args.dry_run:
            return 3

    if args.auto_style and candidates:
        n = apply_heading_styles(doc, candidates, args.levels)
        print(f"[处理] 已为 {n} 个段落套上标题样式")
    elif styled == 0 and candidates:
        print("[提示] 检测到编号标题但没套样式，加 --auto-style 可自动套。")

    if args.dry_run:
        print("[dry-run] 未写出文件")
        return 0

    out = Path(args.out).resolve() if args.out else src.with_name(f"{src.stem}_带目录.docx")
    insert_toc(doc, args.levels, args.title)
    doc.save(str(out))
    print(f"[完成] 已写出: {out}")
    print("[下一步] 用 Word 打开 → 弹窗选「是」更新域；")
    print("         或 Ctrl+A 全选后按 F9。不更新的话页码是空的，这是正常现象。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
