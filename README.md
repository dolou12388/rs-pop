# 划词助手 · PopDict

Windows 全局划词弹窗：鼠标拖选任意文字，浮层即出现在选区旁，可直接用 AI 搜索、翻译、解释，并支持多轮追问。

不注入进程、不装浏览器扩展、不常驻后台扫描 —— 一个 Python 文件启动，占用约 56 MB 内存。

```
拖选文字  →  浮层弹出（AI 搜索 / 翻译 / 解释 / 复制）  →  结果窗渲染 Markdown  →  可就地追问
```

> 本项目的每一条结论都附带可复现的实测脚本，不是"理论上可行"。完整推导见 [`Windows全局划词_终版报告.md`](Windows全局划词_终版报告.md)。

---

## 特性

- **拖选即弹**：左键按下到松开位移 ≥ 4px 才触发，单击不会误触
- **保护剪贴板**：可完整备份时逐格式按字节还原；截图留下的图片格式也照常取词，不会被它挡住
- **取消选中自动关闭**：浮层跟随选区生命周期，不会飘在屏幕上挡内容
- **图标按钮**：浮层四个按钮都带线框图标，用系统图标字体绘制（`Segoe Fluent Icons` / `Segoe MDL2 Assets`），不打包任何图片资源；字体不可用时自动退化为纯文字，图标颜色随主题翻转
- **AI 搜索 / 翻译 / 解释**：任何 OpenAI 兼容接口都能接（DeepSeek、OpenAI、Moonshot、智谱、本地 Ollama…）
- **Markdown 渲染**：标题、列表、表格（按显示宽度对齐）、代码块、引用、链接全部原生渲染
- **多轮追问**：历史按正确顺序注入，首轮回答也进上下文
- **高 DPI 清晰**：显式声明 Per-Monitor V2 感知，文字按真实 DPI 重新栅格化，不是被拉伸放大
- **系统托盘常驻**：右键进入设置或退出，设置界面可配 AI 供应商与 API Key
- **双主题**：设置中可选跟随系统、浅色或深色；已打开的浮层和结果窗会同步换色
- **零第三方依赖**：只用 `ctypes` + `tkinter` + 标准库，`pip install` 什么都不用装

---

## 环境要求

| 项目 | 要求 |
|------|------|
| 系统 | Windows 10 / 11（圆角外观需 Win11 22000+，Win10 自动退化为直角） |
| Python | 3.9+，**必须带 tkinter**（官方安装器勾选 `tcl/tk and IDLE`） |
| 第三方包 | 无 |
| 网络 | 调用云端 AI 时需要；用本地 Ollama 则完全离线 |

### 检查 tkinter 是否可用

```bat
py -3 -c "import tkinter; print('OK', tkinter.TkVersion)"
```

若报 `ModuleNotFoundError: No module named 'tkinter'`，说明你的 Python 是精简版或嵌入式发行版（常见于各类 IDE 自带的运行时）。请改装官方 Python。

---

## 快速开始

### 方式一：无窗口启动（推荐）

双击 **`启动划词.vbs`**。没有任何控制台窗口，程序直接进托盘。

### 方式二：带控制台启动（便于看报错）

双击 **`启动划词.bat`**。会保留一个控制台窗口，关掉窗口即退出，适合排查问题时使用。

### 停止

双击 **`停止划词.vbs`**，或右键托盘图标 → 退出。

> 程序有命名互斥量保护，重复双击不会堆出多个托盘图标。

---

## 配置 AI

1. 右键托盘图标 → **设置**
2. 选一个预设（DeepSeek / OpenAI / Moonshot / 智谱 GLM / 本地 Ollama），或选「自定义（OpenAI 兼容）」自己填
3. 填 **Base URL**、**模型名**、**API Key**
4. 点「测试连接」确认通路，保存

设置窗口右上角可选 **跟随系统 / 浅色 / 深色**，点击「保存」后生效。

**配置文件位置**：`%APPDATA%\PopDict\`

- `config.json` —— 供应商、模型、提示词、界面偏好
- `secrets.json` —— 仅存 API Key，写入后立刻收紧文件权限到当前用户

这两个文件**不在代码仓库内**，也已在 `.gitignore` 中排除。

### 用本地模型（完全离线）

1. 装 [Ollama](https://ollama.com/) 并 `ollama pull qwen2.5:7b`
2. 设置里选「本地 Ollama」，Base URL 保持 `http://127.0.0.1:11434/v1`
3. 不需要 API Key

---

## 工作原理

### 三级降级取词

| 级别 | 手段 | 适用 | 代价 |
|------|------|------|------|
| 1 | Win32 消息级（`GetGUIThreadInfo` → `EM_GETSEL` → `WM_GETTEXT`） | 标准 EDIT / RichEdit 控件 | 零延迟，完全不碰剪贴板 |
| 2 | UIA `TextPattern` | 理论覆盖广，实测收益极低，默认跳过 | 保留开关 |
| 3 | 剪贴板事务（合成 `Ctrl+C`，失败退 `Ctrl+Insert`） | 浏览器正文、WPS、Word 等不暴露选区的场景 | 需要严格的备份/还原 |

为什么第 2 级默认跳过、为什么第 3 级不可避免 —— 报告第二章有三个实测结论和三条早期误判的纠正。

### 剪贴板零残留（ClipGuard）

```
快照所有格式 → 合成 Ctrl+C → 读 CF_UNICODETEXT → 逐格式按字节写回 → 校验内容归因
```

**一条必须如实说明的限制**：写回过程中存在毫秒级中间态，第三方剪贴板管理器可能捕获到。这是 Windows 机制固有行为，任何走剪贴板取词的方案都无法规避。项目保证的是"事务结束后内容与原状逐字节一致"，而不是"第三方永远看不到中间态"。

