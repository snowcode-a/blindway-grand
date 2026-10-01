# -*- mode: python ; coding: utf-8 -*-
"""
Blindway Grand —— PyInstaller 打包配置
======================================
把整个「盲道占用检测」程序打成**独立可执行程序**，目标机器不需要装 Python。

打包内容：
  · 全部 Python 代码（qt_app / detector / evidence）
  · 依赖库：PyQt5、OpenCV、torch、ultralytics、onnxruntime、hyperlpr3
  · 数据文件：config/（参数与 ROI）、videos/（演示视频）、yolov8n.pt（YOLO 权重）

用法::

    pyinstaller build.spec --noconfirm

产物：
    dist/BlindwayGrand/BlindwayGrand.exe   ← 双击运行
    dist/BlindwayGrand/                     ← 整个目录一起拷走即可

为什么用「一个目录」而不是「单个 exe」：
  单个 exe 每次启动都要把自己解压到临时目录，本程序依赖近 1GB
  （torch 就占 500MB），启动会非常慢。一个目录的形式首次启动快得多，
  拷走整个目录即可，使用上没差别。
"""
import os

# 项目根目录 = build.spec 所在目录
# 注意：PyInstaller 注入的 SPECPATH 是"当前工作目录"而不是 spec 所在目录，
# 用它算路径会错（实测踩过：报 qt_app.py not found）。用 SPEC（spec 的完整路径）才准。
try:
    _SPEC_FILE = os.path.abspath(SPEC)          # noqa: F821
except NameError:
    _SPEC_FILE = os.path.abspath(__file__)
ROOT = os.path.dirname(_SPEC_FILE)

# 分析阶段 matplotlib 会想去 %LOCALAPPDATA%\matplotlib 建缓存，
# 在受限环境下会被拒绝（实测 WinError 5 刷屏）。这里指到一个可写目录。
os.environ.setdefault("MPLCONFIGDIR",
                      os.path.join(ROOT, "build", "_mplcache"))
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)


def data_files():
    """要一并打进产物里的数据文件（保持原有目录层级）"""
    items = []

    # 配置文件：参数 + 盲道 ROI
    items.append((os.path.join(ROOT, "config"), "config"))

    # 演示视频（体积较大但演示要用）
    for name in ("demo_blindway.mp4", "test_blindway.mp4"):
        p = os.path.join(ROOT, "videos", name)
        if os.path.exists(p):
            items.append((p, "videos"))

    # YOLO 权重
    wp = os.path.join(ROOT, "yolov8n.pt")
    if os.path.exists(wp):
        items.append((wp, "."))

    # 界面样式表
    qss = os.path.join(ROOT, "ui", "style.qss")
    if os.path.exists(qss):
        items.append((qss, "ui"))

    # onnxruntime 整包直接拷进来（见下方说明）
    try:
        import onnxruntime as _ort
        ort_dir = os.path.dirname(_ort.__file__)
        items.append((ort_dir, "onnxruntime"))
    except Exception as exc:
        print("!! 未能收集 onnxruntime 目录:", exc)

    return items


hidden = [
    # PyQt5 的.sip / Qt 插件
    "PyQt5.sip",
    "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets",
    # OpenCV
    "cv2",
    # YOLO / torch
    "ultralytics",
    "ultralytics.models",
    "ultralytics.nn.modules",
    "ultralytics.utils",
    "torch", "torchvision",
    # 追踪器（BoT-SORT 用）
    "scipy", "scipy.optimize", "scipy.spatial", "scipy.linalg",
    # 车牌识别（onnxruntime / hyperlpr3 交给 pyi_hooks 里的 hook 处理）
    "hyperlpr3",
    # 取证图合成用
    "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
    # 其它
    "yaml", "psutil", "tqdm", "packaging",
    # 本工程模块
    "detector", "evidence",
]

excludes = [
    # 明显用不到的，剔掉能省体积、也能少踩坑
    "tkinter", "PyQt6", "PySide2", "PySide6",
    "IPython", "jupyter", "notebook", "pytest", "pytest_asyncio",
    "tensorboard", "torch.utils.tensorboard",
    # matplotlib / seaborn / pandas 只有 ultralytics 的绘图辅助在用，
    # 本程序自己画图（QPainter / PIL），不需要它们
    "matplotlib", "seaborn", "pandas",
    "onnx", "onnxslim",           # 只做推理，不做模型导出
    # ★ onnxruntime 必须排除：PyInstaller 的隔离子进程 import 它必崩
    #   （SubprocessDiedError / 0xC0000005）。它的文件已经由 data_files()
    #   整目录拷进产物，运行时照样能从 _internal/onnxruntime 导入。
    "onnxruntime", "onnxruntime.capi", "onnxruntime.transformers",
    # hyperlpr3 声明了 fastapi/uvicorn 这类 Web 依赖，本程序完全不用，排掉能瘦不少
    "fastapi", "uvicorn", "starlette", "pydantic", "anyio",
    "opentelemetry", "multipart", "python_multipart",
    "rich", "markdown_it", "mdurl", "pygments", "click",
    "requests", "urllib3", "charset_normalizer", "idna", "certifi",
]

a = Analysis(
    [os.path.join(ROOT, "qt_app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=data_files(),
    hiddenimports=hidden,
    hookspath=[os.path.join(ROOT, "pyi_hooks")],   # 自定义 hook（onnxruntime / hyperlpr3）
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlindwayGrand",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX 压缩对 torch 的大 DLL 容易出问题，关掉
    console=False,             # 窗口程序，不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BlindwayGrand",
)
