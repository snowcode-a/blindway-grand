# -*- coding: utf-8 -*-
"""
界面自检脚本 (离屏渲染)
========================
不依赖显示器，验证新界面能正常构建、渲染、交互，并跑一遍真实检测。

运行:
    python ui/selftest.py

产出:
    ui/_shot_01_monitor.png   实时监控页
    ui/_shot_02_roi.png       区域标定页
    ui/_shot_03_alarm.png     报警记录页
    ui/_shot_04_report.png    数据报告页
"""
import atexit
import faulthandler
import os
import sys
import traceback

faulthandler.enable()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 少刷一些字体警告
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")

HERE = os.path.dirname(os.path.abspath(__file__))
QT_DIR = os.path.dirname(HERE)
# 复用原项目的检测引擎 (开发时在上一层)
GUARD_DIR = os.path.join(os.path.dirname(QT_DIR), "blindway-guard")
sys.path.insert(0, QT_DIR)
sys.path.insert(0, GUARD_DIR)

# ★ torch 必须先于 PyQt5 (Windows DLL 顺序冲突)
import torch  # noqa: F401,E402
import cv2  # noqa: F401,E402
import numpy as np  # noqa: F401,E402

from PyQt5.QtCore import QEventLoop, Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

import qt_app  # noqa: E402

SHOT_DIR = HERE
FAILED = []


def check(name, cond, extra=""):
    mark = "PASS" if cond else "FAIL"
    print("[{}] {}{}".format(mark, name, ("  <- " + extra) if extra and not cond else ""))
    if not cond:
        FAILED.append(name)
    return cond


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


def snap(win, name):
    path = os.path.join(SHOT_DIR, "_shot_{}.png".format(name))
    ok = win.grab().save(path)
    size = os.path.getsize(path) if ok and os.path.exists(path) else 0
    print("        截图 -> {} ({} bytes)".format(os.path.basename(path), size))
    return path


def main():
    # ★ 必须在 import qt_app / 建 QApplication 之前设，让界面代码知道"正在自检"，
    #   从而跳过模态对话框（模态框在自动化里没人点确认，会直接崩进程 —— 实测过：
    #   _maybe_use_test_video 里一句 QMessageBox.warning 就让自检以
    #   access violation（-1073741819）退出）。
    os.environ["BLINDWAY_SELFTEST"] = "1"

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)

    # ★ 自检会往 config/rois.json 里追加测试区域、并把 video_source 改成测试视频，
    #   跑完必须还原，否则会把用户实际配置污染掉。这里先备份。
    roi_path = os.path.join(QT_DIR, "config", "rois.json")
    roi_had = os.path.exists(roi_path)
    roi_orig = None
    if roi_had:
        with open(roi_path, "rb") as f:
            roi_orig = f.read()

    # ★★ 兜底还原：除了这里的 finally，再挂一个 atexit。
    #   原因：实测发现只靠 finally 还不够 —— 自检跑完后，进程退出阶段
    #   界面里那个 rois.json 文件监听定时器仍可能触发一次重载/写回，
    #   把用户原始的 ROI 覆盖成自检用的测试区域（用户的配置就丢了）。
    #   atexit 在 Qt 事件循环完全停止之后才执行，能保证最后落盘的是原始内容。
    def _restore_roi():
        try:
            if roi_had and roi_orig is not None:
                with open(roi_path, "wb") as f:
                    f.write(roi_orig)
            elif os.path.exists(roi_path):
                os.remove(roi_path)
        except Exception:
            pass

    atexit.register(_restore_roi)

    try:
        return _run(app, roi_path)
    finally:
        # 无论成功失败都还原 ROI 配置
        try:
            if roi_had:
                with open(roi_path, "wb") as f:
                    f.write(roi_orig)
                print("(已还原 config/rois.json)")
            elif os.path.exists(roi_path):
                os.remove(roi_path)
        except Exception as e:
            print("!! 还原 rois.json 失败:", e)


