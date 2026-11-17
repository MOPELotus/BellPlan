# 铃序（BellPlan）

面向 Windows 10/11 的单文件便携定时播放工具，使用 PySide6 / Qt Quick Controls。保留“节目单、定时播、立即播、节假日、调休”的操作方式，节目数量不设人工上限。

支持文件和目录播放、多时点、星期／每月／指定日期、日期时间有效期、播放队列、独立即时播放、淡入淡出、输出设备、分项备份和日志。内置音频按原分类提供。命令、朗读及 Windows 系统操作作为节目类型或系统功能提供。

## 运行

安装 Python 3.10–3.12，然后执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m bellplan
```

Linux 开发环境使用 `.venv/bin/python`。程序完全离线运行。源码开发模式使用用户数据目录，便携 EXE 使用同目录数据文件夹；可用 `--data-dir 路径` 指定独立目录。

## Windows 构建

```powershell
.venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m PyInstaller BellPlan.spec --noconfirm
```

Windows 构建输出为 `dist/BellPlan.exe`。复制该文件即可运行，不要求安装 Python、不需安装程序或注册码。运行数据放在 EXE 同目录的 `BellPlan-data/`，迁移时一并复制；内置音频随 EXE 携带。单文件运行时会在临时目录展开依赖，退出后由运行时清理。GitHub Actions 自动运行测试并构建 Windows 版本，可从对应工作流的 Artifacts 下载。构建成功不代表 Windows 实机音频、托盘和电源操作已经验收。

## 验证与范围

```sh
python -m pytest -q
```

图形交互检查：`DISPLAY=:92 QSG_RHI_BACKEND=opengl QSG_INFO=1 python tests/gui_smoke.py`。该脚本要求真实 NVIDIA 硬件渲染，不会自动改用软件渲染。

当前已在 Linux 的物理 NVIDIA 显卡上操作关键页面并检查截图。Windows 实机音频输出、多声卡、自启动、电源操作与逐格式解码仍需验收；CDA 光盘播放和旧版数据转换尚未实现。关机或程序退出期间无法播放，暂停期间的任务不会在恢复后补播。详细状态见[功能清单](docs/FEATURE_PARITY.md)。

## 文档

- [使用说明](docs/HELP.md)
- [内置音频](docs/BUNDLED_RESOURCES.md)
- [架构](docs/ARCHITECTURE.md)
- [构建与 Release](docs/BUILD.md)
- [Windows 验收](docs/WINDOWS_TESTING.md)

仓库包含独立编写的应用代码、测试及说明和音频资源，不包含原版程序、反编译／反汇编代码、伪代码、提取的界面材料或调查中间文件。
