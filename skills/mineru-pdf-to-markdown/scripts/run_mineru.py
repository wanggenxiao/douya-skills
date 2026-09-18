#!/usr/bin/env python3
"""
MinerU PDF → Markdown 包装脚本（MinerU 4.x / 3.x·2.x 双接口自适应）

★ 2026-09-18 实测重写。以下全部是【在本机装了 4.0.2 跑出来的结论】，不是照文档猜的：

1) ★★ `mineru` 和 `mineru-kit` 是两套【不同的 CLI】，不是别名（官方 README 说 both valid，误导）
   - `mineru parse`      → 新"个人文档中心"，走 doclib + 服务架构：
                           必须先 `mineru server start`，且默认 parse_server.local.mode=disabled，
                           basic/standard/advanced 会直接报 "Local parse-server is disabled"。
                           **它的 --pages 默认只转前 10 页**（静默截断！）
   - `mineru-kit parse`  → 传统解析器，【本 skill 用这个】：
                           不需要起服务、不需要改配置、开箱即用，
                           **--pages 默认转全部页面**。
   → 同名子命令、默认值相反，查文档时务必看清是哪一个。

2) 4.x 的 `mineru-kit parse` 真实参数（--help 实测）：
   inputs(位置参数，可传文件或目录) / -o --output(必填，可传目录，会自动拼 <名>.md)
   / --tier flash|basic|standard|advanced / -p --pages '1-5,8,r3-r1'|'all'(默认 all)
   / -f --format markdown|middle_json|zip / --ocr-mode auto|txt|ocr
   / --remote --remote-url --api-key / --disable-image-analysis
   ⚠ 没有 --force（那是 `mineru parse` 的）。

3) ★ 4.x 把图片【base64 内嵌进 md】，而 2.x/3.x 是抽到 images/ 子目录用相对路径引用。
   实测 13 页文档 → md 4.1MB，其中正文仅 2949 字，其余全是 base64。
   `--disable-image-analysis` 关不掉。对"转出来喂给 AI 读"这个用途是倒退（白烧 token）。
   → 本脚本跑完【自动剥离 base64】，把图存成 images/*.png，md 改回相对路径引用。
     要保留内嵌图，加 --keep-base64。

4) 首次用 --tier standard/advanced 会自动下模型（实测约 4~5 分钟），一次性。

两版接口对照：
   | 概念   | 2.x/3.x              | 4.x (mineru-kit)                |
   | 命令   | mineru               | mineru-kit parse                |
   | 输入   | -p <pdf>             | 位置参数                         |
   | 输出   | -o <目录>            | -o <目录>（同样给目录即可）      |
   | 质量   | -b pipeline/vlm-*    | --tier flash|basic|standard|advanced |
   | 页范围 | -s 0 -e 49 (0-based) | --pages 1-50 (1-based 闭区间)   |
   | 语言   | -l ch                | 无（自动）                       |
   | 解析法 | -m auto|txt|ocr      | --ocr-mode auto|txt|ocr         |
"""

import argparse
import base64
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIERS = ["flash", "basic", "standard", "advanced"]
B64_RE = re.compile(r'!\[([^\]]*)\]\(\s*data:image/([A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+?)\s*\)')


def find_cli(want: int = None):
    """返回 (可执行路径, 主版本)。判据：有 mineru-kit ⇒ 4.x（实测 3.1.14 的 venv 里没有它）。

    want=2/4 时只找该版本对应的可执行文件——⚠ 不能只改版本号不换 exe，
    否则 --force-major 2 会拿 mineru-kit 去跑老参数，必挂（2026-09-18 dry-run 抓到过）。
    """
    skill = Path(__file__).resolve().parent.parent
    kits, olds = [], []

    def collect(kit: Path, old: Path):
        """⚠ 同目录下有 mineru-kit 的 mineru.exe 属于 4.x 那套「文档中心」CLI，
        它跑不了 -p/-o/-b 老参数，绝不能收进 olds。"""
        if kit.exists():
            kits.append(str(kit))
        if old.exists() and not kit.exists():
            olds.append(str(old))

    # 本机可能同时存在 venv(旧 3.x) 和 venv4(新 4.x)，优先新的
    for vd in ("venv4", "venv"):
        d = skill / vd / "Scripts"
        collect(d / "mineru-kit.exe", d / "mineru.exe")
    w = shutil.which("mineru-kit")
    if w:
        kits.append(w)
        if not kits[:-1]:                       # PATH 上是 4.x 环境
            pass
    w2 = shutil.which("mineru")
    if w2 and not w:                            # PATH 上没有 mineru-kit 才算老版
        olds.append(w2)

    if want == 4:
        return (kits[0], 4) if kits else (None, 0)
    if want == 2:
        # 老接口只能用 mineru.exe；4.x 环境里的 mineru.exe 是"文档中心"那套，跑不了 -p/-o/-b
        return (olds[0], 2) if olds else (None, 0)
    if kits:
        return kits[0], 4
    if olds:
        return olds[0], 2
    return None, 0


