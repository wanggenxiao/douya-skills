# -*- coding: utf-8 -*-
"""
骨架完整性校验 —— 专治「整块内容丢失」。

为什么需要它:
  转录丢内容时**不留任何痕迹**, md 读起来依然通顺, 人不会想到去核那一页。
  所以"按需核对"防不住丢失。但规范类文档有个好性质:
  条文号(4.1.1)/表号/图号**严格连续**, 断号=那段没了。纯机器可判, 零人工。

用法(校核专用 venv):
  verify_venv/Scripts/python.exe check_skeleton.py --md "<转录.md>" [--json 报告.json]

适用: 国标/行标/地标/规程等带层级条文编号的文档。普通图书/论文不适用。
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# 行首的条文号: "4.1.1 xxx" / "11.6.7" / "A.0.2"; 允许 md 标题符号和空格在前
# ⚠ MinerU 常把编号和正文连写成 "## 6.2.1正截面承载力…"(无空格),
#   所以结尾不能要求空格, 只排除后面还是数字/点/连字符的情况(那是 6.2.11 或公式号 6.2.1-1)
CLAUSE = re.compile(r"^[#>\s*]*((?:[A-Z]|\d{1,2})(?:\.\d{1,2}){1,3})(?![\d.\-])")
# 表/图号: 表 4.1.3-1, 图 11.6.7
CAPTION = re.compile(r"^[#>\s*]*([表图])\s*((?:[A-Z]|\d{1,2})(?:\.\d{1,2}){1,3}(?:-\d{1,2})?)")


def parse(md: str):
    clauses, captions = [], []
    for ln in md.splitlines():
        s = ln.strip()
        if not s:
            continue
        m = CAPTION.match(s)
        if m:
            captions.append((m.group(1), m.group(2)))
            continue
        m = CLAUSE.match(s)
        if m:
            clauses.append(m.group(1))
    return clauses, captions


def key_of(num: str):
    """'4.1.3' -> (('4','1'), 3)  末位是序号, 前面是父级"""
    parts = num.split(".")
    try:
        last = int(parts[-1])
    except ValueError:
        return None, None
    return tuple(parts[:-1]), last


def find_gaps(numbers, label):
    """按父级分组, 找断号。只报"中间缺失", 不报结尾缺失(无法判断原文到几)"""
    groups = defaultdict(set)
    for n in numbers:
        parent, last = key_of(n)
        if parent:
            groups[parent].add(last)
    issues = []
    for parent, seq in sorted(groups.items()):
        if len(seq) < 2:
            continue
        lo = min(seq)
        # 从 lo 起的最长连续段才可信。断口之后的零星大号多半是误抽:
        # MinerU 把编号与正文连写("11.9.2"+正文首字"8度")会粘成 "11.9.28"。
        run_end = lo
        while run_end + 1 in seq:
            run_end += 1
        outliers = sorted(x for x in seq if x > run_end + 2)
        if outliers and len(outliers) <= 2:
            issues.append({
                "type": label + "-疑似误抽", "parent": ".".join(parent),
                "text": f"{label} {'.'.join(parent)}.{outliers} 疑似误抽"
                        f"（该组连续到 {'.'.join(parent)}.{run_end} 就断了，"
                        f"多半是编号与正文连写被粘成大号，不是真缺失）",
            })
            seq -= set(outliers)
            if not seq:
                continue
        hi = max(seq)
        missing = [i for i in range(lo, hi + 1) if i not in seq]
        if missing:
            p = ".".join(parent)
            issues.append({
                "type": label, "parent": p, "missing": missing,
                "present": f"{lo}~{hi}",
                "text": f"{label} {p}.* 缺 {['%s.%d' % (p, i) for i in missing]}"
                        f"（该组现有 {p}.{lo} ~ {p}.{hi}）",
            })
    return issues


def find_dups(numbers, label):
    """重复条文号: 常见于转录把同一段重复输出, 或把引用误判成条文"""
    seen, dup = set(), []
    for n in numbers:
        if n in seen:
            dup.append(n)
        seen.add(n)
    return [{"type": label + "-重复", "text": f"{label} 重复出现: {sorted(set(dup))[:20]}"}] if dup else []


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="骨架完整性校验(条文号/表号/图号断号检测)")
    ap.add_argument("--md", required=True)
    ap.add_argument("--json", default=None, help="额外输出机器可读的 json 报告")
    ap.add_argument("--quiet", action="store_true", help="只打印结论")
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    md = md_path.read_text(encoding="utf-8", errors="replace")
    clauses, captions = parse(md)
    tabs = [n for k, n in captions if k == "表"]
    figs = [n for k, n in captions if k == "图"]

    issues = find_gaps(clauses, "条文") + find_gaps(tabs, "表") + find_gaps(figs, "图")
    # 重复检测对规范无效: 目录 + 正文 + 附录"条文说明"必然三重复, 不计入异常

    R = ["# 骨架完整性校验报告", "",
         f"- 转录 md: `{md_path}`",
         f"- 抽到条文号 {len(clauses)} 条 / 表号 {len(tabs)} / 图号 {len(figs)}", ""]
    if not clauses:
        R += ["⚠ **没抽到任何条文号** —— 这份文档可能不是规范类(无层级编号),",
              "或转录把编号丢了/混进了正文。本校验不适用, 请改用其他手段。"]
    elif not issues:
        R += ["## ✅ 未发现断号",
              "条文号/表号/图号在各自分组内连续, **没有整块内容丢失的迹象**。",
              "",
              "> 注意: 这只证明骨架没缺, 不证明每页内容都对。",
              "> 单元格错位、数值 OCR 错、公式错, 本校验一律看不出来。"]
    else:
        R += [f"## ⚠ 发现 {len(issues)} 处骨架异常（每一处都可能是整块内容丢失）", ""]
        for it in issues:
            R.append(f"- {it['text']}")
        R += ["", "**怎么处置**: 逐条回原 PDF 找该编号所在页 ——",
              "- 原件确实没有该号(规范本身跳号) -> 属正常, 可忽略;",
              "- 原件有而 md 没有 -> **转录丢了整段, 必须补**(重转该页或手工补录)。"]

    out = "\n".join(R)
    rep = md_path.parent / "_check_骨架.md"
    rep.write_text(out, encoding="utf-8")
    if not args.quiet:
        print(out)
    print(f"\n[ok] 骨架报告: {rep}")
    if args.json:
        Path(args.json).write_text(json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
