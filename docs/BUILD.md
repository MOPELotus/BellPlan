# 构建与发布

项目使用 Python 3.12 构建 Windows 单文件便携版。程序运行不需要联网、安装 Python、安装程序或注册码。首次运行会在 EXE 所在目录创建 `BellPlan-data/`；迁移时一并复制此目录。

## 本机构建

在 Windows 仓库目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m PyInstaller BellPlan.spec --noconfirm --clean
```

产物为 `dist/BellPlan.exe`。单文件程序运行时会在临时目录展开依赖；该过程不安装服务或修改系统目录。

## Actions 构建

`Check` 工作流检查代码并提供 Windows 构建产物。在该次工作流页面的 Artifacts 中下载 `BellPlan-Windows`，解开后运行 EXE。

## Actions 发布 Release

`Release` 工作流提供两种入口：

- 在 Actions 中选择 **Release → Run workflow**，填写版本号，例如 `v0.1.0`。默认发布为预发布版本。
- 推送 `v` 开头的版本标签。带预发布后缀的标签标为预发布，其余标为正式版本。

版本号必须与所选提交中 `pyproject.toml` 的 `project.version` 一致，并增加 `v` 前缀。已有标签必须指向该提交。已有同名 Release 时工作流会失败，不覆盖原发布。预发布版本号应采用同时符合 Python 包版本约定的形式，例如 `0.2.0-rc1`。

工作流先在 Ubuntu 和 Windows 上运行全部单元测试与 Ruff 检查，再构建单文件 EXE。测试或构建失败时不会发布 Release。手动触发时，如果标签尚不存在，发布步骤会把标签创建在本次工作流的提交上。

发布附件包含 `BellPlan.exe` 和 `SHA256SUMS.txt`。用户可以直接从 Releases 下载 EXE；校验文件用于检查下载内容。构建产物不会附带用户数据、凭据、调查文件或外部程序代码。

自动检查不包含 Windows 实机音频、休眠唤醒、声卡热插拔及系统操作验收，参见 [Windows 验收](WINDOWS_TESTING.md)。