def strip_base64(md_path: Path) -> tuple:
    """把 md 里内嵌的 base64 图片抽成 images/*.png，正文改相对路径。返回 (抽出张数, 省下字节)。"""
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    if "base64," not in raw:
        return 0, 0
    before = len(raw.encode("utf-8"))
    img_dir = md_path.parent / "images"
    seen, n = {}, 0

    def repl(m):
        nonlocal n
        alt, ext, data = m.group(1), m.group(2).lower(), re.sub(r"\s+", "", m.group(3))
        try:
            blob = base64.b64decode(data)
        except Exception:
            return m.group(0)                      # 解不开就原样留着，绝不丢内容
        key = hashlib.md5(blob).hexdigest()
        if key in seen:                            # 同图去重
            name = seen[key]
        else:
            n += 1
            ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(ext, ext)
            name = f"img_{n:03d}.{ext}"
            img_dir.mkdir(exist_ok=True)
            (img_dir / name).write_bytes(blob)
            seen[key] = name
        return f"![{alt}](images/{name})"

    new = B64_RE.sub(repl, raw)
    if n:
        md_path.write_text(new, encoding="utf-8")
    return n, before - len(new.encode("utf-8"))


def build_v4(exe, pdf, out_dir, a):
    """4.x: mineru-kit parse <pdf> -o <目录> --tier X [--pages A-B] [--ocr-mode Y]"""
    cmd = [exe, "parse", str(pdf), "-o", str(out_dir), "--tier", a.tier]
    if a.start is not None or a.end is not None:
        s = (a.start or 0) + 1                       # 0-based → 1-based
        e = str(a.end + 1) if a.end is not None else "r1"
        cmd += ["--pages", f"{s}-{e}"]
    # 不传页范围就不加 --pages：mineru-kit 默认就是全部页面
    if a.method != "auto":
        cmd += ["--ocr-mode", a.method]
    if a.no_image_analysis:
        cmd += ["--disable-image-analysis"]
    dropped = [n for n, v in (("--backend", a.backend != "pipeline"),
                              ("--lang", a.lang != "ch"),
                              ("--no-formula", a.no_formula),
                              ("--no-table", a.no_table)) if v]
    if dropped:
        print(f"[warn] 4.x 不支持 {' '.join(dropped)}，已忽略（质量统一由 --tier 控制）", file=sys.stderr)
    return cmd


