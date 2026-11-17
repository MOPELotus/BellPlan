# Windows 桌面功能验收

目标平台为 Windows 10/11。`platform_services.py` 延迟加载 Windows Core Audio、用户注册表和系统 API；导入模块不会修改系统状态。非 Windows 系统调用这些功能时明确报告不支持。

系统主音量通过 [pycaw 官方音频端点 API](https://github.com/AndreMiras/pycaw/blob/main/examples/audio_endpoint_volume_example.py) 读取和设置，独立于节目音量。设置音量不自动解除静音，返回值为设备的实际读回值。没有默认输出设备、COM 初始化失败或依赖缺失时报告错误。

登录自启动只操作当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 中本程序的 `BellPlan` 值。源码运行登记当前 Python 的 `-m bellplan`；打包程序登记当前可执行文件。程序路径正确加引号，不调用命令解释器。

关机和重启使用系统目录中的 `shutdown.exe`，参数为 `/s /t 0` 或 `/r /t 0`。倒计时由应用维护，用户取消时停止应用计时器。Microsoft 文档明确说明 [`/t` 大于零会隐含 `/f`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/shutdown)；因此平台服务拒绝非零延迟，避免隐含强制关闭应用。`cancel_power()` 的 `/a` 用于取消已提交给 Windows 的关机请求。

## 当前自动检查

平台服务的单元测试全部使用模拟对象，不连接真实音频端点、不修改注册表、不锁屏、不关机或重启。测试覆盖音量范围与实际读回、静音、COM 配对、注册表路径及自身值、源码与打包命令、系统命令参数、非 Windows 拒绝和错误反馈。这些测试证明调用与错误处理，不代表 Windows 系统功能已实际验收。

## Windows 实机检查

1. 在音量设置中记录当前系统音量与静音状态。分别刷新、调整音量、切换静音，并与 Windows 音量面板对照，最后恢复原值。
2. 在不同默认音频设备、拔出原默认设备和无设备状态下刷新，检查设备名称及错误信息。节目独立设备由播放模块另行验证。
3. 启用登录自启动，检查本程序注册值指向当前安装路径；重新登录后确认单实例。禁用后检查只删除本程序注册值。
4. 在允许锁屏的测试会话中手动执行锁屏，再检查返回后的程序和调度状态。
5. 先验证关机、重启的倒计时和取消，确认取消后不会提交系统命令。真实关机与重启应在保存工作后的专用测试机验收，检查不强制关闭有未保存内容的应用。
6. 打包产物必须在全新 Windows 用户环境中验证依赖、启动路径、中文目录、空格路径和升级后自启动路径。

当前 Linux 开发环境仅完成隔离单元测试；Windows 实机检查仍待执行。

## 自启动状态与路径修复

`get_startup_status()` 分开返回当前 Run 命令、当前程序所需命令、精确匹配结果以及 Windows 启动审批状态。`enabled` 表示当前观察到的注册值与可识别审批状态满足启动条件，不保证组策略或其他系统管理设置一定允许下次登录启动。

可执行文件移动后，只有用户已启用应用设置中的“登录 Windows 后启动”，上层才可调用 `repair_start_at_login()` 更新本程序的 Run 命令。它不会注册其他名字的启动项、计划任务或服务。Windows 任务管理器中的启动禁用记录只读，不覆盖用户的禁用选择。无法读取审批状态或遇到未知数据布局时不自动修复，并返回具体状态。

`StartupApproved` 不是稳定公开的 Windows API。实现只识别常见的 12 字节布局，其他布局保留未知，不能把未知状态视为已经启用。相关行为可对照 [Electron 的登录项状态读取实现](https://github.com/electron/electron/blob/main/shell/browser/browser_win.cc)。

## 休眠恢复与设备通知

`WindowsEvents` 在 Qt 事件循环中观察 Windows 原生消息，不截断消息，也不安装驱动或改变电源设置。它把 [`WM_POWERBROADCAST`](https://learn.microsoft.com/en-us/windows/win32/power/wm-powerbroadcast-messages) 的暂停、自动恢复与交互恢复转换为信号，并把设备变化转换为设备刷新信号。应用收到恢复通知后自行重置调度起点并刷新声卡；此模块不执行播放、不唤醒电脑。

原生通知在单元测试中通过内存中的 Windows 消息结构验证，没有伪造操作系统消息或触发真实休眠。需要在 Windows 实机继续验证正常睡眠、休眠、自动恢复、用户恢复、重复通知以及声卡拔插。还需验证以下自启动情况：路径含空格及中文、移动单文件程序、清除本程序 Run 值、任务管理器禁用、审批数据未知及注册表访问失败。
