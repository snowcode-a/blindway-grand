# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller hook: onnxruntime
=============================
为什么需要这个 hook：
  PyInstaller 分析依赖时会用一个**隔离子进程**去 import 目标模块。
  实测 onnxruntime.capi 在这个子进程里会直接崩溃
  （SubprocessDiedError, exit code 3221225477 = 0xC0000005 访问冲突），
  导致整个打包中断。所以这里**不导入** onnxruntime，改成用
  collect_dynamic_libs / collect_data_files 把 DLL 和数据文件直接收集进来。
"""
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

binaries = collect_dynamic_libs("onnxruntime")
datas = collect_data_files("onnxruntime", include_py_files=False)

# capi 目录下的 pyd/dll 有时不在 collect_dynamic_libs 的结果里，补一次
try:
    datas += collect_data_files("onnxruntime.capi", include_py_files=False)
except Exception:
    pass

hiddenimports = [
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi._pybind_state",
]
