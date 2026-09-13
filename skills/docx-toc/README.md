# 安装说明 · docx-toc

这个 skill 要装一个小东西：**python-docx**。

> 和 `mineru` 那个几个 G 的不一样，**这个只有几百 KB，十几秒就装完**。

---

## 装法（让 AI 替你装）

打开 WorkBuddy，把下面这段原样发给它：

```
帮我装一下 python-docx 和 pywin32：
1. 先检查这台电脑有没有 Python（python --version），没有就告诉我去哪下
2. 有的话执行：pip install python-docx pywin32
3. 装完跑一下 python -c "import docx; print('ok')" 验证
4. 如果 pip 很慢或超时，换国内镜像源再试
报错了先把报错内容告诉我，不要自己乱改
```

## 手动装

```bash
pip install python-docx
```

**再装一个（可选，但推荐）**——让目录页码能自动生成，省掉手动按 F9：

```bash
pip install pywin32
```

慢的话换镜像：

```bash
pip install python-docx -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证：

```bash
python -c "import docx; print('ok')"
```

---

## 第一次怎么用（三步）

### 1. 先体检，别急着插

```bash
python scripts/add_toc.py --docx "你的方案.docx" --dry-run
```

它会告诉你两个数字：

```
[体检] 已使用标题样式的段落: 0 个
[体检] 按编号识别到的疑似标题: 7 个
         L1  1 编制依据
         L2  2.1 项目概况
         ...
```

**第一个数字是 0，说明你的标题只是手动加粗的** —— 这是绝大多数人的情况，也正是"插了目录却是空的"的原因。

### 2. 插

```bash
python scripts/add_toc.py --docx "你的方案.docx" --auto-style
```

会生成 `你的方案_带目录.docx`，**原件不动**。

### 3. 打开就有目录了

**装了 Word 的话，脚本会自动把目录和页码生成好，你打开就能看到，什么都不用按。**

它会在后台悄悄开一次 Word 刷新目录再关掉，你可能会看到 Word 一闪而过，这是正常的。

#### 如果它告诉你"没装 Word"

```
[提示] 这台电脑没装 Word（或缺 pywin32），没法自动更新页码。
```

**文档是好的**，只是页码要你自己触发一次：**打开 Word/WPS → Ctrl+A 全选 → 按 F9**。

想让它变全自动，装个小库就行（前提是这台机器有 Office）：

```bash
pip install pywin32
```

---

## ⚠ 你可能会遇到的

| 现象 | 原因 | 怎么办 |
|:--|:--|:--|
| `No module named docx` | 没装好，或装到了别的 Python 里 | 重新 `pip install python-docx` |
| `pip install` 很慢/超时 | 国内访问官方源慢 | 用上面的清华镜像 |
| **目录是空的** | 标题没套样式 | 加 `--auto-style` 重跑 |
| **页码是空白** | 这台机器没装 Word/pywin32 | **Ctrl+A 然后 F9**；或 `pip install pywin32` 变全自动 |
| 提示"只支持 .docx" | 你的是老的 `.doc` | Word 里另存为 `.docx` |
| 认不出标题 | 你用的是「第一章」「一、」 | 只能在 Word 里手动套「标题 1/2/3」样式 |
| 套样式后字体变了 | 套上样式后跟随模板 | **正常，而且更规范**。不想变就别加 `--auto-style`，自己在 Word 里套 |

> 卡住了，把报错原文截图发群里。

---

## 一句话原理

**Word 的目录不是"你写了什么"，是它去收集"用了标题样式"的段落。**

你手动调大字号加粗，在 Word 眼里那还是**普通正文**，所以收不到。
这个 skill 干的最有用的一件事，就是**把你那些"看起来像标题"的段落，变成 Word 真正认的标题**。
