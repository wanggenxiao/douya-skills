#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SenseVoice 本地语音转文字（豆学堂教学版 video-to-notes 自带）

把 16k 单声道 wav 用阿里 SenseVoice-Small 转成带标点的逐字稿。免费、本地跑、不上传。
有 NVIDIA 显卡自动用显卡（20 分钟音频几秒钟）；没有就用 CPU（慢不少，但能跑）。

输出（与输入 wav 同目录）：
  <name>.txt              纯文本逐字稿
  <name>_segments.json    分段 + 时间戳

用法：
  python transcribe.py <wav路径>
  python transcribe.py <wav路径> --hotword-file 热词.txt   # 每行一个专有名词，提高准确率
  python transcribe.py <wav路径> --language en             # 英文视频

依赖（建议 Python 3.10~3.12，3.13 以上 funasr 可能装不上）：
  有 NVIDIA 显卡: pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
  没有显卡:       pip install torch torchaudio
  然后:           pip install funasr modelscope
第一次运行会自动下载模型（约 1GB），之后就不用了。
"""
import sys, json, time, argparse, re
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_hotwords(path):
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        print(f"[hotwords] 找不到 {p}，不用热词")
        return ""
    words = [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    print(f"[hotwords] 载入 {len(words)} 个词")
    return " ".join(words)


def pick_device(want):
    if want != "auto":
        return want
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda:0"
    except Exception:
        pass
    return "cpu"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav_path")
    ap.add_argument("--hotword-file", default="")
    ap.add_argument("--language", default="auto", help="auto / zh / en / ja / ko / yue")
    ap.add_argument("--device", default="auto", help="auto / cuda:0 / cpu")
    args = ap.parse_args()

    wav = Path(args.wav_path).resolve()
    if not wav.exists():
        sys.exit(f"文件不存在: {wav}")

    hotwords = load_hotwords(args.hotword_file)
    device = pick_device(args.device)
    print(f"[1/3] 加载 SenseVoice（device={device}）...")
    from funasr import AutoModel
    t0 = time.time()
    model = AutoModel(model="iic/SenseVoiceSmall", vad_model="fsmn-vad",
                      vad_kwargs={"max_single_segment_time": 30000},
                      device=device, disable_update=True)

    print(f"[2/3] 转写：{wav.name}")
    t1 = time.time()
    res = model.generate(input=str(wav), cache={}, language=args.language, use_itn=True,
                         batch_size_s=60, merge_vad=True, merge_length_s=15,
                         hotword=hotwords or None)
    if not res:
        sys.exit("转写结果为空")

    tag = re.compile(r"<\|[^|]+\|>")
    texts, segs = [], []
    for it in res:
        clean = tag.sub("", it.get("text", "")).strip()
        texts.append(clean)
        segs.append({"text": clean, "timestamp": it.get("timestamp")})

    out_txt = wav.with_suffix(".txt")
    out_json = wav.with_name(wav.stem + "_segments.json")
    out_txt.write_text("\n".join(texts), encoding="utf-8")
    out_json.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/3] 完成：转写 {time.time()-t1:.0f} 秒，总共 {time.time()-t0:.0f} 秒")
    print(f"   逐字稿: {out_txt}")


if __name__ == "__main__":
    main()