### 资源占用（本机实测）

| 指标 | 数值 |
|------|------|
| 工作集 | **56.1 MB**（任务管理器「内存」列，含共享 DLL 页） |
| 私有内存 | 31.7 MB |
| 进程数 | 1 |
| 空闲 CPU | 0.78%（8 秒 62 ms） |
| 8 秒内存增长 | +0.00 MB |
| 峰值 | 57.0 MB，无爬升 |

对照：Electron 方案实测 **392 MB / 5 进程**。其中 27.7 MB 与 16 个原生线程来自 tkinter 运行时本身，业务代码合计不足 3.3 MB。

---

## 目录结构

运行所需文件都在根目录（`py_tray.py` 从脚本所在目录读 `popdict.ico`，所以不能挪进子目录）：

```
启动划词.vbs          无窗口启动（双击即用）
启动划词.bat          带控制台启动（看报错用）
停止划词.vbs          结束所有 py_pop.py 进程
launcher.py           解释器探测：挑一个带 tkinter 的 Python 再启动主程序
popdict.ico           应用图标（10 个尺寸，419 KB）

py_pop.py             主程序：取词、浮层、结果窗、追问、单实例
py_clipguard.py       剪贴板事务：备份 / 还原 / 校验
py_ai.py              AI 调用层：流式、错误分类、代理识别
py_markdown.py        Markdown 解析与渲染
py_config.py          配置与密钥持久化
py_settings.py        设置界面
py_tray.py            托盘图标（纯 ctypes，不用 pystray）
py_theme.py           配色单一真源
py_pill_effect.py     浮层的抗锯齿圆角与阴影（逐像素分层窗口）
py_pill_icon.py       按钮图标（系统图标字体，无图片资源）
py_dpi.py             DPI 感知与换算
py_res.py             读自身进程内存/句柄占用（7 项指标）
py_log.py             日志

assets/icon_source.png  图标源图
py_icon.py              纯 Python 图像处理（不用 Pillow）
py_mkicon.py            重建 popdict.ico
_mkbat.py               重建 启动划词.bat（GBK + CRLF）
_mkvbs2.py              重建 启动划词.vbs

py_*_test.py            各层测试，直接 python 运行，无 pytest 依赖
py_*（其余）             一次性探测脚本，报告里每条结论的原始证据
src/main.rs            Rust 可行性验证（不参与运行）
```

### 重建图标与启动器

```bat
py -3 py_mkicon.py     :: 从 assets/icon_source.png 重建 popdict.ico
py -3 _mkbat.py        :: 重新生成 启动划词.bat（GBK / CRLF 自检）
py -3 _mkvbs2.py       :: 重新生成 启动划词.vbs
```

`.bat` 与 `.vbs` 由脚本生成而不是手改，因为它们是 **GBK + CRLF** 编码：cmd 只认 `\r\n`，且按系统 ANSI 代码页（简中 936）解码。用 UTF-8 或 LF 保存会直接闪退，且报错信息完全看不出原因（详见报告 12.1）。

---

## 测试

```bat
py -3 py_dpi_test.py            :: DPI 声明顺序与换算
py -3 py_markdown_test.py       :: Markdown 解析
py -3 py_clipguard.py --test    :: 剪贴板事务（含「有图片时仍可取词」回归）
py -3 py_single_test.py         :: 单实例（必须在子进程里测）
py -3 py_icon_wire_test.py      :: 图标接线
py -3 py_ctrlc_test.py          :: 结果窗 Ctrl+C 只复制、不退出（含对照实验）
py -3 py_pill_icon_test.py      :: 浮层图标字体 / 布局不重叠 / 悬停 / 点击
py -3 py_pill_visual_test.py    :: 浮层真实绘制色（区分截图产物与界面缺陷）
py -3 _live_drag.py             :: 实机拖选端到端：真鼠标事件 + 浮层是否弹出
```

全部是自包含脚本，打印 `PASS` / `FAIL` 并返回退出码，不需要 pytest。

关于 `py_pill_visual_test.py`：浮层用 `-transparentcolor "#FF00FF"` 做透明键，
用 `PrintWindow` 抓图时透明区会被渲染成纯黑，看上去像有黑边和黑缝。
那个测试改用窗口自身 DC 取色，只看「真正画了什么」，避免被截图的假象误导。

---

## 已知限制

- **UIA TextPattern 在 Chromium 系上无效**：飞书、ChatGPT、Edge、豆包、微信等全部是 `Chrome_WidgetWin_1`，`GetSelection()` 恒返回 0，只能走剪贴板路径
- **剪贴板中间态**：如上所述，无法对第三方剪贴板管理器完全隐藏
- **部分剪贴板格式**：含无法备份的格式时会跳过该次取词，避免覆盖原内容。其中 `CF_BITMAP`（截图后必有）属于派生格式 —— 它由 `CF_DIB` / `CF_DIBV5` 自动合成，写回 DIB 后系统会重建，因此跳过它不影响还原，也不会阻止取词
- **多显示器混合缩放未实测**：只在单屏 150% 下验证过浮层定位与字号
- **Win10 外观退化**：`DWMWA_WINDOW_CORNER_PREFERENCE` 是 Win11 才有的属性，Win10 上读回失败并自动跳过，显示为直角
- **仅 Windows**：大量使用 `user32` / `kernel32` / `dwmapi`，没有跨平台打算
- **不声称成功率百分比**：取词测试是脚本化可重复执行，每项至少 3 次一致，但没有做大样本统计

---

## 许可

[MIT](LICENSE)
