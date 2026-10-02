@echo off
rem 运行清理器（CleanerML）守门测试：校验 cleaners\ 与 doc\example_cleaner.xml 的合法性与可用性。
rem
rem 清除 IDE/终端可能注入的 Python 环境变量，避免加载外部 sitecustomize.py。
rem （外部 shim 会替换 os.remove / os.unlink，导致测试里真实的删除用例误报失败）

set PYTHONPATH=
set PYTHONHOME=
set PYTHONSTARTUP=

cd /d "%~dp0"
"%~dp0windows\vcpkg_installed\x86-windows\tools\python3\python.exe" -m pytest tests\TestCleanerML.py -q
echo.
pause
