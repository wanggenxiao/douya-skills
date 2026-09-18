#!/usr/bin/env python3
"""
MinerU PDF → Markdown 包装脚本（双接口自适应：MinerU 2.x / 4.x 都能跑）

★ 为什么要自适应（2026-09-18）：
  MinerU 4.0 把 CLI 彻底改了，2.x 的 `mineru -p X -o DIR -b pipeline` 在 4.0 上完全无效。
  而豆芽本机装的还是 2.x、学员机器装的是 4.0 —— 这个 skill 要分发给学员，
  所以不能简单替换成 4.0 接口，否则改完这边就跑不了了。
  本脚本【探测装的是哪个版本，自动走对应命令】，对外参数保持不变。

两版接口差异（据官方 README 核实，不是猜的）：
  |  概念    | 2.x                      | 4.x                                  |
  |----------|--------------------------|--------------------------------------|
  | 命令名   | mineru                   | mineru 或 mineru-kit（互为别名）      |
  | 子命令   | 无                       | parse                                |
  | 输入     | -p <pdf>                 | 位置参数 <pdf>                        |
  | 输出     | -o <目录>                | -o <文件.md>   ← ★ 语义变了          |
  | 质量     | -b pipeline/vlm-*        | --tier flash/basic/standard/advanced |
  | 页范围   | -s 0 -e 49（0-based）    | --pages 1-50（1-based 闭区间）★      |
  | 语言     | -l ch                    | 无（自动判定）                        |
  | 解析方式 | -m auto/txt/ocr          | 无（并入 tier）                       |

⚠ 官方 README 里【没有】--ocr-mode 这个参数。若看到有人这么用，先核实再抄。
⚠ 官方明确提示：不要拿 flash 当最终阅读质量（flash 只适合检索/建索引）。

用法（两版通用，参数没变）：
    python run_mineru.py --pdf "C:/x.pdf" [--out DIR] [--tier standard] [--start 0 --end 49]
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIERS = ["flash", "basic", "standard", "advanced"]


def find_cli():
    """定位 mineru 可执行文件。
    优先级：skill 自带 venv（豆芽本机 2.x）→ PATH 里的 mineru-kit → PATH 里的 mineru（学员机 4.x）。
    """
    skill_root = Path(__file__).resolve().parent.parent
    venv_exe = skill_root / "venv" / "Scripts" / "mineru.exe"
    if venv_exe.exists():
        return str(venv_exe)
    for name in ("mineru-kit", "mineru"):
        p = shutil.which(name)
        if p:
            return p
    return None


def detect_major(exe: str) -> int:
    """探测主版本：4 = 有 parse 子命令的新 CLI；2 = 老的 -p/-o/-b 接口。
    判据用 --help 文本，不额外跑解析，开销很小。
    """
    try:
        r = subprocess.run([exe, "--help"], capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        txt = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        print(f"[warn] 探测版本失败({e})，按 2.x 处理", file=sys.stderr)
        return 2
    # 4.x 的顶层 help 会列出 parse / read / search 等子命令
    if re.search(r"^\s*parse\b", txt, re.M) or "--tier" in txt:
        return 4
    # 2.x 的 help 里有 -p/--path 这种顶层选项
    if re.search(r"-p\b|--path\b", txt):
        return 2
    # 拿不准就再试一次 `parse --help`
    try:
        r2 = subprocess.run([exe, "parse", "--help"], capture_output=True, text=True,
                            timeout=60, encoding="utf-8", errors="replace")
        if r2.returncode == 0 and ("--tier" in (r2.stdout or "") or "--pages" in (r2.stdout or "")):
            return 4
    except Exception:
        pass
    return 2


def build_cmd_v2(exe, pdf, out_dir, a):
    """MinerU 2.x：-p 输入 / -o 输出目录 / -b 后端 / -s -e 0-based。"""
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
    return cmd


def build_cmd_v4(exe, pdf, out_dir, a):
    """MinerU 4.x：parse 子命令 / 位置参数输入 / -o 是【文件】/ --tier / --pages 1-based。"""
    md_out = out_dir / f"{pdf.stem}.md"
    cmd = [exe, "parse", str(pdf), "-o", str(md_out), "--tier", a.tier]
    # 页范围：2.x 的 0-based 闭区间 → 4.x 的 1-based 闭区间
    if a.start is not None or a.end is not None:
        s = (a.start or 0) + 1
        e = str(a.end + 1) if a.end is not None else "r1"   # r1 = 最后一页
        cmd += ["--pages", f"{s}-{e}"]
    # 4.x 无 -l/-m/-f/-t：质量统一由 tier 决定
    dropped = [n for n, v in (("--lang", a.lang != "ch"), ("--method", a.method != "auto"),
                              ("--no-formula", a.no_formula), ("--no-table", a.no_table),
                              ("--backend", a.backend != "pipeline")) if v]
    if dropped:
        print(f"[warn] MinerU 4.x 不支持这些参数，已忽略：{' '.join(dropped)}"
              f"（4.x 的解析质量统一由 --tier 控制）", file=sys.stderr)
    return cmd, md_out


def main() -> int:
    ap = argparse.ArgumentParser(description="MinerU PDF → Markdown（2.x / 4.x 自适应）")
    ap.add_argument("--pdf", required=True, help="PDF 绝对路径")
    ap.add_argument("--out", default=None, help="输出目录，默认 <PDF父目录>/<PDF名>_mineru/")
    ap.add_argument("--tier", default="standard", choices=TIERS,
                    help="【4.x】解析质量，默认 standard。规范/图纸这类难文档用 advanced；"
                         "flash 只适合建索引，别拿来当最终稿")
    ap.add_argument("--backend", default="pipeline",
                    choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                    help="【2.x】解析后端；4.x 已废弃，改用 --tier")
    ap.add_argument("--lang", default="ch", help="【2.x】OCR 语言，默认 ch；4.x 自动判定")
    ap.add_argument("--method", default="auto", choices=["auto", "txt", "ocr"],
                    help="【2.x】解析方式；4.x 已并入 --tier")
    ap.add_argument("--start", type=int, default=None, help="起始页 0-based（4.x 会自动 +1 转成 1-based）")
    ap.add_argument("--end", type=int, default=None, help="结束页 0-based 含（4.x 自动转换）")
    ap.add_argument("--no-formula", action="store_true", help="【2.x】关闭公式识别")
    ap.add_argument("--no-table", action="store_true", help="【2.x】关闭表格识别")
    ap.add_argument("--force-major", type=int, choices=[2, 4], default=None,
                    help="跳过自动探测，强制按该主版本构造命令（排障用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要执行的命令，不真的跑。换版本/排障时先用这个核对参数")
    a = ap.parse_args()

    pdf = Path(a.pdf).resolve()
    if not pdf.exists():
        print(f"[err] PDF 不存在: {pdf}", file=sys.stderr)
        return 2
    if pdf.suffix.lower() != ".pdf":
        print(f"[err] 不是 PDF: {pdf}", file=sys.stderr)
        return 2

    out_dir = Path(a.out).resolve() if a.out else pdf.parent / f"{pdf.stem}_mineru"
    out_dir.mkdir(parents=True, exist_ok=True)

    exe = find_cli()
    if not exe:
        print("[err] 找不到 mineru / mineru-kit。请先装：pip install -U mineru", file=sys.stderr)
        return 3

    major = a.force_major or detect_major(exe)
    md_out = None
    if major == 4:
        cmd, md_out = build_cmd_v4(exe, pdf, out_dir, a)
    else:
        cmd = build_cmd_v2(exe, pdf, out_dir, a)

    print(f"[info] CLI        : {exe}")
    print(f"[info] 检测版本   : MinerU {major}.x" + ("（--force-major 指定）" if a.force_major else "（自动探测）"))
    print(f"[info] PDF        : {pdf}")
    print(f"[info] 输出目录   : {out_dir}")
    print(f"[info] 质量       : " + (f"--tier {a.tier}" if major == 4 else f"-b {a.backend} -l {a.lang} -m {a.method}"))
    print(f"[info] 页范围     : {a.start}~{a.end if a.end is not None else 'end'}")
    print(f"[info] CMD        : {' '.join(cmd)}")
    print()

    if a.dry_run:
        print("[dry-run] 未执行。确认上面这条命令无误后，去掉 --dry-run 重跑。")
        return 0

    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"[err] mineru 退出码 {rc}", file=sys.stderr)
        if major == 4:
            print("[hint] 若报未知参数，用 --force-major 2 再试一次，并回报给豆芽更新本脚本", file=sys.stderr)
        return rc

    mds = list(out_dir.rglob("*.md"))
    if mds:
        print(f"\n[ok] 生成 {len(mds)} 个 markdown:")
        for m in mds:
            print(f"     {m}")
    else:
        print("[warn] 未找到 .md 产物，请检查 mineru 日志", file=sys.stderr)
        if md_out:
            print(f"[warn] 4.x 预期输出: {md_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
