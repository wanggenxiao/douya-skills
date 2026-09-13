#!/usr/bin/env python3
"""
MinerU PDF → Markdown 包装脚本

调用 venv 内的 mineru CLI，把 200 页限制改成 99999，提供合理默认值。
本脚本需要用装了 mineru 的 python 执行。两种装法都支持：
  A. 装进本 skill 目录下的 venv/  ->  venv/Scripts/python.exe run_mineru.py ...
  B. 装进你自己的 Python 环境     ->  python run_mineru.py ...  (脚本会退回 PATH 找 mineru)
安装步骤见上层目录的 README.md
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="MinerU PDF → Markdown (local CLI, no 200-page limit)")
    ap.add_argument("--pdf", required=True, help="PDF 绝对路径")
    ap.add_argument("--out", default=None, help="输出目录，默认 <PDF父目录>/<PDF名>_mineru/")
    ap.add_argument("--backend", default="pipeline",
                    choices=["pipeline", "vlm-auto-engine", "hybrid-auto-engine"],
                    help="解析后端（默认 pipeline；vlm/hybrid-auto-engine 走 GPU 视觉大模型，精度更高但首跑要下 2GB 模型）")
    ap.add_argument("--lang", default="ch",
                    choices=["ch", "ch_server", "ch_lite", "en", "korean", "japan",
                             "chinese_cht", "ta", "te", "ka", "th", "el", "latin",
                             "arabic", "east_slavic", "cyrillic", "devanagari"],
                    help="OCR 语言，默认 ch（中英混合）；仅 pipeline/hybrid 后端生效")
    ap.add_argument("--method", default="auto", choices=["auto", "txt", "ocr"],
                    help="解析方式（默认 auto）；仅 pipeline/hybrid 后端生效")
    ap.add_argument("--start", type=int, default=None, help="起始页 0-based")
    ap.add_argument("--end", type=int, default=None, help="结束页 0-based（含）")
    ap.add_argument("--no-formula", action="store_true", help="关闭公式识别（默认开）")
    ap.add_argument("--no-table", action="store_true", help="关闭表格识别（默认开）")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"[err] PDF 不存在: {pdf_path}", file=sys.stderr)
        return 2
    if pdf_path.suffix.lower() != ".pdf":
        print(f"[err] 不是 PDF: {pdf_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out).resolve() if args.out else pdf_path.parent / f"{pdf_path.stem}_mineru"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 定位 venv 的 mineru.exe
    skill_root = Path(__file__).resolve().parent.parent
    mineru_exe = skill_root / "venv" / "Scripts" / "mineru.exe"
    if not mineru_exe.exists():
        # 兜底：交给 PATH
        mineru_exe_str = shutil.which("mineru") or "mineru"
    else:
        mineru_exe_str = str(mineru_exe)

    cmd = [
        mineru_exe_str,
        "-p", str(pdf_path),
        "-o", str(out_dir),
        "-b", args.backend,
    ]
    # lang/method 仅在 pipeline 或 hybrid-* 后端有效
    if args.backend.startswith("pipeline") or args.backend.startswith("hybrid"):
        cmd += ["-l", args.lang, "-m", args.method]
    if args.start is not None:
        cmd += ["-s", str(args.start)]
    if args.end is not None:
        cmd += ["-e", str(args.end)]
    if args.no_formula:
        cmd += ["-f", "False"]
    if args.no_table:
        cmd += ["-t", "False"]

    print(f"[info] PDF        : {pdf_path}")
    print(f"[info] Output dir : {out_dir}")
    print(f"[info] Backend    : {args.backend}")
    print(f"[info] Lang       : {args.lang}")
    print(f"[info] Page range : {args.start}~{args.end if args.end is not None else 'end'}")
    print(f"[info] CMD        : {' '.join(cmd)}")
    print()

    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"[err] mineru 退出码 {rc}", file=sys.stderr)
        return rc

    # 找产物
    md_files = list(out_dir.rglob("*.md"))
    if md_files:
        print()
        print(f"[ok] 生成 {len(md_files)} 个 markdown:")
        for m in md_files:
            print(f"     {m}")
    else:
        print("[warn] 未找到 .md 产物，请检查 mineru 日志")

    return 0


if __name__ == "__main__":
    sys.exit(main())
