#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MinerU 转录校核脚本 —— 转完必跑，不是可选项。

原理: MinerU 的错是"看上去完全正常的错", 读 md 本身检不出来。
      必须引入独立于 MinerU 的第二数据源(pdfplumber/pypdfium2 直抽)做对照。

必须用校核专用 venv 执行(与 mineru venv 隔离, 依赖冲突):
    <本skill目录>/verify_venv/Scripts/python.exe
    （安装见上层目录 README.md；注意是 verify_venv，不是跑 MinerU 那个 venv）

产出: <md同目录>/_check_报告.md  + (可选)可疑页 PNG
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ"
CMP = "≤≥＜＞<>≦≧"
UNITS = "°‰℃㎡㎥±×÷√∅φΦ"

# 明显的 OCR 事故特征
GLITCH = [
    (r"\d\.\.\d", "小数点重复(2..5)"),
    (r"[IⅠ]{3,}", "罗马数字堆叠"),
    (r"[\ufffd]", "Unicode 替换字符"),
]


def _is_toc_page(lines) -> bool:
    """目录页: 多数行以页码收尾。那些页码不是内容, 比对它们纯属自找假警报。"""
    if len(lines) < 6:
        return False
    # 行可能被坐标聚类切碎(标题与页码分行), 所以数点线而不是看行尾
    return sum(1 for ln in lines if re.search(r"[.·…]{3,}", ln)) >= 5


def _words_to_lines(pg):
    """按词提取再按行归并。不能用 extract_text: 它按行直接拼接,
    表格里相邻单元格的数字会粘成 '110.0113' 这种不存在的假数字。"""
    rows = {}
    for w in pg.extract_words():
        rows.setdefault(round(w["top"] / 3), []).append(w)
    lines = []
    for k in sorted(rows):
        ws = sorted(rows[k], key=lambda x: x["x0"])
        lines.append(" ".join(x["text"] for x in ws))
    return lines


def _fmt(pages, cap=25):
    """页码清单封顶, 免得 441 页文档打印 300 个页码"""
    if not pages:
        return "无"
    if len(pages) <= cap:
        return str(pages)
    return f"{pages[:cap]} …另有 {len(pages) - cap} 页(见报告同目录 _check_报告.md 完整清单)"


def _is_running_head(line: str) -> bool:
    """页眉/页脚判定: 短行且主体是页码或规范号"""
    s = line.strip()
    if not s or len(s) > 24:
        return False
    return bool(re.fullmatch(r"[-—\s]*\d{1,4}[-—\s]*", s) or
                re.fullmatch(r"[A-Z]{1,4}[\s/]?\d{3,5}[\s—-]*\d{0,4}", s))


def pdf_pages_text(pdf: Path):
    """独立第二数据源: 逐页抽文本层"""
    import pdfplumber
    out = []
    with pdfplumber.open(str(pdf)) as doc:
        for pg in doc.pages:
            lines = _words_to_lines(pg)
            # MinerU 默认剥除页眉页脚, 这边不剥就会每页假报"缺页码"
            while lines and _is_running_head(lines[0]):
                lines.pop(0)
            while lines and _is_running_head(lines[-1]):
                lines.pop()
            # 目录页: 形如 "x.x 标题......109" 的点线页码, 同样不是内容
            lines = [re.sub(r"[.·…]{3,}\s*\d+\s*$", "", ln) for ln in lines]
            out.append("" if _is_toc_page(lines) else "\n".join(lines))
    return out


def pdf_pages_tables(pdf: Path):
    """独立抽表格结构: 每页 [(行数, 最大列数), ...]"""
    import pdfplumber
    out = []
    with pdfplumber.open(str(pdf)) as doc:
        for pg in doc.pages:
            shapes = []
            for t in (pg.extract_tables() or []):
                if not t:
                    continue
                shapes.append((len(t), max(len(r) for r in t)))
            out.append(shapes)
    return out


