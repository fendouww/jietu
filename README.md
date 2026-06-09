# 截图 jietu

极简截图工具，支持 macOS / Windows。

- 截图区域选择，可选钉住桌面（始终置顶），多屏支持
- 矩形、箭头、画笔、文字标注 —— 可拖动、可缩放、文字就地编辑
- OCR 识别 + 原位翻译（中英），译文按背景色融合、整句翻译
- 全局快捷键、托盘菜单、开机自启、守护进程、一键安全升级

> 前置：先装好 **Python 3.10+** 和 **Git**。

---

## 🪟 Windows

**安装**（自动安装所有依赖，含 easyocr + torch，约 2.5GB，请耐心等到出现 `Successfully installed`）

```powershell
python -m pip install "git+https://github.com/fendouww/jietu.git"
```

**静默启动**（无控制台窗口 + 守护进程，崩溃自动重启）

```powershell
pythonw -m jietu.watchdog
```

> 若 `python` / `pythonw` 输入后没有任何反应，是被微软商店占位程序拦截了。改用真 Python 的完整路径，例如：
> ```powershell
> & "C:\Users\你的用户名\AppData\Local\Programs\Python\Python311\pythonw.exe" -m jietu.watchdog
> ```
> 或：设置 → 应用 → 高级应用设置 → 应用执行别名 → 关掉 `python.exe` / `python3.exe`。

---

## 🍎 macOS

**安装**（自动安装所有依赖；Mac 使用系统 Vision OCR，无需 torch，安装很快）

```bash
pip3 install "git+https://github.com/fendouww/jietu.git"
```

**静默启动**（后台运行，不占用终端 + 守护进程自动重启）

```bash
nohup python3 -m jietu.watchdog >/dev/null 2>&1 &
```

**首次必做 —— 开启三个系统权限**（否则截图黑屏 / 快捷键无效）：

- 系统设置 → 隐私与安全性 → **屏幕录制** → 勾选「终端」（截图用）
- 系统设置 → 隐私与安全性 → **辅助功能** → 勾选「终端」（快捷键用）
- 系统设置 → 隐私与安全性 → **输入监控** → 勾选「终端」（全局快捷键 `~` 必需）

> 三个权限授权后请**完全退出并重新启动 jietu**。若用 `pythonw`/`launchd` 后台运行，授权对象是 **Python**（而非终端）。**macOS** 通过系统级 HID 监听**独占** `~` 键（其它程序收不到），必须开启「**输入监控**」和「辅助功能」后重启。

---

## 使用

| 操作 | 说明 |
|------|------|
| `~` 或点击托盘图标 | 开始截图（按键盘上的 `~` 键，通常与 `` ` `` 同键 + Shift） |
| 截图时拖动选区 | 松开后即出现工具栏，可继续拖动/缩放选区；`Enter` 或**双击选区**完成选区，`Esc` 取消 |
| 工具栏 `↖ □ → ✏ T` | 选择 / 矩形 / 箭头 / 画笔 / 文字 |
| 选择工具下点击标注 | 选中后可拖动、拖角缩放，`Delete` 删除 |
| 文字工具点击图上 | 直接就地输入（双击已有文字可重新编辑） |
| 工具栏 `📌` | 钉住 / 取消置顶 |
| 工具栏 `译` | OCR 识别并原位翻译（再点切换显隐） |
| 工具栏 `⎘` / 双击空白 | 复制图片到剪贴板（双击同时关闭） |
| 工具栏 `✕` / `Esc` / 右键 | 关闭当前截图 |
| 拖动截图空白处 | 移动窗口位置 |

托盘菜单：**开机自动启动**、**检查更新**、**升级到最新版**、**自动升级**（默认开启）、**退出**。

### 自动升级

推送到 GitHub `master` 后，CI 会自动 bump 版本号。客户端 jietu 在后台**每 5 分钟**检查一次云端版本；发现新版本且当前未在截图时，会**自动下载安装并重启**（托盘提示「自动升级」）。若正在截图/钉图，会等全部关闭后再升级。可在托盘关闭「自动升级」改为仅手动升级。

> 首次点「译」会下载 OCR 模型（Windows ~300MB；Mac 使用系统自带，无需下载）。

---

## 升级

托盘菜单点 **「升级到最新版」** 即可（自动停止旧进程 → 重装 → 静默重启，跨平台通用）。

也可命令行：

```powershell
# Windows
python -m pip install --upgrade --force-reinstall --no-deps "git+https://github.com/fendouww/jietu.git"
```

```bash
# macOS
pip3 install --upgrade --force-reinstall --no-deps "git+https://github.com/fendouww/jietu.git"
```

> 升级前请先关闭正在运行的 jietu（或直接用托盘「升级到最新版」，它会自动处理）。Windows 用户也可双击仓库内的 `升级.bat`。

---

## 最简记忆版

```bash
# Windows
python -m pip install "git+https://github.com/fendouww/jietu.git"   # 安装
pythonw -m jietu.watchdog                                            # 启动（静默）

# macOS
pip3 install "git+https://github.com/fendouww/jietu.git"            # 安装
nohup python3 -m jietu.watchdog >/dev/null 2>&1 &                   # 启动（静默）
```
