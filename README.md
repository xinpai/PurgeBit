# PurgeBit（涤尘）

PurgeBit（中文名：涤尘）是一个 Windows 平台的磁盘清理与隐私保护工具，基于
[BleachBit](https://github.com/bleachbit/bleachbit) **6.0.5** 定制，界面与命令行均已汉化。

本发行版是 BleachBit 的衍生作品：来源、许可与修改声明见 [NOTICE](NOTICE)；
许可全文见 [COPYING](COPYING)（GNU GPL v3 或更新版本）。

## 与上游 BleachBit 的差异

### 已移除的功能

| 功能 | 说明 |
|---|---|
| 填塞文件（Chaff / GuiChaff） | 上游用于生成假文件，本发行版不含该功能及其 markovify 依赖 |
| 系统信息（SystemInformation） | 上游的侧栏系统信息面板 |
| 更新检查与 Winapp2.ini 下载（Update） | 不会联网检查新版本，也不在界面内提供 Winapp2.ini 下载 |

### 保留与本地化

- **106 个清理器定义**（`cleaners/*.xml`），支持 CleanerML 语法
- 支持手动放置 `winapp2.ini` 扩展清理规则（见下文）
- 简体中文为默认界面语言，另带繁体中文与英文，翻译完整度 100%
- 中文品牌图标与样式表（`windows/purgebit.ico`、`share/purgebit.css`）

### 工程调整

- 删除上游的 GitHub Actions、`Makefile`、pylint / coverage 配置
- 仅保留 CleanerML 守门测试（`tests/TestCleanerML.py`）

## 运行

双击 **`启动涤尘.bat`** 即可，无需单独安装 Python：运行环境随程序自带
（`windows/vcpkg_installed`，包含 Python 3.12.13、GTK3、psutil、pywin32）。

> **为什么要用 .bat 启动**：某些 IDE 或终端会注入 `sitecustomize.py` 钩子替换
> `os.remove`，导致清理文件时报错。该脚本会先清空 `PYTHONPATH`、`PYTHONHOME`、
> `PYTHONSTARTUP` 来规避这个问题。

手动运行（在项目根目录的 `cmd` 中执行）：

```bat
set PYTHONPATH=
set PYTHONHOME=
set PYTHONSTARTUP=
windows\vcpkg_installed\x86-windows\tools\python3\python.exe bleachbit.py
```

## 使用流程

1. 启动后在左栏勾选要清理的项目，可用搜索框按名称过滤
2. 点击 **预览**，逐条检查将要删除的文件与将要执行的修改
3. 确认无误后点击 **删除**

建议首次使用前在 **首选项** 中确认语言、删除方式（普通删除 / 覆写删除）等设置。

## 命令行用法

```bat
set PY=windows\vcpkg_installed\x86-windows\tools\python3\python.exe
%PY% bleachbit.py [选项] 清理器.选项 [...]
```

| 选项 | 说明 |
|---|---|
| `-l`, `--list-cleaners` | 列出所有清理器 |
| `-p`, `--preview` | 预览要删除的文件和进行的修改 |
| `-c`, `--clean` | 执行清理：删除文件并执行其他操作 |
| `-s`, `--shred` | 覆写指定文件或文件夹 |
| `-w`, `--wipe-empty-space` | 覆写指定分区中的未使用空间 |
| `-o`, `--overwrite` | 以覆写方式删除文件，彻底清除数据 |
| `--gui` | 显示图形界面 |
| `--preset` | 使用图形界面内配置的选项 |
| `--all-but-warning` | 执行除有警告之外的所有选项 |
| `--except=排除项` | 排除部分选项（可重复，英文逗号分隔） |
| `--debug` | 输出详细日志 |
| `--debug-log=文件` | 将调试信息记录到文件 |
| `-v`, `--version` | 显示版本信息后退出 |
| `--no-uac` | 不弹出请求管理员权限的对话框 |

示例：

```bat
set PY=windows\vcpkg_installed\x86-windows\tools\python3\python.exe

%PY% bleachbit.py --list-cleaners                  :: 列出所有清理器
%PY% bleachbit.py --preview google_chrome.cache    :: 预览 Chrome 缓存将被删除的内容
%PY% bleachbit.py --clean google_chrome.cache      :: 直接清理（跳过预览）
```

## 自定义清理器与 Winapp2.ini

程序按以下位置查找清理器：

| 位置 | 说明 |
|---|---|
| `<程序目录>\cleaners\*.xml` | 随程序分发（系统清理器） |
| `%APPDATA%\BleachBit\cleaners\*.xml` | 用户个人清理器 |

把 `winapp2.ini` 放在上面任意一个目录，启动时即会自动导入其中的清理规则。
本发行版不提供界面内下载，需自行获取该文件。

> 个人清理器位于用户可写目录，因此其中不允许执行危险命令，仅允许删除类操作。

## 运行测试

清理器定义是否合法，由 `tests\TestCleanerML.py` 守门：它会加载 `cleaners\` 下全部
106 个 XML 与 `doc\example_cleaner.xml`，逐条执行并校验结果。

首次运行需安装测试依赖：

```bat
windows\vcpkg_installed\x86-windows\tools\python3\python.exe -m pip install -r requirements-dev.txt
```

之后双击 **`运行测试.bat`**，当前基线为：

```
19 passed, 2 skipped, 14 subtests passed
```

（2 项跳过属预期：一项需设置 `DESTRUCTIVE_TESTS=T` 才会执行破坏性用例，一项仅在非 Windows 平台运行。）

## 目录结构

| 路径 | 说明 |
|---|---|
| `bleachbit.py` | 程序入口 |
| `启动涤尘.bat` | Windows 启动脚本 |
| `运行测试.bat` | 守门测试启动脚本 |
| `bleachbit/` | 主程序模块（界面、清理引擎、工具函数） |
| `cleaners/` | 106 个清理器定义（CleanerML XML） |
| `doc/` | CleanerML 示例与 XSD 规范 |
| `po/` | 翻译源文件（zh_CN、zh_TW） |
| `locale/` | 编译后的翻译（构建产物，不入版本库） |
| `share/` | 样式表、菜单定义、受保护路径 |
| `windows/` | Windows 运行环境与图标 |
| `logs/` | 运行日志（自动生成，不入版本库） |
| `tests/` | CleanerML 守门测试 |

## 已知限制

- 随包 Python 为 **32 位**（3.12.13），单进程可用内存有限。清理包含数百万文件的
  超大目录时可能内存紧张，建议分批清理；如需长期处理超大规模目录，建议迁移到
  64 位运行环境。
- 本发行版仅针对 Windows 打包与验证，其他平台的依赖需自行准备。

## 许可

PurgeBit 自身及其派生自 BleachBit 的清理器定义，均以
[GNU General Public License version 3](COPYING) 或更新版本发布。

```
Copyright (C) 2008-2026 Andrew Ziem and the BleachBit contributors
Copyright (C) 2026 PurgeBit contributors
```

“BleachBit” 名称与标识属于上游项目。本发行版使用自有名称与图标，并非官方 BleachBit 发行版。