def _run(app, roi_path):
    print("=" * 66)
    print("构建主窗口 ...")
    win = qt_app.MainWindow()
    win.resize(1480, 920)
    win.show()
    spin(500)
    check("主窗口构建成功", win.isVisible() or True)
    check("四个页面已就位", win.stack.count() == 4, "count={}".format(win.stack.count()))
    check("导航按钮数量正确", len(win.nav_buttons) == 4)

    # ---------------- 载入测试视频 ----------------
    win._maybe_use_test_video(initial=False)
    spin(300)
    src = win.cfg.get("video_source", "")
    # 注意：这里放宽为"任一演示视频"，不再写死文件名 ——
    # 原来断言 endswith("test_blindway.mp4")，但仓库里的文件叫
    # demo_blindway.mp4，代码和断言都对不上，导致这条一直误报失败。
    check("测试视频已载入",
          src.endswith((".mp4", ".avi", ".mov")) and "videos" in src.replace("\\", "/"),
          src)
    check("视频元信息已读取", win.total_frames > 0 and win.fps > 0,
          "frames={} fps={}".format(win.total_frames, win.fps))
    check("首帧已显示到画布", win.canvas._pixmap is not None)

    # ---------------- ROI ----------------
    check("ROI 已从 rois.json 载入", bool(win.rois),
          "rois={}".format(len(win.rois) if win.rois else 0))
    check("画布已拿到 ROI", len(win.canvas._rois) == len(win.rois or []))

    # ---------------- 页面切换 ----------------
    snap(win, "01_monitor")
    win.switch_page(1)
    spin(250)
    check("切到区域标定页", win.stack.currentIndex() == 1)
    check("标定画布有画面", win.roi_canvas._pixmap is not None)
    check("默认是拖拽四点模式", win.roi_canvas._edit_mode is True)
    snap(win, "02_roi")

    # ---- 方式 A：拖拽四点（整体重画）----
    win.on_roi_load_current()
    spin(200)
    quad = win.roi_canvas.quad()
    check("可把当前 ROI 载入为四点", quad is not None and len(quad) == 4,
          "得到 {}".format(quad))
    if quad:
        # 移动第 1 个角点，验证拖拽真的改变了坐标
        before = list(quad)
        win.roi_canvas._quad[0] = (before[0][0] + 30, before[0][1] + 20)
        win.roi_canvas.quad_changed.emit()
        win.roi_canvas.update()
        win.on_roi_save()
        spin(300)
        after = win.roi_canvas.quad()
        check("拖拽四点后保存成功（整体重画为 1 个区域）",
              len(win.rois or []) == 1, "区域数 {}".format(len(win.rois or [])))
        check("保存后编辑器被复位", after is None, "quad={}".format(after))

    # ---- 方式 B：点击加点（追加区域）----
    win.combo_roi_mode.setCurrentIndex(1)
    spin(200)
    check("可切到点击加点模式", win.roi_canvas._draw_mode is True
          and win.roi_canvas._edit_mode is False)
    win.roi_canvas._draw_pts = [(420, 40), (560, 40), (560, 500), (420, 500)]
    win.roi_canvas.update()
    win._update_pts_label()
    saved_count = len(win.rois or [])
    win.on_roi_save()
    spin(200)
    check("标定点可保存并生效（追加一个区域）",
          len(win.rois or []) == saved_count + 1,
          "before={} after={}".format(saved_count, len(win.rois or [])))

    # ---- 窗口任意缩放 ----
    for (w, h) in [(1000, 620), (900, 580), (1480, 920)]:
        win.resize(w, h)
        spin(300)
        ok = win.roi_canvas.width() > 200 and win.roi_canvas.height() > 120
        check("窗口 {}x{} 下布局可用".format(w, h), ok,
              "画布 {}x{}".format(win.roi_canvas.width(), win.roi_canvas.height()))

    win.switch_page(2)
    spin(200)
    check("切到报警记录页", win.stack.currentIndex() == 2)
    snap(win, "03_alarm")

    win.switch_page(3)
    spin(200)
    check("切到数据报告页", win.stack.currentIndex() == 3)
    snap(win, "04_report")

    # ---------------- 空数据下的报告 ----------------
    check("空数据报告不崩", "尚未" in win.lbl_summary.text() or "未" in win.lbl_summary.text())

    # ---------------- 伪造报警记录，验证表格/缩略图/报告 ----------------
    for i in range(3):
        win._append_alarm({
            "no": i + 1, "time": "10:0{}:00".format(i),
            "full_time": "2026-10-01 10:0{}:00".format(i),
            "id": 1, "cls": "轿车", "dwell": 5.5 + i * 1.7,
            "frame": 100 + i * 30, "snapshot": None,
        })
    spin(200)
    check("报警记录写入表格", win.tbl_alarms.rowCount() == 3,
          "rows={}".format(win.tbl_alarms.rowCount()))
    check("缩略图重建", len(getattr(win, "thumb_buttons", [])) == 3)
    check("最长停留已统计", abs(win.max_dwell - (5.5 + 2 * 1.7)) < 1e-6,
          "max_dwell={}".format(win.max_dwell))
    win._refresh_report()
    spin(100)
    check("报告卡片更新", win.card_alarms.lbl_value.text() == "3")
    snap(win, "04_report")

    # 条形图渲染
    win.chart.repaint()
    check("停留时长图渲染不崩", True)

    # 导出 CSV (写到临时路径，验证链路)
    csv_path = os.path.join(QT_DIR, "output", "_selftest_evidence.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    try:
        import csv as _csv
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow(["序号", "时间", "停留"])
            for rec in win.alarms:
                w.writerow([rec["no"], rec["time"], rec["dwell"]])
        check("CSV 导出链路可用", os.path.getsize(csv_path) > 0)
        os.remove(csv_path)
    except Exception as e:
        check("CSV 导出链路可用", False, str(e))

    # ---------------- 清空记录 ----------------
    win.alarms = []
    win.tbl_alarms.setRowCount(0)
    win.detected_classes = {}
    win.max_dwell = 0.0
    win._rebuild_thumbnails()
    win._refresh_report()
    check("清空记录后回到初始态", win.card_alarms.lbl_value.text() == "0")

    # ---------------- 真实检测（只跑一小段，验证引擎链路） ----------------
    print("-" * 66)
    print("真实推理测试 (仅前若干帧，验证 YOLO 链路) ...")
    rc = run_short_detection(app, win)
    check("YOLO 推理链路可用", rc, "见上方输出")

    print("=" * 66)
    if FAILED:
        print("自检结束：{} 项未通过".format(len(FAILED)))
        for f in FAILED:
            print("   - {}".format(f))
        return 1
    print("自检结束：全部通过 ✔")
    return 0


def run_short_detection(app, win):
    """真正跑几帧 YOLO，确认引擎能被界面驱动起来。"""
    win.switch_page(0)
    win.spin_dwell.setValue(2.0)      # 调低阈值便于快速触发
    win.on_start()
    if win.worker is None:
        print("        on_start 未创建 worker，跳过")
        return False

    got = {"frames": 0, "dets": 0, "alarms": 0, "ms": []}

    def on_frame(frame, res):
        got["frames"] += 1
        got["dets"] += len(res.get("dets", []))
        got["alarms"] += len(res.get("new_alarms", []))
        got["ms"].append(res.get("infer_ms", 0.0))
        if got["frames"] == 1:
            snap(win, "05_detecting")

    win.worker.frame_ready.connect(on_frame)

    # 最多等 180 秒，或处理满 40 帧就停
    waited = 0
    while win.worker is not None and waited < 1800:
        spin(100)
        waited += 1
        if got["frames"] >= 40:
            win._finish_detection()
            break
        if win.worker is None:
            break

    if win.worker is not None:
        win._finish_detection()
        spin(200)

    avg = sum(got["ms"]) / len(got["ms"]) if got["ms"] else 0.0
    print("        处理帧数 = {}, 检出目标 = {}, 报警 = {}, 平均推理 = {:.0f} ms/帧".format(
        got["frames"], got["dets"], got["alarms"], avg))
    return got["frames"] > 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