LATEX_MAP = {
    r"\\times": "×", r"\\div": "÷", r"\\sqrt": "√", r"\\pm": "±",
    r"\\geq": "≥", r"\\ge": "≥", r"\\leq": "≤", r"\\le": "≤",
    r"\\circ": "°", r"\\phi": "φ", r"\\varphi": "φ", r"\\Phi": "Φ",
    r"\\alpha": "α", r"\\beta": "β", r"\\gamma": "γ", r"\\sigma": "σ",
    r"\\sum": "∑", r"\\int": "∫", r"\\permil": "‰",
}


def normalize_md(md: str) -> str:
    """把 md 里的 LaTeX 拉回可比形态, 否则公式密集文档会满屏误报。
    MinerU 会把 ×→\times, 1.90→'1 . 9 0', 必须先归一化再做 token 对照。"""
    for k, v in LATEX_MAP.items():
        md = re.sub(k + r"(?![a-zA-Z])", v, md)
    md = re.sub(r"[\\{}$^_]", "", md)          # 去 LaTeX 结构符
    md = re.sub(r"(?<=\d)\s+(?=[.\d])", "", md)  # '1 . 9 0' -> '1.90'
    md = re.sub(r"(?<=\.)\s+(?=\d)", "", md)
    return md


FULLWIDTH = str.maketrans("＜＞＝（），．０１２３４５６７８９",
                          "<>=(),.0123456789")


def tokens(text: str) -> Counter:
    """高危 token 多重集: 数字 / 比较符 / 罗马数字 / 单位符号"""
    text = text.translate(FULLWIDTH)
    c = Counter()
    for n in re.findall(r"\d+(?:\.\d+)?", text):
        c["num:" + n] += 1
    for ch in text:
        if ch in CMP:
            c["cmp:" + ch] += 1
        elif ch in ROMAN:
            c["rom:" + ch] += 1
        elif ch in UNITS:
            c["unit:" + ch] += 1
    return c


def md_table_shapes(md: str):
    """MinerU 输出的 HTML 表 -> [(行数, 最大列数), ...]"""
    shapes = []
    for tb in re.findall(r"<table.*?</table>", md, re.S):
        rows = re.findall(r"<tr.*?</tr>", tb, re.S)
        if not rows:
            continue
        widths = []
        for r in rows:
            w = 0
            for cell in re.findall(r"<t[dh]\b([^>]*)>", r):
                m = re.search(r'colspan\s*=\s*"?(\d+)', cell)
                w += int(m.group(1)) if m else 1
            widths.append(w)
        shapes.append((len(rows), max(widths) if widths else 0))
    return shapes


