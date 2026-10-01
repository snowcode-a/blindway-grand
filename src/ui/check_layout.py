# -*- coding: utf-8 -*-
"""
布局几何体检
============
用真实显示驱动起窗口，量出右侧控制面板里每个分组框的实际位置，
判断有没有重叠 / 超出面板。比肉眼看截图更可靠。

运行:
    python ui/check_layout.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QT_DIR = os.path.dirname(HERE)
sys.path.insert(0, QT_DIR)

import torch  # noqa: F401,E402

from PyQt5.QtCore import Qt, QEventLoop, QTimer  # noqa: E402
from PyQt5.QtWidgets import (QApplication, QGroupBox, QScrollArea,
                             QWidget)  # noqa: E402

import qt_app  # noqa: E402


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


def find_panel(win):
    """从「视频源」分组框向上找到它的 Panel 祖先（即右侧控制面板）"""
    anchor = None
    for w in win.findChildren(QGroupBox):
        if w.title() == "视频源":
            anchor = w
            break
    if anchor is None:
        return None, None

    node = anchor.parentWidget()
    while node is not None and node is not win:
        if node.objectName() == "Panel":
            return node, anchor
        node = node.parentWidget()
    return None, anchor


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)

    win = qt_app.MainWindow()
    win.resize(1480, 920)
    win.show()
    spin(1000)

    win.switch_page(0)
    spin(800)

    panel, anchor = find_panel(win)
    if panel is None:
        print("[警告] 没找到右侧控制面板")
        return 1

    layout = panel.layout()
    if layout is not None:
        layout.activate()
    spin(300)

    print("=" * 68)
    print("页面: 实时监控")
    print("锚点控件「视频源」尺寸: {} x {}".format(anchor.width(), anchor.height()))
    print("面板尺寸: {} x {}".format(panel.width(), panel.height()))
    print("-" * 68)

    bad = 0
    rows = []
    groups = [w for w in panel.findChildren(QGroupBox) if w.parentWidget() is panel]
    for g in groups:
        geo = g.geometry()
        rows.append((geo.y(), g.title(), geo))
    rows.sort()

    for y, title, geo in rows:
        bottom = geo.y() + geo.height()
        flag = ""
        if geo.x() < 0 or geo.width() > panel.width() + 1:
            flag = "  <-- 宽度异常"
            bad += 1
        print("  y={:<5} h={:<4} 底={:<5} {}{}".format(geo.y(), geo.height(), bottom, title, flag))

    # 面板是否装在滚动区域里？在的话内容高于视口是正常的（会出滚动条）
    in_scroll = False
    node = panel.parentWidget()
    while node is not None and node is not win:
        if isinstance(node, QScrollArea) or node.__class__.__name__ == "QScrollArea":
            in_scroll = True
            break
        node = node.parentWidget()

    if not in_scroll:
        for y, title, geo in rows:
            bottom = geo.y() + geo.height()
            if bottom > panel.height() + 1:
                print("  [越界] 「{}」 超出面板 {}px".format(title, bottom - panel.height()))
                bad += 1

    # 裁切检测：实际高度是否小于内容所需的最小高度
    print("-" * 68)
    print("裁切检测（实际高度 vs 内容所需最小高度）")
    for y, title, geo in rows:
        hint = 0
        for g in groups:
            if g.title() == title:
                hint = g.minimumSizeHint().height()
                break
        need = max(hint, 40)
        if geo.height() < need:
            print("  [裁切] 「{}」 实际 {}px < 需要 {}px（被压掉 {}px）".format(
                title, geo.height(), need, need - geo.height()))
            bad += 1
        else:
            print("  [正常] 「{}」 实际 {}px >= 需要 {}px".format(title, geo.height(), need))

    # 相邻重叠检查
    for i in range(len(rows) - 1):
        y1, t1, g1 = rows[i]
        y2, t2, g2 = rows[i + 1]
        gap = y2 - (y1 + g1.height())
        if gap < 0:
            print("  [重叠] 「{}」 与 「{}」 交叠 {}px".format(t1, t2, -gap))
            bad += 1

    print("-" * 68)
    print("面板高度 {} , 内容底部 {} , 剩余空间 {}".format(
        panel.height(),
        rows[-1][0] + rows[-1][2].height() if rows else 0,
        panel.height() - (rows[-1][0] + rows[-1][2].height()) if rows else 0))
    print("结论: " + ("发现 {} 处布局问题".format(bad) if bad else "布局正常 ✔"))
    print("=" * 68)

    win.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
