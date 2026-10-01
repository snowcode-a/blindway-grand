# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller hook: hyperlpr3
===========================
hyperlpr3 的模型权重不在包内，而是首次使用时下载到 `C:\\Users\\<用户>\\.hyperlpr3`。
打包时只需要把**代码**收进去；模型缓存仍然放在用户目录（换机器首次运行会
自动重新下载约 12MB）。
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules("hyperlpr3")
datas = collect_data_files("hyperlpr3", include_py_files=False)
