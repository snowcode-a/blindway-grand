# -*- coding: utf-8 -*-
"""
启动自检 (startup_check.py)
===========================
以**真实子进程**方式启动 qt_app.py，验证它能起来。

为什么需要这个脚本
------------------
ui/selftest.py 是直接 `qt_app.MainWindow()` 构造窗口来测的，**从不执行
qt_app.py 的 `if __name__ == "__main__":` 代码块**。

结果是：`__main__` 块里一旦有错（例如把 `setHintingPreference` 误写成
`setStyleStrategy`），selftest 全绿，但用户一双击就崩 —— 实测确实发生过。

本脚本就是补这个盲区：不构造窗口，而是像用户那样**真的启动一次**。

判据：
  · 进程在观察期内不退出
  · stderr 无 traceback
  · 内存增长到合理量级（torch + Qt 加载后通常 > 200MB）

用法::

    python ui/startup_check.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
QT_DIR = os.path.dirname(HERE)
APP = os.path.join(QT_DIR, "qt_app.py")

# 观察期（秒）。torch 首帧加载较慢，给足时间。
WATCH_SECONDS = 30
# 内存下限（MB）：低于这个值说明依赖没真正加载起来
MIN_MB = 120

# 关掉 Qt 的字体噪音，避免误判成报错
ENV = dict(os.environ)
ENV["QT_LOGGING_RULES"] = "qt.qpa.fonts=false"
ENV["PYTHONIOENCODING"] = "utf-8"


def main():
    if not os.path.exists(APP):
        print("!! 找不到 qt_app.py:", APP)
        return 1

    print("=" * 66)
    print("启动自检：以子进程方式真实启动 qt_app.py")
    print("-" * 66)

    proc = subprocess.Popen(
        [sys.executable, APP],
        cwd=QT_DIR,
        env=ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    peak_mb = 0.0
    # 注意：在 Windows 上 Popen 拿到的 pid 可能只是个壳进程，真正加载
    # torch/Qt 的是它的子进程。实测只测 shell 进程会得到 4MB 的假结果，
    # 所以要把整棵进程树的内存加起来。
    try:
        import psutil
        parent = psutil.Process(proc.pid)

        def get_mb():
            total = 0
            try:
                total += parent.memory_info().rss
            except Exception:
                pass
            try:
                for ch in parent.children(recursive=True):
                    try:
                        total += ch.memory_info().rss
                    except Exception:
                        pass
            except Exception:
                pass
            return total / 1024 / 1024
    except Exception:
        get_mb = None

    exited_early = False
    for i in range(WATCH_SECONDS):
        time.sleep(1)
        if proc.poll() is not None:
            exited_early = True
            break
        if get_mb is not None:
            try:
                peak_mb = max(peak_mb, get_mb())
            except Exception:
                pass

    result = 0
    if exited_early:
        print("  [FAIL] 进程提前退出，退出码 =", proc.returncode)
        result = 1
    else:
        print("  [PASS] 进程持续运行 {} 秒未退出".format(WATCH_SECONDS))
        if get_mb is not None:
            print("         峰值内存 {:.0f} MB".format(peak_mb))
            if peak_mb < MIN_MB:
                print("  [FAIL] 内存仅 {:.0f} MB（低于 {} MB），"
                      "可能依赖没加载起来".format(peak_mb, MIN_MB))
                result = 1
            else:
                print("  [PASS] 内存 {:.0f} MB，依赖已加载".format(peak_mb))
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    # 收输出，查 traceback
    try:
        out = proc.stdout.read() or ""
    except Exception:
        out = ""
    err_lines = [ln for ln in out.splitlines()
                 if ln.strip() and "QFontDatabase" not in ln
                 and "Qt no longer ships" not in ln]
    tb = [ln for ln in err_lines if "Traceback" in ln or "Error" in ln
          or "错误" in ln]
    if tb:
        print("  [FAIL] 输出里发现报错：")
        for ln in tb[:12]:
            print("         " + ln)
        result = 1
    else:
        print("  [PASS] 输出无 traceback / 报错")

    print("-" * 66)
    print("启动自检结论:", "通过 ✔" if result == 0 else "未通过 ✘")
    return result


if __name__ == "__main__":
    sys.exit(main())
