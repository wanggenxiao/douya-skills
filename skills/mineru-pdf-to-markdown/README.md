# 安装说明 · MinerU 本地版

> **这个 skill 本身只有几 KB，它是一份"说明书"。真正干活的 MinerU 环境要装在你自己电脑上（约 3~5 GB）。**
> 不装环境，AI 读到这个 skill 也跑不了。

---

## 你需要先想清楚：要不要装

**先别急着装。** 大多数人用**桌面客户端**就够了：

- 去 MinerU 官网下客户端 → 双击装 → 拖进去 PDF → 出 Markdown
- **零门槛，不用装任何环境**
- 限制：**免费额度 200 页**；文件会**上传到云端**

**只有下面两种情况才需要装本地版：**
1. 你的文件**超过 200 页**（大部头地勘报告、规范汇编）
2. 资料**不能上云**（涉密、客户保密要求）

> 如果你两条都不占，**就用客户端，不用往下看了**。

---

## 装法（让 AI 替你装，不用自己敲命令）

打开 WorkBuddy，把下面这段**原样发给它**：

```
帮我在这台电脑上安装 MinerU 本地版，步骤：
1. 先检查有没有 Python，版本要 3.10 以上。没有就告诉我去哪下。
2. 在这个 skill 目录下创建虚拟环境：python -m venv venv
3. 用虚拟环境的 pip 安装：pip install -U "mineru[core]"
4. 装完告诉我虚拟环境里 python 的完整路径
5. 随便找个小 PDF 跑一次 scripts/run_mineru.py 验证能用
遇到报错先告诉我报错内容，不要自己乱改。
```

**这一步本身就是一次练习——让 agent 替你操作电脑，而不是你自己敲命令。**

---

## 手动装（AI 装不上时的兜底）

```bash
# 1. 进到本 skill 目录
cd <这个skill所在目录>

# 2. 建虚拟环境
python -m venv venv

# 3. 装 mineru（会下 2~4 GB，慢是正常的）
venv\Scripts\pip install -U "mineru[core]"        # Windows
# venv/bin/pip install -U "mineru[core]"          # Mac/Linux

# 4. 验证
venv\Scripts\python scripts\run_mineru.py --pdf "随便一个.pdf"
```

装完目录结构应该是：
```
mineru-pdf-to-markdown/
├── SKILL.md
├── README.md
├── scripts/run_mineru.py
└── venv/              ← 新装出来的，3~5GB，不上传 GitHub
```

---

## ⚠ 你大概率会遇到的报错（遇到了说明你走对了）

| 报错 / 现象 | 原因 | 怎么办 |
|---|---|---|
| `python 不是内部或外部命令` | 没装 Python，或装了没勾 PATH | 重装 Python，**勾上 "Add to PATH"** |
| `pip install` 卡住 / 超时 | 国内网络访问官方源慢 | 换国内镜像源，让 AI 帮你加 `-i` 参数 |
| 装了一半报 `No space left` | C 盘满了 | 清空间，或把 skill 放到别的盘 |
| **第一次跑很久没反应** | **在下模型（1~2 GB），不是卡死** | **等。下完之后就秒开了** |
| `CUDA out of memory` | 显存不够 | 加 `--backend pipeline` |
| 没有独立显卡 | 会自动用 CPU | **能跑，只是慢一些，不影响用** |
| 转出来表格是乱的 | OCR 的固有问题 | 见 SKILL.md「输出可信度红线」 |

> **这些报错都是正常的，不是你做错了。** 卡住了把报错原文截图发群里。

---

## 装完之后，第一件事不是转大文件

**先拿一份你熟悉的、知道里面写了什么的 PDF 转一次**，然后**翻到里面的表格，和原文对一遍**。

你会发现它并不是 100% 准确的 —— **这很正常，也是你必须亲眼看到的一件事**。

具体怎么建立自己的核对关口，看 `SKILL.md` 里的「输出可信度红线」和「总原则」两节。

> **不要求 100% 准确，要求错误可发现。**