def build_v2(exe, pdf, out_dir, a):
    """2.x/3.x: mineru -p <pdf> -o <目录> -b <backend> ..."""
    cmd = [exe, "-p", str(pdf), "-o", str(out_dir), "-b", a.backend]
    if a.backend.startswith("pipeline") or a.backend.startswith("hybrid"):
        cmd += ["-l", a.lang, "-m", a.method]
    if a.start is not None:
        cmd += ["-s", str(a.start)]
    if a.end is not None:
        cmd += ["-e", str(a.end)]
    if a.no_formula:
        cmd += ["-f", "False"]
    if a.no_table:
        cmd += ["-t", "False"]
    if a.tier != "standard":
        print("[warn] --tier 仅 4.x 生效，当前是 2.x/3.x，已忽略", file=sys.stderr)
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="MinerU PDF → Markdown（4.x / 3.x·2.x 自适应）")
    ap.add_argument("--pdf", required=True, help="PDF 绝对路径")
    ap.add_argument("--out", default=None, help="输出目录，默认 <PDF父目录>/<PDF名>_mineru/")
    ap.add_argument("--tier", default="standard", choices=TIERS,
                    help="【4.x】解析档位，默认 standard。国标规范/图纸这类难文档用 advanced；"
                         "flash 只适合建索引，别当最终稿。首次用 standard/advanced 会自动下模型(约4~5分钟)")
    ap.add_argument("--backend", default="pipeline",
                    choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                    help="【2.x/3.x】解析后端；4.x 已废弃，改用 --tier")
    ap.add_argument("--lang", default="ch", help="【2.x/3.x】OCR 语言，默认 ch；4.x 自动判定")
    ap.add_argument("--method", default="auto", choices=["auto", "txt", "ocr"],
                    help="解析方式。2.x/3.x → -m；4.x → --ocr-mode")
    ap.add_argument("--start", type=int, default=None, help="起始页 0-based（4.x 自动转 1-based）")
    ap.add_argument("--end", type=int, default=None, help="结束页 0-based 含（4.x 自动转换）")
    ap.add_argument("--no-formula", action="store_true", help="【2.x/3.x】关闭公式识别")
    ap.add_argument("--no-table", action="store_true", help="【2.x/3.x】关闭表格识别")
    ap.add_argument("--no-image-analysis", action="store_true", help="【4.x】禁用图像分析")
    ap.add_argument("--keep-base64", action="store_true",
                    help="【4.x】保留 md 里内嵌的 base64 图片（默认会剥离成 images/ 相对引用）")
    ap.add_argument("--force-major", type=int, choices=[2, 4], default=None, help="强制按该版本构造命令")
    ap.add_argument("--dry-run", action="store_true", help="只打印命令不执行")
    a = ap.parse_args()

    pdf = Path(a.pdf).resolve()
    if not pdf.exists() or pdf.suffix.lower() != ".pdf":
        print(f"[err] PDF 不存在或不是 PDF: {pdf}", file=sys.stderr)
        return 2

    out_dir = Path(a.out).resolve() if a.out else pdf.parent / f"{pdf.stem}_mineru"
    out_dir.mkdir(parents=True, exist_ok=True)

    exe, major = find_cli(a.force_major)
    if not exe:
        which = f"MinerU {a.force_major}.x 对应的" if a.force_major else ""
        print(f"[err] 找不到{which} mineru-kit / mineru。装法：pip install -U 'mineru[core]'", file=sys.stderr)
        return 3

    cmd = build_v4(exe, pdf, out_dir, a) if major == 4 else build_v2(exe, pdf, out_dir, a)

    print(f"[info] CLI      : {exe}")
    print(f"[info] 版本     : MinerU {'4.x' if major == 4 else '3.x/2.x'}"
          + ("（--force-major 指定）" if a.force_major else "（按有无 mineru-kit 判定）"))
    print(f"[info] PDF      : {pdf}")
    print(f"[info] 输出目录 : {out_dir}")
    print(f"[info] 质量     : " + (f"--tier {a.tier}" if major == 4 else f"-b {a.backend} -l {a.lang}"))
    print(f"[info] 页范围   : " + ("全部" if a.start is None and a.end is None
                                 else f"{a.start}~{a.end if a.end is not None else 'end'}（0-based）"))
    print(f"[info] CMD      : {' '.join(cmd)}\n")

    if a.dry_run:
        print("[dry-run] 未执行。")
        return 0

    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"[err] mineru 退出码 {rc}", file=sys.stderr)
        if major == 4:
            print("[hint] 若报 'Local parse-server is disabled'，说明误用了 `mineru parse`；"
                  "本脚本应调 mineru-kit。用 --dry-run 看实际命令。", file=sys.stderr)
        return rc

    mds = sorted(out_dir.rglob("*.md"))
    if not mds:
        print("[warn] 未找到 .md 产物，请检查上面的 mineru 日志", file=sys.stderr)
        return 0

    print(f"\n[ok] 生成 {len(mds)} 个 markdown:")
    for m in mds:
        line = f"     {m}  ({m.stat().st_size/1024:.0f}KB)"
        if major == 4 and not a.keep_base64:
            n, saved = strip_base64(m)
            if n:
                line += f"  → 抽出 {n} 张图到 images/，瘦身 {saved/1024/1024:.1f}MB，现 {m.stat().st_size/1024:.0f}KB"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
