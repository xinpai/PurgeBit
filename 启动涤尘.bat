@echo off
rem 清除 IDE/终端可能注入的 Python 环境变量，避免加载外部 sitecustomize.py
rem （外部 shim 会替换 os.remove 导致清理文件时报错）
set PYTHONPATH=
set PYTHONHOME=
set PYTHONSTARTUP=
start "" "%~dp0windows\vcpkg_installed\x86-windows\tools\python3\python.exe" "%~dp0bleachbit.py"