def render_pages(pdf: Path, pages, outdir: Path, scale=3.0):
    """把可疑页渲染成 PNG 供肉眼核对; 严格 <=1600px(全局铁律)"""
    import pypdfium2 as pdfium
    from PIL import Image
    outdir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    made = []
    for p in pages:
        img = doc[p - 1].render(scale=scale).to_pil()
        if max(img.size) > 1600:
            img.thumbnail((1600, 1600), Image.LANCZOS)
        fp = outdir / f"p{p:03d}.png"
        img.save(fp)
        made.append(fp)
    return made


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="MinerU 转录校核(转完必跑)")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--md", required=True, help="MinerU 产出的 .md")
    ap.add_argument("--md2", default=None, help="第二后端产出的 .md, 做交叉比对(扫描件必给)")
    ap.add_argument("--render", action="store_true", help="把必看页渲染成 PNG")
    args = ap.parse_args()

    pdf = Path(args.pdf).resolve()
    md_path = Path(args.md).resolve()
    if not pdf.exists() or not md_path.exists():
        print("[err] pdf 或 md 不存在", file=sys.stderr)
        return 2
    md = md_path.read_text(encoding="utf-8", errors="replace")

    pages = pdf_pages_text(pdf)
    npages = len(pages)
    chars = sum(len(t) for t in pages)
    scanned = chars < npages * 20          # 无文本层 => 扫描件
    R = ["# MinerU 转录校核报告", "",
         f"- 源 PDF: `{pdf}`", f"- 转录 md: `{md_path}`",
         f"- PDF 页数: **{npages}**", f"- PDF 文本层字符数: {chars}",
         f"- 判定: **{'扫描件(无文本层)' if scanned else '有文本层'}**", ""]
    must_see, red = set(), []
    see_b, see_c = set(), set()   # B档含表页 / C档公式页

    # ---- 检查1 体检 ----
    R += ["## 1 体检"]
    imgs = list(md_path.parent.glob("images/*"))
    R.append(f"- md 字符数: {len(md)}")
    R.append(f"- 抽出图片: {len(imgs)} 张")
    if len(md) < 50:
        red.append("md 几乎为空")
    ratio = len(md) / chars if chars else 0
    if not scanned:
        R.append(f"- md/PDF 文本量比: {ratio:.2f} (正常 0.8~1.4)")
        if ratio < 0.7:
            red.append(f"md 文本量仅为 PDF 的 {ratio:.0%}, 疑似整块内容丢失")
    for pat, name in GLITCH:
        hits = re.findall(pat, md)
        if hits:
            red.append(f"OCR 事故特征[{name}] 命中 {len(hits)} 处: {hits[:5]}")
    R.append("")
    # ---- 检查2 高危 token 逐页对照 ----
    R += ["## 2 高危 token 对照(数字/比较符/罗马数字/单位)"]
    if scanned:
        R.append("- 跳过: 无文本层, 无第二数据源可比 -> 必须靠检查3双后端交叉")
    else:
        md_tok = tokens(normalize_md(md))
        for i, ptxt in enumerate(pages, 1):
            miss = []
            for k, v in tokens(ptxt).items():
                if md_tok[k] < v:
                    miss.append(f"{k.split(':', 1)[1]}(缺{v - md_tok[k]})")
            if len(miss) >= 8:
                must_see.add(i)
                R.append(f"- ⚠ 第 {i} 页 数值密集(缺 {len(miss)} 项): 该页多为表格/数据,"
                         " PDF 侧相邻数字易粘连, **逐项对照不可靠 -> 整页人工看**")
            elif miss:
                must_see.add(i)
                R.append(f"- ⚠ 第 {i} 页 md 中缺失: {', '.join(miss[:12])}")
        if not must_see:
            R.append("- ✅ 全部高危 token 在 md 中均能找到, 无缺失")
    R.append("")

    # ---- 检查3 双后端交叉 ----
    R += ["## 3 双后端交叉比对"]
    if args.md2:
        md2 = Path(args.md2).read_text(encoding="utf-8", errors="replace")
        a, b = tokens(normalize_md(md)), tokens(normalize_md(md2))
        diff = [(k, a[k], b[k]) for k in set(a) | set(b) if a[k] != b[k]]
        if diff:
            R.append(f"- ⚠ 两后端 token 分歧 {len(diff)} 处(必须回原页定夺):")
            for k, x, y in sorted(diff)[:30]:
                R.append(f"    - `{k}` 本次={x} 另一后端={y}")
        else:
            R.append("- ✅ 两后端 token 完全一致")
    else:
        R.append("- 未提供 --md2" + ("  **扫描件必须补跑另一后端**" if scanned else " (有文本层, 可选)"))
    R.append("")
    # ---- 检查4 表格结构对照(唯一能抓合并单元格错位的) ----
    R += ["## 4 表格结构对照", "",
          "> ★★★ 合并单元格错位: 文字全对、归属关系错, 检查2/3 抓不到, 只能靠结构+肉眼。"]
    pdf_tabs = [] if scanned else pdf_pages_tables(pdf)
    pdf_flat = [(i + 1, s) for i, ss in enumerate(pdf_tabs) for s in ss]
    md_tabs = md_table_shapes(md)
    R.append(f"- PDF 侧(pdfplumber)检出表格: {len(pdf_flat)} 个")
    R.append(f"- md 侧(MinerU)输出表格: {len(md_tabs)} 个")
    if len(pdf_flat) == 0 and md_tabs and not scanned:
        R.append("- ℹ PDF 侧检出 0 个表: pdfplumber 只认框线表, 无框线/公式排版表它看不见;"
                 " 不判红灯, 但这些表**无结构可比, 必须肉眼核对原页**")
        (must_see if npages <= 30 else see_b).update(range(1, npages + 1))
    elif len(pdf_flat) != len(md_tabs) and not scanned:
        red.append(f"表格数量不符: PDF {len(pdf_flat)} vs md {len(md_tabs)}")
    if pdf_flat and md_tabs and len(pdf_flat) != len(md_tabs):
        R.append("- ⚠ 两侧表格数不等, **按顺序逐表配对会全部错位**, 已跳过逐表比对。")
        R.append(f"    - PDF 侧形状: {[s for _, s in pdf_flat][:15]}…")
        R.append(f"    - md  侧形状: {md_tabs[:15]}…")
        R.append("    - 差额多半是 MinerU 把版式/公式块误判成表, 或 pdfplumber 漏掉无框线表;"
                 " **两种都要人工看**。")
        must_see.update(pg for pg, _ in pdf_flat)
        pdf_flat = []
    for idx, ((pg, ps), ms) in enumerate(zip(pdf_flat, md_tabs), 1):
        flag = "✅" if ps == ms else "⚠"
        if ps != ms:
            must_see.add(pg)
        R.append(f"  - {flag} 表{idx}(P{pg}) 行×列 PDF={ps} md={ms}")
    for pg, _ in pdf_flat:
        see_b.add(pg)
    R.append("")

    # ---- 检查5 公式页 ----
    R += ["## 5 公式(无自动化手段, 只能看原页)"]
    fpages = set()
    for i, t in enumerate(pages, 1):
        if re.search(r"[=＝].*[a-zA-Zαβγφσ]|[∑∫√]", t):
            fpages.add(i)
    nlatex = len(re.findall(r"\$\$?[^$]+\$\$?", md))
    R.append(f"- md 中 LaTeX 片段: {nlatex} 处; PDF 疑似含公式页: {sorted(fpages) or '—'}")
    if nlatex or fpages:
        see_c |= fpages
        R.append("- ⚠ LaTeX 正确性无法自动验证, 凡要引用公式必须核对原页")
    R.append("")
    # ---- 结论 ----
    R += ["## 结论"]
    if red:
        R.append("### 🔴 红灯(必须处理)")
        R += [f"- {x}" for x in red]
    see_b -= must_see
    see_c -= (must_see | see_b)
    ms = sorted(must_see)
    R.append("")
    R.append("### 必看原页清单（分三档，A 档优先）")
    R.append("")
    R.append(f"- **A 档 必看**（{len(ms)} 页）——自动检查已报异常: {_fmt(ms)}")
    R.append(f"- B 档 含表格（{len(see_b)} 页）——结构比对通过≠归属正确,"
             f" 要引用表内数据就得看: {_fmt(sorted(see_b))}")
    R.append(f"- C 档 含公式（{len(see_c)} 页）——要引用公式才看: {_fmt(sorted(see_c))}")
    if len(ms) + len(see_b) + len(see_c) > npages * 0.5:
        R.append("")
        R.append("> ⚠ 清单覆盖过半篇幅，说明这份文档**整体不适合盲信转录**。"
                 "别指望逐页核完——改成**按需核**：用到哪一页的表/公式，"
                 "当场回原页确认那一页，其余明确标注「仅据转录、待核」。")
    R.append("")
    R.append("**规则: 凡要引用表格或公式的内容, 必须先看上列原页, 无例外。**")
    R.append("下游结论逐条标注「已回原件核实」或「仅据转录、待核」。")

    if args.render and ms:
        pngs = render_pages(pdf, ms[:20], md_path.parent / "_check_pages")
        R += ["", "### 已渲染原页(供肉眼核对)"] + [f"- `{p}`" for p in pngs]

    rep = md_path.parent / "_check_报告.md"
    rep.write_text("\n".join(R), encoding="utf-8")
    print("\n".join(R))
    print(f"\n[ok] 校核报告: {rep}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
