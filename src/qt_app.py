# -*- coding: utf-8 -*-
"""
盲道占用检测 · Blindway Grand —— Qt 图形界面 (升级版)
================================================
在原有 ui_app.py 基础上重写的界面，功能定位：**比赛演示用**

界面结构:
    ┌──────────┬──────────────────────────────────────────────┐
    │          │  页面标题 + 状态徽章                          │
    │  侧边栏   ├──────────────────────────────────────────────┤
    │  实时监控 │                                              │
    │  区域标定 │  页面内容 (QStackedWidget)                    │
    │  报警记录 │                                              │
    │  数据报告 │                                              │
    │          ├──────────────────────────────────────────────┤
    │  引擎状态 │  播放控制条 (开始/暂停/停止 + 进度 + 时间)     │
    └──────────┴──────────────────────────────────────────────┘

相对旧版的改进:
    · 侧边导航 + 页面切换，告别一屏塞满控件
    · 打开视频立即出首帧，不用等模型加载
    · 可在界面里**直接标定 ROI 多边形**，不再弹 OpenCV 窗口
    · 进度条拖动真正跳帧 (不只是顺序播放)
    · 报警记录页: 大图预览 + 缩略图网格
    · 数据报告页: 统计指标 + 停留时长条形图 + 证据 CSV 导出
    · 检测参数改即时生效 (下一帧就吃到新阈值)

运行:
    python qt_app.py

注意 (Windows 上很关键):
    torch / ultralytics 必须**先于** PyQt5 导入，否则 torch 的 c10.dll
    会因 DLL 加载顺序冲突而初始化失败。
"""
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# ★ 顺序铁律：torch 必须在 PyQt5 之前导入
# ---------------------------------------------------------------------------
import torch  # noqa: F401  (不可删除，也不可挪到 PyQt5 之后)

import cv2
import numpy as np

from PyQt5.QtCore import (Qt, QMutex, QPoint, QPointF, QRectF, QSize, QThread,
                          QTimer, pyqtSignal)
from PyQt5.QtGui import (QBrush, QColor, QFont, QIcon, QImage, QPainter, QPen,
                         QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                             QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMainWindow, QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QSlider, QSpinBox, QSplitter,
                             QStackedWidget, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

# ---------------------------------------------------------------------------
# 路径引导
#
# 三种运行方式的目录约定：
#   1. 源码运行        qt_app.py 与 detector.py 同目录（或上一层能找到）
#   2. PyInstaller 打包  数据文件(exe 旁的 config/videos/yolov8n.pt) 由
#                        BLINDWAY_ROOT 环境变量指定；没设就用 exe 所在目录
#   3. 手动指定        直接设环境变量 BLINDWAY_ROOT 指向数据目录
# ---------------------------------------------------------------------------
def _resolve_base_dir():
    # ① 显式指定优先
    env_root = os.environ.get("BLINDWAY_ROOT", "").strip()
    if env_root and os.path.isdir(env_root):
        return os.path.abspath(env_root)

    if getattr(sys, "frozen", False):
        # ② 打包后：优先 exe 所在目录（数据就在旁边）
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        for cand in (exe_dir, os.path.dirname(exe_dir)):
            if os.path.exists(os.path.join(cand, "config")):
                return cand
        return exe_dir

    # ③ 源码运行：找 detector.py
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.dirname(here)):
        if os.path.exists(os.path.join(cand, "detector.py")):
            return cand
    return here


BASE_DIR = _resolve_base_dir()
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from detector import GuardEngine, abs_path, load_rois, load_settings  # noqa: E402

# 取证模块：车牌识别 / 车身颜色 / 车型 + 证据图合成
try:
    from evidence import VehicleEvidence
    _HAS_EVIDENCE = True
except Exception:                                   # pragma: no cover
    VehicleEvidence = None
    _HAS_EVIDENCE = False

APP_TITLE = "盲道占用检测 · Blindway Grand"
VERSION = "2.0"

# COCO 车辆类别
VEHICLE_CLASSES = [2, 5, 7, 3]

# 状态 -> 颜色（画在视频画面上的，不走 QSS，需与主题色系保持一致）
# 视频背景本身偏暗，所以这里用亮一档的色值保证可见性。
CLR_IN_ROI  = QColor("#F2C94C")   # 已进入盲道，计时中（主题强调黄）
CLR_OCCUPY  = QColor("#FF6B5B")   # 超时占用，报警
CLR_NORMAL  = QColor("#23855A")   # 普通车辆
CLR_ROI     = QColor("#FF6B5B")   # 盲道区域
CLR_TEXT    = QColor("#1B3A3A")   # 主题主文字（深墨）
CLR_DIM     = QColor("#5A7575")   # 主题次要文字


def fmt_time(sec):
    """秒 -> mm:ss"""
    sec = max(0, int(sec))
    return "{:02d}:{:02d}".format(sec // 60, sec % 60)


def load_qss():
    path = os.path.join(BASE_DIR, "ui", "style.qss")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


# ===========================================================================
# 指标卡片
# ===========================================================================
class StatCard(QFrame):
    """一个小指标卡: 标题 + 大数值 + 副标题

    数值的颜色**不写死在这里**，而是通过动态属性 accent 交给 style.qss 决定
    （QSS 里用 QLabel#StatValue[accent="ok"] 这样的选择器）。
    这样配色集中在一处管理，改主题不用翻 Python 代码。
    """

    def __init__(self, caption, value="—", sub="", color="default", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(5)

        self.lbl_caption = QLabel(caption)
        self.lbl_caption.setObjectName("StatCaption")

        self.lbl_value = QLabel(value)
        self.lbl_value.setObjectName("StatValue")
        self.set_accent(color)

        self.lbl_sub = QLabel(sub)
        self.lbl_sub.setObjectName("StatSub")

        lay.addWidget(self.lbl_caption)
        lay.addWidget(self.lbl_value)
        lay.addWidget(self.lbl_sub)

    def set_accent(self, accent):
        """切换数值配色（对应 style.qss 里 [accent="..."] 的分支）。

        兼容旧写法：传 '#14807F' 这种色值也能用，会退回到默认主色。
        """
        name = str(accent or "default")
        if name.startswith("#"):
            # 旧的硬编码色值 -> 映射成语义名，保持配色规范不被绕过
            low = name.lower()
            if low in ("#C0433F", "#ff5a3c", "#e5533d", "#ff6b5b"):
                name = "alarm"
            elif low in ("#8A6410", "#f0a020", "#ffc247"):
                name = "warn"
            elif low in ("#23855A", "#22c55e", "#14b88a"):
                name = "ok"
            else:
                name = "default"
        self.lbl_value.setProperty("accent", name)
        # 改属性后要让 Qt 重新套用样式表，否则不生效
        self.lbl_value.style().unpolish(self.lbl_value)
        self.lbl_value.style().polish(self.lbl_value)

    def set_value(self, value, sub=None):
        self.lbl_value.setText(str(value))
        if sub is not None:
            self.lbl_sub.setText(sub)


# ===========================================================================
# 可缩放 / 平移 / 框选的视频画布
# ===========================================================================
class VideoCanvas(QLabel):
    """
    自己用 QLabel + QPainter 画，不做预缩放 —— 因而可以任意放大而不糊。

    · 滚轮      以光标为中心缩放
    · 左键拖动  平移（非标定/非编辑模式）
    · 双击      复位到适应窗口
    · 标定模式  左键单击加顶点 / 右键撤销
    · 编辑模式  拖动**四个角点**自由拉伸盲道区域（保持四边形凸性）
    """

    zoom_changed = pyqtSignal(float)
    quad_changed = pyqtSignal()          # 四点被拖动后发出，供界面刷新顶点信息

    # 手柄命中半径（控件像素）
    HANDLE_R = 11

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VideoCanvas")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        self._pixmap = None          # 原始帧 QPixmap
        self._rois = []              # list[list[(x,y)]] 原图坐标
        self._boxes = []             # list[dict] 检测结果
        self._alert = False          # 是否画报警红边
        self._hud = ""               # 左上角附加信息

        # 视图变换：适配缩放 与 用户缩放 分开
        # （之前把两者混用一个 _scale，导致 _view_rect() 每次调用都会把
        #   用户滚轮缩放覆盖掉 —— 滚轮实际是失效的）
        self._fit_scale = 1.0
        self._zoom = 1.0
        self._scale = 1.0            # = _fit_scale * _zoom，绘制时用
        self._offset = QPointF(0, 0)
        self._panning = False
        self._pan_start = QPoint()

        # 标定模式：单击加点
        self._draw_mode = False
        self._draw_pts = []          # list[(x,y)] 原图坐标
        self._cursor = None          # 光标原图坐标，用于画橡皮筋

        # 编辑模式：拖动四个角点
        self._edit_mode = False
        self._quad = None            # list[(x,y)] 恰好 4 个点
        self._drag_idx = -1          # 正在拖的角点下标
        self._hover_idx = -1         # 鼠标悬停的角点下标

        self._placeholder = "尚未加载视频\n\n在左侧选择视频文件，或点击「用测试视频」"

    # ---------------- 数据接口 ----------------
    def set_frame(self, frame_bgr, keep_view=True):
        """frame_bgr: numpy BGR 帧"""
        if frame_bgr is None:
            return
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        first = self._pixmap is None
        self._pixmap = QPixmap.fromImage(img)
        if first or not keep_view:
            self.fit()
        self.update()

    def set_frame_from_pixmap(self, pixmap, keep_view=False):
        """直接推送一张已构造好的 QPixmap（页面之间同步画面、显示证据图都用它）"""
        if pixmap is None or pixmap.isNull():
            return
        first = self._pixmap is None
        self._pixmap = QPixmap(pixmap)
        if first or not keep_view:
            self.fit()
        self.update()

    def set_rois(self, rois):
        self._rois = [list(map(tuple, np.asarray(r).reshape(-1, 2).tolist())) for r in (rois or [])]
        self.update()

    def set_boxes(self, boxes):
        self._boxes = boxes or []
        self.update()

    def set_alert(self, alert):
        if self._alert != alert:
            self._alert = bool(alert)
            self.update()

    def set_hud(self, text):
        self._hud = text or ""
        self.update()

    def clear_all(self, placeholder=None):
        """清空画面。placeholder 不为空时同时替换占位提示文字。"""
        self._pixmap = None
        self._boxes = []
        self._rois = []
        self._alert = False
        self._draw_pts = []
        self._quad = None
        self._zoom = 1.0
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        if placeholder is not None:
            self._placeholder = placeholder
        self.update()

    # ---------------- 标定模式 ----------------
    def set_draw_mode(self, on):
        self._draw_mode = bool(on)
        self._cursor = None
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self.update()

    def draw_points(self):
        return list(self._draw_pts)

    def clear_draw_points(self):
        self._draw_pts = []
        self.update()

    def pop_draw_point(self):
        if self._draw_pts:
            self._draw_pts.pop()
            self.update()

    # ---------------- 编辑模式：拖动四个角点 ----------------
    def set_edit_mode(self, on):
        """开启后，画布上会显示 4 个可拖拽手柄。"""
        self._edit_mode = bool(on)
        if not on:
            self._drag_idx = -1
            self._hover_idx = -1
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def set_quad(self, pts):
        """设置四个角点（原图坐标）。不足 4 个点会被补齐，多余会被丢弃。"""
        if not pts:
            self._quad = None
            self.update()
            return
        q = [tuple(map(int, p)) for p in pts[:4]]
        while len(q) < 4:                      # 补齐：复制最后一个点
            q.append(q[-1] if q else (0, 0))
        self._quad = q
        self.update()

    def quad(self):
        return list(self._quad) if self._quad else None

    def _hit_handle(self, wpt):
        """返回被命中的角点下标，没命中返回 -1。"""
        if not (self._edit_mode and self._quad):
            return -1
        r = self._view_rect()
        s = self._scale
        best, best_d = -1, float(self.HANDLE_R)
        for i, (px, py) in enumerate(self._quad):
            hx = r.x() + px * s
            hy = r.y() + py * s
            d = ((wpt.x() - hx) ** 2 + (wpt.y() - hy) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = i, d
        return best

    # ---------------- 坐标换算 ----------------
    def _view_rect(self, propagate=True):
        """图像在窗口里的目标矩形。

        · 基准缩放 = 按窗口等比适配
        · 再乘上用户的滚轮缩放 _zoom
        · 加上平移量 _offset（平移用控件像素，与缩放无关）
        这样滚轮缩放才真正生效（旧实现每次调用都会把缩放重置回适配值）。
        """
        if self._pixmap is None:
            return QRectF()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        w, h = self.width(), self.height()
        if pw <= 0 or ph <= 0 or w <= 0 or h <= 0:
            return QRectF()

        fit = min(w / float(pw), h / float(ph))
        s = fit * self._zoom
        if propagate:
            self._fit_scale = fit
            self._scale = s
        dw, dh = pw * s, ph * s
        return QRectF((w - dw) / 2.0 + self._offset.x(),
                      (h - dh) / 2.0 + self._offset.y(),
                      dw, dh)

    def widget_to_image(self, pt):
        if self._pixmap is None or self._scale <= 0:
            return None
        r = self._view_rect()
        x = (pt.x() - r.x()) / self._scale
        y = (pt.y() - r.y()) / self._scale
        if 0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height():
            return (int(x), int(y))
        return None

    def fit(self):
        """复位视图：缩放回到 100%、平移归零。"""
        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._view_rect()
        self.zoom_changed.emit(self._scale)
        self.update()

    # ---------------- 交互 ----------------
    def wheelEvent(self, event):
        if self._pixmap is None:
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        factor = 1.15 ** steps
        new_zoom = self._zoom * factor
        if not (0.05 < new_zoom < 30):
            return

        # 记下光标处对应的图像坐标，缩放后把该点重新对齐到光标下
        cur = QPointF(event.pos())
        r = self._view_rect()
        img_x = (cur.x() - r.x()) / self._scale
        img_y = (cur.y() - r.y()) / self._scale

        self._zoom = new_zoom
        self._scale = self._fit_scale * self._zoom

        # 绘制矩形 = fit_rect(Z) + pan，其中 fit_rect(Z) = center + p*Z
        # 由 cur = fit(Z').x + pan'.x + img_x * fit_scale * Z'
        # 解得 pan'.x = cur.x - r_fit_new.x - img_x * fit_scale * Z'
        def rfit_x():
            pw = self._pixmap.width()
            return (self.width() - pw * self._scale) / 2.0

        def rfit_y():
            ph = self._pixmap.height()
            return (self.height() - ph * self._scale) / 2.0

        self._offset = QPointF(cur.x() - rfit_x() - img_x * self._scale,
                               cur.y() - rfit_y() - img_y * self._scale)

        self.zoom_changed.emit(self._scale)
        self.update()

    def mousePressEvent(self, event):
        if self._pixmap is None:
            return

        # 1) 编辑模式：优先抓角点
        if self._edit_mode and event.button() == Qt.LeftButton:
            idx = self._hit_handle(event.pos())
            if idx >= 0:
                self._drag_idx = idx
                self.setCursor(Qt.ClosedHandCursor)
                return

        # 2) 标定模式：单击加点
        if self._draw_mode:
            if event.button() == Qt.LeftButton:
                pos = self.widget_to_image(event.pos())
                if pos is not None:
                    self._draw_pts.append(pos)
                    self.update()
            elif event.button() == Qt.RightButton:
                self.pop_draw_point()
            return

        # 3) 否则平移
        if event.button() == Qt.LeftButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        # 拖动角点
        if self._drag_idx >= 0 and self._quad is not None:
            pos = self.widget_to_image(event.pos())
            if pos is not None:
                self._quad[self._drag_idx] = pos
                self.quad_changed.emit()
                self.update()
            return

        # 悬停高亮
        if self._edit_mode and self._quad:
            idx = self._hit_handle(event.pos())
            if idx != self._hover_idx:
                self._hover_idx = idx
                if idx >= 0:
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
                self.update()
            return

        if self._draw_mode and self._pixmap is not None:
            self._cursor = self.widget_to_image(event.pos())
            self.update()
            return

        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self._offset += QPointF(delta.x(), delta.y())
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_idx >= 0 and event.button() == Qt.LeftButton:
            self._drag_idx = -1
            self.setCursor(Qt.OpenHandCursor if self._hover_idx >= 0 else Qt.ArrowCursor)
            self.quad_changed.emit()
            return
        if self._panning and event.button() == Qt.LeftButton:
            self._panning = False
            self.setCursor(Qt.CrossCursor if self._draw_mode else Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, event):
        self.fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口尺寸变了 -> 自适应缩放随之变化，重绘即可
        self._view_rect()
        self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        if self._pixmap is None:
            p.setPen(QPen(QColor("#829999")))
            f = QFont("Microsoft YaHei UI", 11)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
            return

        r = self._view_rect()
        p.drawPixmap(r, self._pixmap, QRectF(self._pixmap.rect()))

        def to_widget(pt):
            return QPointF(r.x() + pt[0] * self._scale, r.y() + pt[1] * self._scale)

        # ---- ROI 多边形 ----
        for roi in self._rois:
            if len(roi) < 2:
                continue
            poly = QPolygonF([to_widget(pt) for pt in roi])
            p.setBrush(QBrush(QColor(255, 77, 77, 38)))
            p.setPen(QPen(CLR_ROI, 2.5))
            p.drawPolygon(poly)
            if len(roi) >= 3:
                top = min(roi, key=lambda t: t[1])
                tp = to_widget(top)
                p.setPen(QPen(CLR_ROI))
                f = QFont("Microsoft YaHei UI", 10, QFont.Bold)
                p.setFont(f)
                p.drawText(QPointF(tp.x(), max(16, tp.y() - 8)), "盲道 ROI")

        # ---- 检测框 ----
        f_box = QFont("Microsoft YaHei UI", 9, QFont.Bold)
        for b in self._boxes:
            x1, y1, x2, y2 = b["box"]
            if b.get("occupied"):
                color = CLR_OCCUPY
            elif b.get("inside"):
                color = CLR_IN_ROI
            else:
                color = CLR_NORMAL
            tl, br = to_widget((x1, y1)), to_widget((x2, y2))
            rect = QRectF(tl, br)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(color, 3 if b.get("occupied") else 2))
            p.drawRect(rect)

            # 底边中点 (判定压线的那个点)
            bc = to_widget(((x1 + x2) // 2, y2))
            p.setBrush(QBrush(color))
            p.setPen(Qt.NoPen)
            p.drawEllipse(bc, 4.5, 4.5)

            # 标签
            if b.get("occupied"):
                tag = "ID {} 占用 {:.1f}s".format(b["id"], b.get("dwell", 0.0))
            elif b.get("inside"):
                tag = "ID {} 停留 {:.1f}s".format(b["id"], b.get("dwell", 0.0))
            else:
                tag = "ID {} {}".format(b["id"], b.get("label", ""))
            p.setFont(f_box)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(tag) + 12
            th = fm.height() + 6
            lp = to_widget((x1, y1))
            tag_rect = QRectF(lp.x(), max(0.0, lp.y() - th), tw, th)
            p.setBrush(QBrush(color))
            p.setPen(Qt.NoPen)
            p.drawRect(tag_rect)
            p.setPen(QPen(QColor("#123333") if not b.get("inside") else QColor("#ffffff")))
            p.drawText(tag_rect.adjusted(6, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, tag)

        # ---- 标定模式的橡皮筋 ----
        if self._draw_mode and self._draw_pts:
            pts = [to_widget(pt) for pt in self._draw_pts]
            if self._cursor is not None:
                pts_live = pts + [to_widget(self._cursor)]
            else:
                pts_live = pts
            p.setBrush(QBrush(QColor(77, 155, 255, 40)))
            p.setPen(QPen(QColor("#14807F"), 2, Qt.DashLine))
            if len(pts_live) >= 3:
                p.drawPolygon(QPolygonF(pts_live))
            elif len(pts_live) >= 2:
                p.drawPolyline(QPolygonF(pts_live))
            p.setBrush(QBrush(QColor("#14807F")))
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            for i, pt in enumerate(pts):
                p.drawEllipse(pt, 5, 5)
                p.setPen(QPen(QColor("#ffffff")))
                p.drawText(QPointF(pt.x() + 8, pt.y() - 8), str(i + 1))
                p.setPen(QPen(QColor("#ffffff"), 1.5))

        # ---- 编辑模式：可拖拽的四个角点 ----
        if self._edit_mode and self._quad:
            qpts = [to_widget(pt) for pt in self._quad]
            p.setBrush(QBrush(QColor(77, 155, 255, 45)))
            p.setPen(QPen(QColor("#14807F"), 2))
            p.drawPolygon(QPolygonF(qpts))

            for i, pt in enumerate(qpts):
                active = (i == self._drag_idx) or (i == self._hover_idx)
                radius = 9.0 if active else 7.0
                # 外圈白边 + 内圈实心，拖动中的点用亮橙色区分
                p.setPen(QPen(QColor("#ffffff"), 2))
                p.setBrush(QBrush(QColor("#ff8c1a") if active else QColor("#14807F")))
                p.drawEllipse(pt, radius, radius)
                p.setPen(QPen(QColor("#ffffff")))
                p.setFont(QFont("Microsoft YaHei UI", 8))
                p.drawText(QPointF(pt.x() + 10, pt.y() - 9), str(i + 1))

            # 提示条
            tip = "拖动 4 个圆点自由拉伸盲道区域"
            p.setFont(QFont("Microsoft YaHei UI", 9))
            fm2 = p.fontMetrics()
            tw2 = fm2.horizontalAdvance(tip) + 16
            tip_r = QRectF((self.width() - tw2) / 2.0, 10, tw2, fm2.height() + 8)
            p.setBrush(QBrush(QColor(12, 15, 21, 200)))
            p.setPen(QPen(QColor("#14807F")))
            p.drawRoundedRect(tip_r, 6, 6)
            p.setPen(QPen(QColor("#8FD4D4")))
            p.drawText(tip_r, Qt.AlignCenter, tip)

        # ---- 报警红边 ----
        if self._alert:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(CLR_OCCUPY, 6))
            p.drawRect(self.rect().adjusted(3, 3, -3, -3))
            p.setPen(QPen(CLR_OCCUPY))
            p.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
            p.drawText(self.rect().adjusted(0, 12, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                       "⚠ 盲道占用报警")

        # ---- 左上角 HUD ----
        if self._hud:
            p.setFont(QFont("Microsoft YaHei UI", 9))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(self._hud) + 16
            hud_rect = QRectF(10, 10, tw, fm.height() + 8)
            p.setBrush(QBrush(QColor(12, 15, 21, 190)))
            p.setPen(QPen(QColor("#E2EBEB")))
            p.drawRoundedRect(hud_rect, 6, 6)
            p.setPen(QPen(QColor("#5A7575")))
            p.drawText(hud_rect.adjusted(8, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self._hud)


# ===========================================================================
# 后台检测线程
# ===========================================================================
class DetectWorker(QThread):
    """
    在子线程里跑 YOLO 逐帧推理，避免阻塞界面。

    · 推理本身 (torch) 会释放 GIL，所以界面能保持流畅
    · 暂停 / 停止 / 跳帧 都通过线程安全的标志位与主线程协作
    """

    frame_ready = pyqtSignal(object, object)   # (frame_bgr, result_dict)
    progress = pyqtSignal(int, int)            # (当前帧号, 总帧数)
    status = pyqtSignal(str)
    finished_all = pyqtSignal(int)             # 总帧数
    failed = pyqtSignal(str)

    def __init__(self, cfg, rois, evidence=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.rois = rois
        self.evidence = evidence          # VehicleEvidence 实例（可为 None）
        self.engine = None
        self._stop = False
        self._pause = False
        self._lock = QMutex()
        self._seek_target = None

    # ---------------- 主线程调用 ----------------
    def stop(self):
        self._stop = True

    def set_paused(self, paused):
        self._pause = bool(paused)

    def seek(self, frame_idx):
        """请求跳到指定帧（下一轮循环生效）"""
        self._lock.lock()
        self._seek_target = int(frame_idx)
        self._lock.unlock()

    def _take_seek(self):
        self._lock.lock()
        target = self._seek_target
        self._seek_target = None
        self._lock.unlock()
        return target

    # ---------------- 线程体 ----------------
    def run(self):
        cap = None
        writer = None
        try:
            src = self.cfg.get("video_source", "")
            if str(src).isdigit():
                src = int(src)
            elif not (str(src).startswith("rtsp") or os.path.isabs(str(src))):
                src = abs_path(src)

            cap = cv2.VideoCapture(src)
            if not cap.isOpened():
                self.failed.emit("无法打开视频源：\n{}".format(self.cfg.get("video_source")))
                return

            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if fps < 1:
                fps = 25.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.cfg["_fps"] = fps

            # 若界面在开始前拖动过进度条，就从那一帧开始
            start_frame = int(self.cfg.get("start_frame", 0) or 0)
            if start_frame > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES,
                        max(0, min(start_frame, max(0, total - 1))))

            # ---- 可选：写出标注视频 ----
            out_path = None
            if self.cfg.get("save_output_video"):
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                out_path = abs_path(self.cfg.get("output_video", "output/annotated.mp4"))
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                         fps, (w, h))

            # ---- 加载模型（首次约 5~15 秒）----
            self.status.emit("正在加载 YOLOv8n 模型 ...")
            self.engine = GuardEngine(self.cfg, self.rois)
            # 把取证模块交给引擎：报警时会提取车牌/颜色/车型并合成证据图
            self.engine.evidence = self.evidence
            self.engine.location = str(self.cfg.get("location", "") or "").strip()
            self.engine.fps = fps
            self.engine.reset(fps)
            self.engine.load_model(progress_cb=self.status.emit)
            self.status.emit("模型就绪，开始逐帧检测 ...")

            while not self._stop:
                # 先判暂停再读帧：否则按暂停时会多处理一帧，
                # 看起来像"暂停晚了一帧才生效"。
                if self._pause:
                    self.msleep(50)
                    continue

                # ---- 处理跳帧请求 ----
                target = self._take_seek()
                if target is not None and total > 0:
                    tgt = max(0, min(int(target), total - 1))
                    if not cap.set(cv2.CAP_PROP_POS_FRAMES, tgt):
                        # 个别编码/封装不支持按帧号定位，退化为按时间戳定位
                        cap.set(cv2.CAP_PROP_POS_MSEC, tgt * 1000.0 / max(1.0, fps))
                    # 跳帧后旧的跟踪状态没有意义，重置掉
                    # （注意：这会把引擎帧计数清零，所以界面进度要按
                    #   "真实视频位置"来显示，否则进度条会莫名跳回 0%）
                    self.engine.reset(fps)
                    self.progress.emit(tgt, total)

                ok, frame = cap.read()
                if not ok:
                    break

                t0 = time.time()
                res = self.engine.process(frame)
                infer_ms = (time.time() - t0) * 1000.0

                # 绘图在主线程做（画得更精细），这里只送原始帧
                if writer is not None:
                    try:
                        writer.write(self.engine.draw(frame.copy(), res["dets"]))
                    except Exception:
                        pass

                res["infer_ms"] = infer_ms
                self.frame_ready.emit(frame, res)
                # 进度用**真实视频位置**上报（引擎帧计数在 seek 后会被清零，
                # 直接用它会让进度条跳回 0%）
                vid_pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                if vid_pos <= 0 or vid_pos > total:
                    vid_pos = res["frame_idx"]
                self.progress.emit(vid_pos, total)

                # 给界面一点刷新机会（CPU 推理本身较慢，这步几乎不增加耗时）
                self.msleep(1)

            if writer is not None:
                writer.release()
            # 只有"自然播完"才算检测完成；用户点「停止」而退出循环时
            # 不能发这个信号 —— 否则界面会误判为跑完了（实测会把
            # 「重新开始」的状态判断带偏）。
            if not self._stop:
                self.finished_all.emit(total)

        except Exception as e:
            import traceback
            self.failed.emit("{}\n\n{}".format(e, traceback.format_exc()))
        finally:
            if cap is not None:
                cap.release()


# ===========================================================================
# 主窗口
# ===========================================================================
class MainWindow(QMainWindow):
    PAGES = [
        ("实时监控", "检测画面 · 区域内目标 · 实时统计"),
        ("区域标定", "在画面上点选盲道多边形，保存后立即生效"),
        ("报警记录", "每一次占用报警的证据截图与详情"),
        ("数据报告", "本次检测的统计汇总与证据导出"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("{}  ·  v{}".format(APP_TITLE, VERSION))
        self.resize(1480, 920)
        # 允许缩到比较小；布局各处都做了弹性处理，小窗口下靠滚动条兜底
        self.setMinimumSize(860, 560)

        # ---- 配置与运行时状态 ----
        self.cfg = load_settings()
        self.rois = load_rois()
        self.engine = None            # 惰性创建
        self.model_ready = False
        self.worker = None            # 后台检测线程
        self.preview_cap = None       # 空闲预览用
        self.preview_timer = None

        # ---- 取证模块（车牌识别 / 车身颜色 / 车型）----
        self.evidence = None
        if _HAS_EVIDENCE:
            try:
                self.evidence = VehicleEvidence(
                    snapshot_dir=self.cfg.get("snapshot_dir", "output/alerts"))
            except Exception as exc:               # 模型缺失也不该让程序起不来
                print("[取证模块] 初始化失败，将退化为纯截图:", exc)

        self.total_frames = 0
        self.fps = 25.0
        self.frame_w = 0
        self.frame_h = 0
        self.last_result = None
        self.paused = False
        self._seek_dragging = False
        # 未开始检测时拖动进度条设定的起始帧（0 = 从头开始）
        self._pending_start_frame = 0
        # 用于判断「重新开始」按钮该不该可点
        self._reached_start = False     # 是否刚从开头重播
        self._last_frame_idx = 0

        self.alarms = []              # 报警记录 list[dict]
        self._selected_alarm = None   # 当前在报警页选中的记录
        self.detected_classes = {}    # 类别 -> 次数
        self.max_dwell = 0.0
        self.processed_frames = 0
        self.detect_seconds = 0.0

        self._roi_watch_mtime = 0
        self._cur_roi_poly = []       # 标定页当前多边形
        # True = 拖拽四点（保存时替换）；False = 点击加点（保存时追加）。
        # 默认与下拉框第一项"拖拽四点"一致，由 on_roi_mode_changed 维护。
        self._roi_drag_mode = True
        # 保存 rois.json 期间置 True，屏蔽文件监听器（防竞态覆盖）
        self._roi_saving = False

        self._build_ui()

        qss = load_qss()
        if qss:
            self.setStyleSheet(qss)

        self._refresh_roi_status()
        self._update_badges()
        self._load_location()          # 把已保存的监测地点填进输入框
        self.switch_page(0)
        # 优先装载 settings.json 里配置的视频源；
        # 只有在没有配置（或配置的文件不存在）时才回退到测试视频。
        # —— 这里曾经无条件调用 _maybe_use_test_video()，会把用户配好的
        #    视频覆盖成测试视频，是个真 bug。
        if not self._use_configured_source():
            self._maybe_use_test_video(initial=True)

        # ROI 文件监视（标定工具保存后自动生效）
        self._roi_timer = QTimer(self)
        self._roi_timer.timeout.connect(self._watch_roi_file)
        self._roi_timer.start(1500)

        # 时钟
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

        self.statusBar().showMessage("就绪 · 选择视频后点击「开始检测」")

    # ==================================================================
    #  界面搭建
    # ==================================================================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(20, 16, 20, 8)
        rlay.setSpacing(14)
        rlay.addLayout(self._build_header())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_monitor_page())
        self.stack.addWidget(self._build_roi_page())
        self.stack.addWidget(self._build_alarm_page())
        self.stack.addWidget(self._build_report_page())
        rlay.addWidget(self.stack, 1)

        self._build_playback_bar()
        rlay.addWidget(self.playback_bar)

        root.addWidget(right, 1)

        # 状态栏
        self.lbl_status_left = QLabel("就绪")
        self.lbl_status_left.setObjectName("StatusLeft")
        self.lbl_status_right = QLabel("")
        self.lbl_status_right.setObjectName("StatusRight")
        self.statusBar().addWidget(self.lbl_status_left, 1)
        self.statusBar().addPermanentWidget(self.lbl_status_right)

    # ---------------- 侧边栏 ----------------
    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setMinimumWidth(150)
        side.setMaximumWidth(300)
        side.setMinimumHeight(0)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(12, 18, 12, 14)
        lay.setSpacing(8)

        # 品牌区：参考图是「白色圆角卡片 + 图标 + 标题/副标题」的结构。
        # 注意：里面的标签必须显式设成透明背景，否则会继承全局白底，
        # 在 teal 侧边栏上糊出一块方块（实测踩过）。
        brand_card = QFrame()
        brand_card.setObjectName("BrandCard")
        bl = QHBoxLayout(brand_card)
        bl.setContentsMargins(10, 9, 10, 9)
        bl.setSpacing(10)

        logo = QLabel("盲")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(34, 34)
        # 无渐变：纯 teal 底 + 白字（规范禁止线性渐变）
        logo.setStyleSheet(
            "background:#0E7A7A; color:#FFFFFF; border-radius:10px;"
            "font-size:17px; font-weight:bold;")
        bt = QVBoxLayout()
        bt.setSpacing(0)
        t = QLabel("盲道占用检测")
        t.setObjectName("BrandTitle")
        s = QLabel("Blindway Grand v{}".format(VERSION))
        s.setObjectName("BrandSub")
        bt.addWidget(t)
        bt.addWidget(s)
        bl.addWidget(logo)
        bl.addLayout(bt, 1)
        lay.addWidget(brand_card)
        lay.addSpacing(14)

        nav_cap = QLabel("功能导航")
        nav_cap.setObjectName("NavSection")
        lay.addWidget(nav_cap)

        self.nav_buttons = []
        icons = ["◉", "⬚", "⚑", "▤"]
        for i, (name, _) in enumerate(self.PAGES):
            btn = QPushButton("  {}  {}".format(icons[i], name))
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self.switch_page(idx))
            self.nav_buttons.append(btn)
            lay.addWidget(btn)

        lay.addStretch(1)

        # 引擎状态卡
        eng = QFrame()
        eng.setObjectName("Panel")
        el = QVBoxLayout(eng)
        el.setContentsMargins(13, 11, 13, 11)
        el.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(7)
        self.dot_engine = QLabel()
        self.dot_engine.setFixedSize(9, 9)
        self.dot_engine.setStyleSheet("background:#23855A; border-radius:4px;")
        lbl_eng = QLabel("引擎就绪")
        lbl_eng.setObjectName("SectionLabel")
        row.addWidget(self.dot_engine)
        row.addWidget(lbl_eng)
        row.addStretch()
        el.addLayout(row)

        self.lbl_engine_info = QLabel("YOLOv8n · CPU")
        self.lbl_engine_info.setObjectName("CardDesc")
        self.lbl_engine_info.setWordWrap(True)
        el.addWidget(self.lbl_engine_info)

        self.lbl_roi_side = QLabel("ROI —")
        self.lbl_roi_side.setObjectName("CardDesc")
        self.lbl_roi_side.setWordWrap(True)
        el.addWidget(self.lbl_roi_side)

        lay.addWidget(eng)
        return side

    # ---------------- 顶部标题栏 ----------------
    def _build_header(self):
        head = QHBoxLayout()
        head.setSpacing(12)

        tl = QVBoxLayout()
        tl.setSpacing(2)
        self.lbl_page_title = QLabel(self.PAGES[0][0])
        self.lbl_page_title.setObjectName("PageTitle")
        self.lbl_page_sub = QLabel(self.PAGES[0][1])
        self.lbl_page_sub.setObjectName("PageSubtitle")
        tl.addWidget(self.lbl_page_title)
        tl.addWidget(self.lbl_page_sub)

        self.badge_status = QLabel("就绪")
        self.badge_status.setObjectName("IdleBadge")
        self.badge_alarm = QLabel("报警 0 次")
        self.badge_alarm.setObjectName("IdleBadge")
        self.badge_zone = QLabel("区域内 0")
        self.badge_zone.setObjectName("IdleBadge")

        head.addLayout(tl)
        head.addStretch(1)
        head.addWidget(self.badge_zone)
        head.addWidget(self.badge_alarm)
        head.addWidget(self.badge_status)
        return head

    # ---------------- 页面 1: 实时监控 ----------------
    def _build_monitor_page(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # 中间视频区
        center = QFrame()
        center.setObjectName("Panel")
        cl = QVBoxLayout(center)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(8)

        bar = QHBoxLayout()
        cap = QLabel("实时画面")
        cap.setObjectName("SectionLabel")
        hint = QLabel("滚轮缩放 · 拖拽平移 · 双击复位")
        hint.setObjectName("CardDesc")
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("CardDesc")
        bar.addWidget(cap)
        bar.addStretch(1)
        bar.addWidget(self.lbl_zoom)
        bar.addSpacing(10)
        bar.addWidget(hint)
        cl.addLayout(bar)

        self.canvas = VideoCanvas()
        self.canvas.zoom_changed.connect(
            lambda s: self.lbl_zoom.setText("{:.0f}%".format(s * 100)))
        cl.addWidget(self.canvas, 1)

        # 图例
        legend = QHBoxLayout()
        legend.setSpacing(16)
        for text, color in (("普通车辆", "#23855A"),
                            ("区域内停留中", "#8A6410"),
                            ("超时占用报警", "#C0433F")):
            item = QHBoxLayout()
            item.setSpacing(6)
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet("background:%s; border-radius:4px;" % color)
            tl = QLabel(text)
            tl.setObjectName("CardDesc")
            item.addWidget(dot)
            item.addWidget(tl)
            legend.addLayout(item)
        legend.addStretch(1)
        cl.addLayout(legend)

        # 注意：center 下面会被放进分栏器，所以这里不能再 lay.addWidget(center)

        # 右侧控制面板：宽度固定会挡住窗口缩放，改成弹性范围 + 可拖动分栏
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(520)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(13, 13, 13, 13)
        pl.setSpacing(9)

        # --- 地点（可填写、可保存，会写进证据图）---
        g0 = QGroupBox("监测地点")
        g0l = QVBoxLayout(g0)
        g0l.setSpacing(6)
        self.edit_location = QLineEdit()
        self.edit_location.setPlaceholderText("例如：湖北省荆州市 长江大学东校区南门")
        self.edit_location.setMinimumHeight(30)
        self.edit_location.setClearButtonEnabled(True)
        self.edit_location.setToolTip(
            "填写这块盲道的实际位置。填写后可点「保存地点」写入配置文件，\n"
            "之后每次报警的证据图上都会带上这个地点。")
        g0l.addWidget(self.edit_location)

        loc_row = QHBoxLayout()
        loc_row.setSpacing(6)
        self.btn_save_location = QPushButton("保存地点")
        self.btn_save_location.setObjectName("Primary")
        self.btn_save_location.setMinimumHeight(30)
        self.btn_save_location.clicked.connect(self.on_save_location)
        loc_row.addWidget(self.btn_save_location)
        g0l.addLayout(loc_row)

        self.lbl_location_hint = QLabel()
        self.lbl_location_hint.setObjectName("CardDesc")
        self.lbl_location_hint.setWordWrap(True)
        g0l.addWidget(self.lbl_location_hint)
        pl.addWidget(g0)

        # --- 视频源 ---
        g1 = QGroupBox("视频源")
        g1l = QVBoxLayout(g1)
        g1l.setSpacing(6)
        self.edit_video = QLineEdit()
        self.edit_video.setReadOnly(True)
        self.edit_video.setMinimumHeight(30)
        g1l.addWidget(self.edit_video)
        b_open = QPushButton("选择视频…")
        b_open.setMinimumHeight(32)
        b_open.clicked.connect(self.on_select_video)
        b_test = QPushButton("用测试视频")
        b_test.setMinimumHeight(32)
        b_test.clicked.connect(lambda: self._maybe_use_test_video(initial=False))
        g1l.addWidget(b_open)
        g1l.addWidget(b_test)

        self.combo_source = QComboBox()
        self.combo_source.addItems(["视频文件", "本地摄像头 (0)", "RTSP / 网络流"])
        self.combo_source.setMinimumHeight(30)
        self.combo_source.currentIndexChanged.connect(self.on_source_type_changed)
        g1l.addWidget(self.combo_source)
        pl.addWidget(g1)

        # --- 检测参数 ---
        g2 = QGroupBox("检测参数")
        form = QGridLayout(g2)
        form.setSpacing(7)
        form.setColumnStretch(1, 1)

        form.addWidget(self._dim("停留阈值"), 0, 0)
        self.spin_dwell = QDoubleSpinBox()
        self.spin_dwell.setRange(0.5, 120.0)
        self.spin_dwell.setSingleStep(0.5)
        self.spin_dwell.setSuffix(" 秒")
        self.spin_dwell.setFixedWidth(142)
        self.spin_dwell.setValue(float(self.cfg.get("dwell_threshold_sec", 5.0)))
        self.spin_dwell.setToolTip("车辆在盲道内连续停留超过该时长即触发报警")
        form.addWidget(self.spin_dwell, 0, 1)

        form.addWidget(self._dim("置信度"), 1, 0)
        self.spin_conf = QDoubleSpinBox()
        self.spin_conf.setRange(0.05, 0.95)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setFixedWidth(142)
        self.spin_conf.setValue(float(self.cfg.get("confidence", 0.35)))
        form.addWidget(self.spin_conf, 1, 1)

        form.addWidget(self._dim("确认帧数"), 2, 0)
        self.spin_confirm = QSpinBox()
        self.spin_confirm.setRange(1, 30)
        self.spin_confirm.setFixedWidth(142)
        self.spin_confirm.setValue(int(self.cfg.get("confirm_frames", 3)))
        self.spin_confirm.setToolTip("连续多少帧命中才确认进入盲道，用来过滤抖动")
        form.addWidget(self.spin_confirm, 2, 1)

        form.addWidget(self._dim("冷却时间"), 3, 0)
        self.spin_cooldown = QDoubleSpinBox()
        self.spin_cooldown.setRange(0.0, 300.0)
        self.spin_cooldown.setSuffix(" 秒")
        self.spin_cooldown.setFixedWidth(142)
        self.spin_cooldown.setValue(float(self.cfg.get("alarm_cooldown_sec", 8.0)))
        form.addWidget(self.spin_cooldown, 3, 1)

        form.addWidget(self._dim("运行设备"), 4, 0)
        self.combo_device = QComboBox()
        # 只列出这台机器真能用的设备。
        # 之前无脑列 cpu/cuda 两项，用户选了 cuda 而机器没 GPU 时，
        # ultralytics 会抛 "Invalid CUDA 'device=0' requested" 把检测线程搞崩。
        _cuda_ok = False
        try:
            _cuda_ok = bool(torch.cuda.is_available())
            _cuda_n = int(torch.cuda.device_count()) if _cuda_ok else 0
        except Exception:
            _cuda_n = 0
        self._cuda_ok = _cuda_ok
        self.combo_device.addItem("cpu")
        if _cuda_ok:
            for _i in range(max(1, _cuda_n)):
                self.combo_device.addItem("cuda:{}".format(_i))
        else:
            # 仍然列出来但禁用，让用户知道"有 GPU 这个选项，只是本机没有"
            self.combo_device.addItem("cuda（本机无可用 GPU）")
            _item = self.combo_device.model().item(1)
            if _item is not None:
                _item.setEnabled(False)
        self.combo_device.setFixedWidth(142)
        _req = str(self.cfg.get("device", "cpu")).strip().lower()
        self.combo_device.setCurrentIndex(0 if (_req == "cpu" or not _cuda_ok) else 1)
        self.combo_device.setToolTip(
            "本机 CUDA 可用" if _cuda_ok else
            "本机没有可用的 CUDA（torch.cuda.is_available() = False），只能用 CPU")
        form.addWidget(self.combo_device, 4, 1)
        pl.addWidget(g2)

        # 参数即时生效
        self.spin_dwell.valueChanged.connect(self._push_params)
        self.spin_conf.valueChanged.connect(self._push_params)
        self.spin_confirm.valueChanged.connect(self._push_params)
        self.spin_cooldown.valueChanged.connect(self._push_params)

        # --- 报警方式 ---
        g3 = QGroupBox("报警方式")
        g3l = QVBoxLayout(g3)
        g3l.setSpacing(2)
        g3l.setContentsMargins(9, 4, 9, 6)
        self.chk_snapshot = QCheckBox("报警截图落盘")
        self.chk_snapshot.setChecked(bool(self.cfg.get("save_snapshot", True)))
        self.chk_sound = QCheckBox("声音提示")
        self.chk_sound.setChecked(bool(self.cfg.get("play_sound", True)))
        self.chk_record = QCheckBox("保存标注视频")
        self.chk_record.setChecked(bool(self.cfg.get("save_output_video", False)))
        # 默认不勾选：同一辆车在一次连续占用中只报警一次，不重复刷记录
        self.chk_repeat = QCheckBox("同一辆车可重复报警")
        self.chk_repeat.setChecked(bool(self.cfg.get("alarm_repeat_enabled", False)))
        self.chk_repeat.setToolTip(
            "不勾选（默认）：一辆车压住盲道期间只报警一次，直到它离开后再次占用才会重新报警。\n"
            "勾选：冷却时间结束后允许再次报警，适合『占道越久越需要反复提醒』的场景。")
        for c in (self.chk_snapshot, self.chk_sound, self.chk_record, self.chk_repeat):
            g3l.addWidget(c)
        self.chk_repeat.toggled.connect(self._push_params)
        pl.addWidget(g3)

        # --- 盲道 ROI ---
        g4 = QGroupBox("盲道 ROI")
        g4l = QVBoxLayout(g4)
        g4l.setSpacing(5)
        g4l.setContentsMargins(9, 4, 9, 6)
        self.lbl_roi = QLabel("—")
        self.lbl_roi.setObjectName("SectionLabel")
        self.lbl_roi.setWordWrap(True)
        g4l.addWidget(self.lbl_roi)
        b_roi = QPushButton("前往区域标定 →")
        b_roi.clicked.connect(lambda: self.switch_page(1))
        g4l.addWidget(b_roi)
        pl.addWidget(g4)

        # --- 区域内目标 ---
        g6 = QGroupBox("区域内目标")
        g6l = QVBoxLayout(g6)
        g6l.setContentsMargins(8, 6, 8, 8)
        g6l.setSpacing(6)
        self.lbl_active_count = QLabel("区域内目标 (0)")
        self.lbl_active_count.setObjectName("CardDesc")
        g6l.addWidget(self.lbl_active_count)

        self.tbl_active = QTableWidget(0, 3)
        self.tbl_active.setHorizontalHeaderLabels(["ID", "类型", "停留"])
        self.tbl_active.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_active.verticalHeader().setVisible(False)
        self.tbl_active.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_active.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl_active.setAlternatingRowColors(True)
        self.tbl_active.setMinimumHeight(88)
        self.tbl_active.setMaximumHeight(110)
        g6l.addWidget(self.tbl_active)
        pl.addWidget(g6)

        # --- 实时统计 ---
        g5 = QGroupBox("实时状态")
        g5l = QGridLayout(g5)
        g5l.setSpacing(4)
        g5l.setContentsMargins(9, 4, 9, 6)
        g5l.setColumnStretch(1, 1)
        self.lbl_frame = QLabel("0 / 0")
        self.lbl_inroi = QLabel("0")
        self.lbl_alarms = QLabel("0")
        self.lbl_infer = QLabel("—")
        for i, (k, v) in enumerate([("帧", self.lbl_frame),
                                    ("区域内目标", self.lbl_inroi),
                                    ("报警次数", self.lbl_alarms),
                                    ("推理耗时", self.lbl_infer)]):
            kl = self._dim(k)
            g5l.addWidget(kl, i, 0)
            g5l.addWidget(v, i, 1)
        pl.addWidget(g5)

        # 内容总高超过可用高度，因此套一个滚动区域：
        # 面板按内容所需的最小高度布局，不够时出滚动条，绝不压缩控件。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(240)
        scroll.setMaximumWidth(540)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget"
                             " { background: transparent; border: none; }")
        scroll.setWidget(panel)

        # 用分栏器包一层：分隔条可以拖动，用户能自己分配画面和控制面板的宽度
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(center)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)     # 画面吃掉多余空间
        splitter.setStretchFactor(1, 0)     # 控制面板保持宽度
        splitter.setSizes([1000, 350])

        lay.addWidget(splitter, 1)
        return page

    def _dim(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("CardDesc")
        return lbl

    # ---------------- 页面 2: 区域标定 ----------------
    def _build_roi_page(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        center = QFrame()
        center.setObjectName("Panel")
        cl = QVBoxLayout(center)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(8)

        bar = QHBoxLayout()
        cap = QLabel("标定画面")
        cap.setObjectName("SectionLabel")
        self.lbl_roi_hint = QLabel()
        self.lbl_roi_hint.setObjectName("CardDesc")
        bar.addWidget(cap)
        bar.addStretch(1)
        bar.addWidget(self.lbl_roi_hint)
        cl.addLayout(bar)

        self.roi_canvas = VideoCanvas()
        self.roi_canvas.quad_changed.connect(self._on_quad_changed)
        # 标定画布同步显示当前帧
        cl.addWidget(self.roi_canvas, 1)
        lay.addWidget(center, 1)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setMinimumWidth(330)
        panel.setMaximumWidth(420)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(13, 13, 13, 13)
        pl.setSpacing(10)

        # --- 模式选择 ---
        g0 = QGroupBox("编辑方式")
        g0l = QVBoxLayout(g0)
        g0l.setSpacing(6)
        self.combo_roi_mode = QComboBox()
        self.combo_roi_mode.addItems(["拖拽四点（自由变形）",
                                      "点击加点（任意多边形）"])
        self.combo_roi_mode.currentIndexChanged.connect(self.on_roi_mode_changed)
        g0l.addWidget(self.combo_roi_mode)

        self.lbl_roi_tip = QLabel()
        self.lbl_roi_tip.setObjectName("CardDesc")
        self.lbl_roi_tip.setWordWrap(True)
        g0l.addWidget(self.lbl_roi_tip)
        pl.addWidget(g0)

        # --- 操作按钮 ---
        g = QGroupBox("标定操作")
        gl = QVBoxLayout(g)
        gl.setSpacing(7)
        self.lbl_pts = QLabel("当前顶点：0 个")
        self.lbl_pts.setObjectName("SectionLabel")
        gl.addWidget(self.lbl_pts)

        self.b_roi_load = QPushButton("载入当前 ROI 到编辑器")
        self.b_roi_load.clicked.connect(self.on_roi_load_current)
        self.b_roi_undo = QPushButton("撤销上一个顶点")
        self.b_roi_undo.clicked.connect(self.on_roi_undo)
        self.b_roi_clear = QPushButton("清空当前多边形")
        self.b_roi_clear.clicked.connect(self.on_roi_clear)
        b_reset = QPushButton("删除全部 ROI")
        b_reset.clicked.connect(self.on_roi_reset)
        b_save = QPushButton("保存区域")
        b_save.setObjectName("Primary")
        b_save.clicked.connect(self.on_roi_save)
        for b in (self.b_roi_load, self.b_roi_undo, self.b_roi_clear, b_reset):
            gl.addWidget(b)
        gl.addWidget(b_save)
        pl.addWidget(g)

        g2 = QGroupBox("已有区域")
        g2l = QVBoxLayout(g2)
        g2l.setSpacing(7)
        self.lbl_roi_list = QLabel("—")
        self.lbl_roi_list.setObjectName("CardDesc")
        self.lbl_roi_list.setWordWrap(True)
        g2l.addWidget(self.lbl_roi_list)
        b_reload = QPushButton("重新加载 rois.json")
        b_reload.clicked.connect(self.on_reload_roi)
        g2l.addWidget(b_reload)
        pl.addWidget(g2)

        pl.addStretch(1)

        # 面板高度可能超过窗口，套滚动区（宽度仍随窗口变化）
        roi_scroll = QScrollArea()
        roi_scroll.setWidgetResizable(True)
        roi_scroll.setFrameShape(QFrame.NoFrame)
        roi_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        roi_scroll.setMinimumWidth(346)
        roi_scroll.setMaximumWidth(436)
        roi_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget"
                                 " { background: transparent; border: none; }")
        roi_scroll.setWidget(panel)
        lay.addWidget(roi_scroll)

        # 初始化模式（默认拖拽四点）
        self.on_roi_mode_changed(0)
        return page

    # ---------------- 页面 3: 报警记录 ----------------
    def _build_alarm_page(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # 左：记录表
        left = QFrame()
        left.setObjectName("Panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(13, 13, 13, 13)
        ll.setSpacing(9)

        t = QLabel("报警记录")
        t.setObjectName("CardTitle")
        ll.addWidget(t)

        self.tbl_alarms = QTableWidget(0, 6)
        self.tbl_alarms.setHorizontalHeaderLabels(
            ["#", "时间", "目标", "停留", "类型", "车辆信息"])
        self.tbl_alarms.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_alarms.verticalHeader().setVisible(False)
        self.tbl_alarms.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_alarms.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_alarms.setAlternatingRowColors(True)
        self.tbl_alarms.itemSelectionChanged.connect(self.on_alarm_selected)
        ll.addWidget(self.tbl_alarms, 1)

        row = QHBoxLayout()
        b_dir = QPushButton("打开截图目录")
        b_dir.clicked.connect(self.on_open_snapshot_dir)
        b_csv = QPushButton("导出 CSV")
        b_csv.clicked.connect(self.on_export_csv)
        b_clear = QPushButton("清空记录")
        b_clear.clicked.connect(self.on_clear_alarms)
        row.addWidget(b_dir)
        row.addWidget(b_csv)
        row.addWidget(b_clear)
        ll.addLayout(row)
        lay.addWidget(left, 1)

        # 右：大图预览 + 缩略图
        right = QFrame()
        right.setObjectName("Panel")
        right.setMinimumWidth(280)
        right.setMaximumWidth(560)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(13, 13, 13, 13)
        rl.setSpacing(9)

        t2 = QLabel("证据截图")
        t2.setObjectName("CardTitle")
        bar2 = QHBoxLayout()
        bar2.setSpacing(8)
        bar2.addWidget(t2)
        bar2.addStretch(1)
        self.lbl_ev_zoom = QLabel("100%")
        self.lbl_ev_zoom.setObjectName("CardDesc")
        bar2.addWidget(self.lbl_ev_zoom)
        self.btn_ev_fit = QPushButton("复位")
        self.btn_ev_fit.setObjectName("ghostButton")
        self.btn_ev_fit.setFixedHeight(26)
        self.btn_ev_fit.setCursor(Qt.PointingHandCursor)
        self.btn_ev_fit.setToolTip("把证据图恢复到适应窗口的大小")
        bar2.addWidget(self.btn_ev_fit)
        self.btn_ev_open = QPushButton("外部打开")
        self.btn_ev_open.setObjectName("ghostButton")
        self.btn_ev_open.setFixedHeight(26)
        self.btn_ev_open.setCursor(Qt.PointingHandCursor)
        self.btn_ev_open.setToolTip("用系统看图工具打开原图，可看到最大分辨率")
        bar2.addWidget(self.btn_ev_open)
        rl.addLayout(bar2)

        hint2 = QLabel("滚轮缩放 · 拖拽平移 · 双击复位（可放大看清车牌）")
        hint2.setObjectName("CardDesc")
        rl.addWidget(hint2)

        # 证据图预览用可缩放画布（原来是 QLabel，图片不能放大）
        self.evidence_canvas = VideoCanvas()
        self.evidence_canvas.setMinimumHeight(250)
        self.evidence_canvas.zoom_changed.connect(
            lambda s: self.lbl_ev_zoom.setText("{:.0f}%".format(s * 100)))
        self.btn_ev_fit.clicked.connect(self.evidence_canvas.fit)
        self.btn_ev_open.clicked.connect(self.on_open_evidence)
        self.evidence_canvas.clear_all("在左侧选择一条报警记录\n这里会显示对应的证据图\n\n"
                                      "显示后可用滚轮放大查看细节")
        rl.addWidget(self.evidence_canvas, 1)

        self.lbl_alarm_meta = QLabel("—")
        self.lbl_alarm_meta.setObjectName("CardDesc")
        self.lbl_alarm_meta.setWordWrap(True)
        rl.addWidget(self.lbl_alarm_meta)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(130)
        self.thumb_host = QWidget()
        self.thumb_grid = QGridLayout(self.thumb_host)
        self.thumb_grid.setContentsMargins(0, 0, 0, 0)
        self.thumb_grid.setSpacing(6)
        scroll.setWidget(self.thumb_host)
        rl.addWidget(scroll)

        lay.addWidget(right)
        return page

    # ---------------- 页面 4: 数据报告 ----------------
    def _build_report_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_frames = StatCard("已处理帧数", "0", "尚未开始", "accent")
        self.card_alarms = StatCard("占用报警", "0", "未触发", "alarm")
        self.card_maxdwell = StatCard("最长停留", "0.0 s", "—", "warn")
        self.card_ratio = StatCard("占用占比", "0%", "报警次数 / 处理帧数", "ok")
        for c in (self.card_frames, self.card_alarms, self.card_maxdwell, self.card_ratio):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        body = QHBoxLayout()
        body.setSpacing(14)

        # 停留时长分布（自绘条形图）
        chart_card = QFrame()
        chart_card.setObjectName("Card")
        chl = QVBoxLayout(chart_card)
        chl.setContentsMargins(16, 14, 16, 14)
        chl.setSpacing(8)
        ct = QLabel("各次报警的停留时长")
        ct.setObjectName("CardTitle")
        chl.addWidget(ct)
        self.chart = _DwellChart()
        chl.addWidget(self.chart, 1)
        body.addWidget(chart_card, 1)

        # 结论
        sum_card = QFrame()
        sum_card.setObjectName("Card")
        sl = QVBoxLayout(sum_card)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(9)
        st = QLabel("检测结论")
        st.setObjectName("CardTitle")
        sl.addWidget(st)
        self.lbl_summary = QLabel("尚未执行检测。\n\n在「实时监控」页点击「开始检测」后，"
                                  "这里会汇总本次结果。")
        self.lbl_summary.setObjectName("CardDesc")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setAlignment(Qt.AlignTop)
        sl.addWidget(self.lbl_summary, 1)

        row = QHBoxLayout()
        b_csv2 = QPushButton("导出证据 CSV")
        b_csv2.setObjectName("Primary")
        b_csv2.clicked.connect(self.on_export_csv)
        b_open2 = QPushButton("打开输出目录")
        b_open2.clicked.connect(self.on_open_output_dir)
        row.addWidget(b_csv2)
        row.addWidget(b_open2)
        row.addStretch(1)
        sl.addLayout(row)
        body.addWidget(sum_card, 1)

        lay.addLayout(body, 1)
        return page

    # ---------------- 底部播放控制条 ----------------
    def _build_playback_bar(self):
        bar = QFrame()
        bar.setObjectName("Panel")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(13, 9, 13, 9)
        lay.setSpacing(10)

        self.btn_start = QPushButton("开始检测")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setMinimumWidth(112)
        self.btn_start.clicked.connect(self.on_start)

        # 「重新开始」：把视频拉回第 0 帧。视频已经在开头时该按钮置灰不可点。
        self.btn_restart = QPushButton("重新开始")
        self.btn_restart.setMinimumWidth(96)
        self.btn_restart.setToolTip("把视频回到最开始重新播放（已在开头时不可点）")
        self.btn_restart.setEnabled(False)
        self.btn_restart.clicked.connect(self.on_restart)

        # 「暂停 / 继续 / 停止」三态合一（原「停止」按钮已按需求去掉）：
        #   检测中 -> 暂停 ；已暂停 -> 停止（结束本次检测） ；空闲 -> 开始检测
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setMinimumWidth(88)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setToolTip("检测中：暂停/继续。暂停后再点一次即结束本次检测；"
                                  "空闲时点它等于开始检测。")
        self.btn_pause.clicked.connect(self.on_pause)

        lay.addWidget(self.btn_start)
        lay.addWidget(self.btn_restart)
        lay.addWidget(self.btn_pause)
        lay.addSpacing(10)

        # 进度条：始终可拖动。检测中拖动＝跳到该时间点重播；
        # 还没开始检测时拖动＝设定起始播放位置。
        self.slider_pos = QSlider(Qt.Horizontal)
        self.slider_pos.setRange(0, 1000)
        self.slider_pos.setEnabled(False)          # 载入视频后才可用
        self.slider_pos.setToolTip("拖动可跳到任意时间点")
        self.slider_pos.sliderPressed.connect(self._on_seek_press)
        self.slider_pos.sliderReleased.connect(self._on_seek_release)
        self.slider_pos.valueChanged.connect(self._on_slider_value_changed)
        lay.addWidget(self.slider_pos, 1)

        self.lbl_time = QLabel("00:00 / 00:00")
        # 用等宽字体（见 style.qss 的 #TimeLabel），否则秒数跳动时整行会左右抖
        self.lbl_time.setObjectName("TimeLabel")
        self.lbl_time.setMinimumWidth(104)
        self.lbl_time.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.lbl_time)

        self.playback_bar = bar

    # ==================================================================
    #  页面切换
    # ==================================================================
    def switch_page(self, index):
        if index < 0 or index >= len(self.PAGES):
            return
        self.stack.setCurrentIndex(index)
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == index)
        self.lbl_page_title.setText(self.PAGES[index][0])
        self.lbl_page_sub.setText(self.PAGES[index][1])

        # 播放控制只在需要的页面出现
        self.playback_bar.setVisible(index in (0, 1))
        if index == 2:
            self._rebuild_thumbnails()
        if index == 3:
            self._refresh_report()
        if index in (0, 1):
            self._sync_canvas_frame()

    def _sync_canvas_frame(self):
        """两个页面共用同一帧：切页时把当前画面推过去"""
        if self.canvas._pixmap is not None:
            self.roi_canvas.set_frame_from_pixmap(self.canvas._pixmap)
            self.roi_canvas.set_rois(self.rois)
            self.roi_canvas._draw_pts = list(self._cur_roi_poly)
            self.roi_canvas.update()

    # ==================================================================
    #  视频源
    # ==================================================================
    def _use_configured_source(self):
        """把 settings.json 里配的视频源真正装载进来。

        注意：不要在这里无条件回退到测试视频 —— 那会把用户配好的视频覆盖掉。
        """
        raw = self.cfg.get("video_source", "")
        if not raw:
            self.statusBar().showMessage("配置里没有视频源，请手动选择视频", 6000)
            return False

        # 摄像头 / RTSP 这类不是文件路径，不做存在性检查
        if not (str(raw).isdigit() or str(raw).startswith("rtsp")):
            path = abs_path(raw)
            if not os.path.exists(path):
                self.statusBar().showMessage(
                    "配置里的视频不存在：{}".format(raw), 8000)
                return False
            self.edit_video.setText(os.path.basename(path))
            self.edit_video.setToolTip(path)
        else:
            self.edit_video.setText(str(raw))
            self.edit_video.setToolTip(str(raw))

        self.combo_source.setCurrentIndex(0)
        self._load_video_meta()
        self._show_first_frame()
        return True

    def _find_test_video(self):
        """找可用的测试视频。

        修复记录：原来写死 videos/test_blindway.mp4，但仓库里实际的文件叫
        demo_blindway.mp4 / demo_blindway_long.mp4 —— 文件名对不上，
        导致「用测试视频」按钮永远弹"未找到"，界面自检也因此在同一处崩掉。
        现在按候选名依次找，并兜底扫 videos/ 下的任意 mp4。
        """
        vdir = os.path.join(BASE_DIR, "videos")
        for name in ("demo_blindway.mp4", "test_blindway.mp4",
                     "demo_blindway_long.mp4"):
            p = os.path.join(vdir, name)
            if os.path.exists(p):
                return p
        # 兜底：videos 目录下任意一个视频
        try:
            if os.path.isdir(vdir):
                vids = sorted(f for f in os.listdir(vdir)
                              if f.lower().endswith((".mp4", ".avi", ".mov")))
                if vids:
                    return os.path.join(vdir, vids[0])
        except Exception:
            pass
        return None

    def _maybe_use_test_video(self, initial=False):
        path = self._find_test_video()
        if not path:
            # 自动化环境（界面自检）里弹模态框会直接崩，且没人能点确认。
            # 这里改成只有交互式启动时才提示，自检时静默跳过。
            if not initial and os.environ.get("BLINDWAY_SELFTEST") != "1":
                QMessageBox.warning(
                    self, "提示",
                    "在 videos 目录下没找到测试视频。\n\n"
                    "请把视频放到 videos/ 下（支持 mp4/avi/mov），\n"
                    "或点「选择视频…」手动挑一个。")
            else:
                self.statusBar().showMessage("未找到测试视频，已跳过", 5000)
            return
        self.cfg["video_source"] = path
        self.edit_video.setText(os.path.basename(path))
        self.edit_video.setToolTip(path)
        self.combo_source.setCurrentIndex(0)
        self._load_video_meta()
        self._show_first_frame()
        self.statusBar().showMessage(
            "已加载测试视频：{}".format(os.path.basename(path)), 5000)

    def on_select_video(self):
        start = os.path.join(BASE_DIR, "videos")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", start,
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)")
        if not path:
            return
        self.cfg["video_source"] = path
        self.edit_video.setText(os.path.basename(path))
        self.edit_video.setToolTip(path)
        self.combo_source.setCurrentIndex(0)
        self._load_video_meta()
        self._show_first_frame()
        self.statusBar().showMessage("已选择：{}".format(os.path.basename(path)), 5000)

    def on_source_type_changed(self, idx):
        if idx == 0:
            return
        if idx == 1:
            self.cfg["video_source"] = "0"
            self.edit_video.setText("本地摄像头 (index 0)")
            self.edit_video.setReadOnly(True)
        else:
            self.cfg["video_source"] = "rtsp://"
            self.edit_video.setText("rtsp://  （请在这里填写地址）")
            self.edit_video.setReadOnly(False)
        self._load_video_meta()
        self.statusBar().showMessage("已切换视频源类型", 4000)

    # ==================================================================
    #  监测地点（可填写 / 可保存，会写进证据图）
    # ==================================================================
    def _load_location(self):
        """把配置里的地点填进输入框。"""
        loc = str(self.cfg.get("location", "") or "").strip()
        self.edit_location.setText(loc)
        if loc:
            self.lbl_location_hint.setText("当前地点：{}".format(loc))
            self.lbl_location_hint.setStyleSheet("color:#23855A;")
        else:
            self.lbl_location_hint.setText("尚未填写地点，报警证据图上会留空")
            self.lbl_location_hint.setStyleSheet("color:#8A6410;")

    def on_save_location(self):
        """把输入框里的地点写进 config/settings.json，之后一直生效。"""
        loc = self.edit_location.text().strip()
        self.cfg["location"] = loc

        # 写回配置文件（保留其它字段）
        path = os.path.join(BASE_DIR, "config", "settings.json")
        try:
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["location"] = loc
            # 顺手把界面上可调的参数也一起存下来，避免下次又被配置覆盖回去
            data["dwell_threshold_sec"] = float(self.spin_dwell.value())
            data["confidence"] = float(self.spin_conf.value())
            data["confirm_frames"] = int(self.spin_confirm.value())
            data["alarm_cooldown_sec"] = float(self.spin_cooldown.value())
            data["alarm_repeat_enabled"] = bool(self.chk_repeat.isChecked())
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", "无法写入配置文件：\n{}".format(e))
            return

        if loc:
            self.lbl_location_hint.setText("已保存：{}".format(loc))
            self.lbl_location_hint.setStyleSheet("color:#23855A;")
            self.statusBar().showMessage("监测地点已保存：{}".format(loc), 6000)
        else:
            self.lbl_location_hint.setText("地点已清空")
            self.lbl_location_hint.setStyleSheet("color:#8A6410;")
            self.statusBar().showMessage("监测地点已清空", 5000)

    def _load_video_meta(self):
        self.total_frames = 0
        self.fps = 25.0
        self.frame_w = self.frame_h = 0
        src = self._resolve_source()
        if src is None or str(src).startswith("rtsp"):
            return
        cap = cv2.VideoCapture(src)
        if cap.isOpened():
            self.fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            if self.fps < 1:
                self.fps = 25.0
            self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        # 载入视频后允许手动拖动进度条
        if self.slider_pos is not None:
            self.slider_pos.setEnabled(self.total_frames > 0)
        self._update_time_label(0)

    def _resolve_source(self):
        raw = self.cfg.get("video_source", "")
        if raw in ("", None):
            return None
        if raw.isdigit():
            return int(raw)
        if raw.startswith("rtsp"):
            return raw
        return abs_path(raw)

    def _show_frame_at(self, idx):
        """把画布显示到指定帧（未开始检测时拖动进度条用）。

        还没跑检测，所以不画检测框，只把画面跳过去、并按当前 ROI 画区域。
        """
        src = self._resolve_source()
        if src is None or str(src).startswith("rtsp"):
            return
        idx = max(0, int(idx))
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            return
        if idx > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return
        self._push_frame_to_canvases(frame)
        self.canvas.set_boxes([])
        self.canvas.set_alert(False)
        self.canvas.set_hud("预览第 {} 帧（{:.1f} 秒）· 点「开始检测」从这里开始".format(
            idx, idx / max(1.0, self.fps)))

    def _show_first_frame(self):
        """打开视频立刻显示首帧 —— 不用等模型加载"""
        self._pending_start_frame = 0
        self._show_frame_at(0)
        self.canvas.set_hud("已加载首帧 · 点击「开始检测」运行 AI 检测")

    def _push_frame_to_canvases(self, frame):
        self.canvas.set_frame(frame, keep_view=False)
        self.roi_canvas.set_frame_from_pixmap(self.canvas._pixmap)
        self.roi_canvas.set_rois(self.rois)

    # ==================================================================
    #  ROI
    # ==================================================================
    def _refresh_roi_status(self):
        if self.rois:
            pts = sum(len(r) for r in self.rois)
            text = "已加载 {} 个区域".format(len(self.rois))
            detail = "{}\n共 {} 个顶点".format(text, pts)
            self.lbl_roi.setText(detail)
            self.lbl_roi.setStyleSheet("color:#23855A;")
            self.lbl_roi_side.setText("ROI {} 个区域".format(len(self.rois)))
            self.lbl_roi_side.setStyleSheet("color:#23855A;")
            lines = []
            for i, r in enumerate(self.rois):
                lines.append("区域 {}：{} 个顶点".format(i + 1, len(r)))
            self.lbl_roi_list.setText("\n".join(lines))
        else:
            self.lbl_roi.setText("未配置 ROI\n整幅画面都视为盲道")
            self.lbl_roi.setStyleSheet("color:#8A6410;")
            self.lbl_roi_side.setText("ROI 未配置")
            self.lbl_roi_side.setStyleSheet("color:#8A6410;")
            self.lbl_roi_list.setText("（空）")
        self.canvas.set_rois(self.rois)
        self.roi_canvas.set_rois(self.rois)

    # ---------------- ROI 编辑模式 ----------------
    def on_roi_mode_changed(self, idx):
        """0 = 拖拽四点（自由变形）；1 = 点击加点（任意多边形）"""
        drag_mode = (idx == 0)
        # ★ 记住当前模式。on_roi_save() 依据它决定"替换"还是"追加"。
        #   之前是直接读 roi_canvas._edit_mode，但画布状态可能被别的流程
        #   （载入、复位、切页）改掉，于是界面显示"点击加点"却走了"替换"分支，
        #   表现就是追加区域保存后区域数不变、自检报 before=1 after=1。
        #   改成以下拉框为单一来源。
        self._roi_drag_mode = drag_mode
        self.roi_canvas.set_edit_mode(drag_mode)
        self.roi_canvas.set_draw_mode(not drag_mode)

        # 按钮按模式启用/禁用，避免误操作
        self.b_roi_load.setEnabled(drag_mode)
        self.b_roi_undo.setEnabled(not drag_mode)
        self.b_roi_clear.setEnabled(True)
        self.lbl_roi_tip.setVisible(drag_mode)

        if drag_mode:
            self.lbl_roi_hint.setText("拖动 4 个圆点自由变形 · 滚轮缩放 · 拖拽平移")
            self.lbl_roi_tip.setText(
                "画面上有 4 个可拖拽的圆点（编号 1~4），\n"
                "按住任意一个拖动即可自由拉伸盲道区域。\n"
                "点「载入当前 ROI」把已保存的区域读进编辑器。")
            # 若还没有四边形，自动把已保存的 ROI 载入
            if self.roi_canvas.quad() is None and self.rois:
                self.on_roi_load_current()
        else:
            self.lbl_roi_hint.setText("左键单击加顶点 · 右键撤销 · 滚轮缩放 · 拖拽平移")
            self.lbl_roi_tip.setVisible(False)

        self._update_pts_label()

    def _quad_from_region(self, region):
        """把一个多边形（可能多于 4 点）换算成 4 个角点。

        做法：从**多边形自身的顶点**里穷举所有 4 点组合，取**包围面积最大**的那个，
        再按绕质心的极角排序，保证得到一个不自交的凸四边形。

        为什么不用 minAreaRect：最小外接矩形对细长/L 形区域会退化成一条线，
        四点会挤在一起（实测出现过角点重合、四边形伸到画面外的情况）。
        直接在原顶点里挑面积最大的四点，结果始终贴合原区域。
        """
        pts = np.asarray(region, dtype=np.float32).reshape(-1, 2)
        n = len(pts)
        if n <= 4:
            return self._order_quad([tuple(map(int, p)) for p in pts])

        def area4(idx):
            sub = pts[list(idx)].astype(np.float32).reshape(-1, 1, 2)
            return abs(cv2.contourArea(sub))

        if n <= 40:
            # 顶点不多，穷举 C(n,4) 找面积最大的组合
            import itertools
            best, best_a = None, -1.0
            for combo in itertools.combinations(range(n), 4):
                a = area4(combo)
                if a > best_a:
                    best_a, best = a, combo
            sel = pts[list(best)]
        else:
            # 顶点太多：用最小外接矩形的 4 个角吸附到最近原顶点（足够接近）
            rect = cv2.minAreaRect(pts)
            box = cv2.boxPoints(rect)
            out = []
            for corner in box:
                d = np.linalg.norm(pts - corner, axis=1)
                out.append(pts[int(np.argmin(d))])
            sel = np.asarray(out, dtype=np.float32)

        return self._order_quad([tuple(map(int, p)) for p in sel])

    @staticmethod
    def _order_quad(pts):
        """把 4 个点按绕质心的极角排序，得到一个不自交的四边形。

        顺便检查反向顺序是否面积更大（凸包顶点是顺时针时用反向更贴合）。
        """
        if not pts:
            return pts
        pts = [tuple(map(int, p)) for p in pts]
        if len(pts) != 4:
            return pts
        cx = sum(p[0] for p in pts) / 4.0
        cy = sum(p[1] for p in pts) / 4.0
        import math
        ordered = sorted(pts, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))

        def poly_area(seq):
            a = 0.0
            for i in range(len(seq)):
                x1, y1 = seq[i]
                x2, y2 = seq[(i + 1) % len(seq)]
                a += x1 * y2 - x2 * y1
            return a / 2.0

        if abs(poly_area(list(reversed(ordered)))) > abs(poly_area(ordered)):
            ordered = list(reversed(ordered))
        return ordered

    def on_roi_load_current(self):
        """把已保存的第一个 ROI 载入编辑器，供拖拽变形。"""
        if not self.rois:
            QMessageBox.information(
                self, "暂无 ROI",
                "当前没有已保存的盲道区域。\n\n"
                "可以切到「点击加点」模式自己画一个，或先保存一个区域。")
            return
        quad = self._quad_from_region(self.rois[0])
        self.roi_canvas.set_quad(quad)
        self.roi_canvas.set_edit_mode(True)
        self._update_pts_label()
        self.statusBar().showMessage("已把当前 ROI 载入编辑器，拖动圆点即可调整", 5000)

    def _on_quad_changed(self):
        """四点被拖动时实时刷新顶点信息。"""
        self._update_pts_label()

    # ---------------- 多边形合法性 ----------------
    @staticmethod
    def _is_self_intersecting(pts):
        """判断多边形是否自相交（蝴蝶结形状）。

        自相交的 ROI 在语义上是矛盾的（一块区域不该自己穿过自己），
        而且会让"四点拖拽"无法正确表示它，所以存盘前要挡掉。
        """
        p = [tuple(map(float, q)) for q in pts]
        n = len(p)
        if n < 4:
            return False

        def seg_hit(a, b, c, d):
            def cross(o, u, v):
                return ((u[0] - o[0]) * (v[1] - o[1])
                        - (u[1] - o[1]) * (v[0] - o[0]))
            d1, d2 = cross(c, d, a), cross(c, d, b)
            d3, d4 = cross(a, b, c), cross(a, b, d)
            return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

        for i in range(n):
            a, b = p[i], p[(i + 1) % n]
            for j in range(i + 1, n):
                # 相邻边共享端点，跳过
                if j == i or (j + 1) % n == i or (i + 1) % n == j:
                    continue
                c, d = p[j], p[(j + 1) % n]
                if seg_hit(a, b, c, d):
                    return True
        return False

    @staticmethod
    def _convex_hull_points(pts):
        """用 OpenCV 求凸包，返回点列表"""
        arr = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
        hull = cv2.convexHull(arr)
        return [tuple(map(int, q[0])) for q in hull]

    def on_roi_undo(self):
        self.roi_canvas.pop_draw_point()
        self._cur_roi_poly = self.roi_canvas.draw_points()
        self._update_pts_label()

    def on_roi_clear(self):
        self.roi_canvas.clear_draw_points()
        self.roi_canvas.set_quad(None)
        self._cur_roi_poly = []
        self._update_pts_label()

    def on_roi_reset(self):
        if not self.rois:
            QMessageBox.information(self, "提示", "当前没有已保存的 ROI。")
            return
        r = QMessageBox.question(self, "删除全部 ROI",
                                 "确定删除全部盲道区域吗？\n删除后整幅画面都会被视为盲道。",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.rois = []
        self._write_rois([])
        self._refresh_roi_status()
        self.canvas.set_rois([])
        self.statusBar().showMessage("已删除全部 ROI", 5000)

    def on_roi_save(self):
        """保存当前编辑结果。

        拖拽四点模式：这是**整体重画**盲道区域，因此替换掉原来保存的区域。
        点击加点模式：作为**新增**区域追加（支持一块盲道拆成多段的情况）。
        """
        # 整个保存过程屏蔽文件监听器，避免它读到写了一半的文件
        self._roi_saving = True
        try:
            return self._on_roi_save_impl()
        finally:
            self._roi_saving = False

    def _on_roi_save_impl(self):
        quad = self.roi_canvas.quad()
        pts = self.roi_canvas.draw_points()
        hull_note = ""

        # 以下拉框记录的模式为准（单一来源），不读画布内部状态 —— 画布状态
        # 可能被载入/复位等流程改动，导致"显示点击加点却走了替换分支"。
        if self._roi_drag_mode and quad:
            poly = [[int(x), int(y)] for x, y in quad]
            regions = [{"name": "blindway", "polygon": poly}]
            mode_desc = "拖拽四点"
        else:
            if len(pts) < 3:
                QMessageBox.warning(
                    self, "顶点不足",
                    "至少需要 3 个顶点才能构成多边形。\n当前只有 {} 个。".format(len(pts)))
                return

            # ---- 自相交保护 ----
            # 画成"蝴蝶结"（两块区域连成一个多边形）时，这个多边形在几何上是
            # 自相矛盾的：四点编辑器无法表示它，后面也会退化成畸形四边形。
            # 这里直接换成它的凸包，并在状态栏说明。
            if self._is_self_intersecting(pts):
                hull = self._convex_hull_points(pts)
                r = QMessageBox.question(
                    self, "多边形自相交",
                    "你画的多边形有边交叉（像蝴蝶结）。\n\n"
                    "这种形状无法用「拖拽四点」继续编辑，也会让判定区域变得不可预期。\n\n"
                    "是否改为按**凸包**保存？\n"
                    "（凸包 = 把所有顶点包起来的最小凸多边形，会填充交叉区域）\n\n"
                    "如果要两块**分开**的盲道区域，请取消后分别画两次，"
                    "每次点「保存区域」追加。",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if r != QMessageBox.Yes:
                    return
                pts = hull
                hull_note = "（原多边形自相交，已按凸包保存）"

            regions = []
            for r in (self.rois or []):
                arr = np.asarray(r)
                regions.append({"name": "blindway", "polygon": arr.astype(int).tolist()})
            regions.append({"name": "blindway",
                            "polygon": [[int(x), int(y)] for x, y in pts]})
            mode_desc = "点击加点"

        self._write_rois(regions)
        # ★ 不要在这里 load_rois() 回读磁盘。
        #   原来写成 self.rois = load_rois()，而写文件会更新 mtime，紧接着
        #   _roi_timer 的文件监听回调也会重载一次 —— 两个重载撞在一起时，
        #   监听回调可能读到"写入过程中"的文件（区域还没写完），把 self.rois
        #   覆盖成旧值。实测表现：磁盘上明明有 2 个区域，内存里只剩 1 个，
        #   界面显示"共 1 个区域"，追加保存看起来失败。
        #   现在直接用刚写出去的数据构造，不依赖回读。
        self.rois = [np.array(r["polygon"], np.int32) for r in regions] or None
        self.roi_canvas.clear_draw_points()
        self.roi_canvas.set_quad(None)
        self._cur_roi_poly = []
        self._update_pts_label()
        self._refresh_roi_status()

        # 让引擎立刻吃到新 ROI
        if self.engine is not None:
            self.engine.rois = self.rois
        if len(regions) == 1 and mode_desc == "拖拽四点":
            self.statusBar().showMessage("盲道区域已保存（拖拽的四边形已生效）", 6000)
        else:
            self.statusBar().showMessage(
                "ROI 已保存并生效（共 {} 个区域）{}".format(
                    len(self.rois), hull_note), 6000)

    def _write_rois(self, regions):
        data = {
            "video_source": self.cfg.get("video_source", ""),
            "frame_size": [self.frame_w or 960, self.frame_h or 540],
            "regions": regions,
        }
        path = os.path.join(BASE_DIR, "config", "rois.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._roi_watch_mtime = os.path.getmtime(path)

    def on_reload_roi(self):
        self.rois = load_rois()
        if self.engine is not None:
            self.engine.rois = self.rois
        self._refresh_roi_status()
        self.statusBar().showMessage("rois.json 已重新加载", 5000)

    def _update_pts_label(self):
        quad = self.roi_canvas.quad()
        if quad and self.roi_canvas._edit_mode:
            txt = "四边形四个角点："
            for i, (x, y) in enumerate(quad):
                txt += "\n  {}. ({}, {})".format(i + 1, x, y)
            self.lbl_pts.setText(txt)
        else:
            n = len(self.roi_canvas.draw_points())
            self.lbl_pts.setText("当前顶点：{} 个".format(n))

    def _watch_roi_file(self):
        # 正在保存中就不要重载 —— 否则会读到写了一半的文件，
        # 把刚保存的区域覆盖掉（详细说明见 on_roi_save 里的注释）。
        if getattr(self, "_roi_saving", False):
            return
        path = os.path.join(BASE_DIR, "config", "rois.json")
        if not os.path.exists(path):
            return
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return
        if self._roi_watch_mtime and mt > self._roi_watch_mtime:
            self._roi_watch_mtime = mt
            self.rois = load_rois()
            if self.engine is not None:
                self.engine.rois = self.rois
            self._refresh_roi_status()
            self.statusBar().showMessage("检测到 rois.json 更新，已自动重载", 5000)
        elif not self._roi_watch_mtime:
            self._roi_watch_mtime = mt

    # ==================================================================
    #  检测控制
    # ==================================================================
    def _current_device(self):
        """取当前真正生效的推理设备名。

        下拉框在无 GPU 时会多一个禁用的占位项（"cuda（本机无可用 GPU）"），
        那个文字不能当设备名传给 ultralytics，否则又会触发
        "Invalid CUDA 'device=0' requested"。所以这里做一次判定。
        """
        txt = self.combo_device.currentText().strip()
        if txt == "cpu" or txt.startswith("cuda:"):
            return txt
        # 占位项 / 任何意外文本 -> 一律 CPU，保证不会崩
        return "cpu"

    def _collect_cfg(self):
        cfg = dict(self.cfg)
        cfg.update({
            "dwell_threshold_sec": self.spin_dwell.value(),
            "confidence": self.spin_conf.value(),
            "confirm_frames": self.spin_confirm.value(),
            "alarm_cooldown_sec": self.spin_cooldown.value(),
            "alarm_repeat_enabled": self.chk_repeat.isChecked(),
            # 设备名只在 CPU 或真正可用的 cuda:N 里取；
            # 那个"cuda（本机无可用 GPU）"的禁用占位项不能被当成设备名。
            "device": self._current_device(),
            "save_snapshot": self.chk_snapshot.isChecked(),
            "play_sound": self.chk_sound.isChecked(),
            "save_output_video": self.chk_record.isChecked(),
            # 监测地点：以输入框当前内容为准（不必先点保存也能带进证据图）
            "location": self.edit_location.text().strip(),
            "_fps": self.fps,
        })
        # detector.py 是 os.path.join(BASE_DIR, "output/alerts")，
        # 在 Windows 上会拼成 "output/alerts\alarm_xxx.jpg"（斜杠混用）。
        # 这里提前归一化，保证写进 CSV 和界面里的路径是干净的。
        cfg["snapshot_dir"] = os.path.normpath(
            cfg.get("snapshot_dir", os.path.join("output", "alerts")))
        if cfg.get("output_video"):
            cfg["output_video"] = os.path.normpath(cfg["output_video"])
        if cfg.get("video_source"):
            cfg["video_source"] = os.path.normpath(cfg["video_source"])
        return cfg

    def _push_params(self):
        """参数改动即时同步到正在跑的引擎"""
        if self.engine is None:
            return
        self.engine.dwell_limit = float(self.spin_dwell.value())
        self.engine.confidence = float(self.spin_conf.value())
        self.engine.confirm_frames = int(self.spin_confirm.value())
        self.engine.cooldown = float(self.spin_cooldown.value())
        self.engine.alarm_repeat = bool(self.chk_repeat.isChecked())

    def on_start(self):
        if self.worker is not None:
            return
        src = self._resolve_source()
        if src is None:
            QMessageBox.warning(self, "提示", "请先选择视频文件。")
            return

        self.rois = load_rois()
        self._refresh_roi_status()
        if not self.rois:
            r = QMessageBox.question(
                self, "未配置 ROI",
                "当前没有盲道 ROI 区域，整幅画面都会被当成盲道。\n\n"
                "建议先到「区域标定」页框出盲道。\n\n是否仍要继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        # 重置统计
        self.alarms = []
        self.tbl_alarms.setRowCount(0)
        self.detected_classes = {}
        self.max_dwell = 0.0
        self.processed_frames = 0
        self.detect_seconds = 0.0
        self.paused = False
        self._roi_watch_mtime = 0
        self._refresh_report()

        cfg = self._collect_cfg()
        # 未开始检测时若拖过进度条，从那个位置开始播
        start_frame = int(getattr(self, "_pending_start_frame", 0) or 0)
        if start_frame > 0 and self.total_frames > 0:
            cfg["start_frame"] = min(start_frame, self.total_frames - 1)
        self.worker = DetectWorker(cfg, self.rois, evidence=self.evidence, parent=self)
        self.worker.frame_ready.connect(self.on_frame)
        self.worker.progress.connect(self.on_progress)
        self.worker.status.connect(lambda s: self.statusBar().showMessage(s))
        self.worker.finished_all.connect(self.on_finished)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停")
        self.slider_pos.setEnabled(True)
        # 从第 0 帧开始时，「重新开始」没有意义，保持置灰
        self._reached_start = (start_frame <= 1)
        self._last_frame_idx = start_frame
        self._refresh_restart_enabled()
        self._set_badge(self.badge_status, "检测中", "IdleBadge")

        # 设备不可用时会自动回退到 CPU —— 必须让用户知道，不能默默降级。
        # （在下拉框选了 cuda 但本机没 GPU，以前会直接抛
        #   "Invalid CUDA 'device=0' requested" 崩掉，现在改为提示 + 用 CPU 继续跑）
        _req = str(self.cfg.get("device", "cpu")).strip()
        if _req.lower() not in ("cpu", "") and not self._cuda_ok:
            QMessageBox.information(
                self, "已自动改用 CPU 运行",
                "你选择的是 {}\n\n"
                "但这台机器没有可用的 CUDA（torch.cuda.is_available() = False），"
                "系统已自动改用 CPU，检测会照常进行，只是速度比 GPU 慢一些\n"
                "（本机实测约 51~71 ms/帧，拍演示视频完全够用）。\n\n"
                "想用 GPU 的话需要安装 CUDA 版 PyTorch。".format(_req))
        self.lbl_engine_info.setText("YOLOv8n · {}".format(self._current_device().upper()))

        if start_frame > 0:
            self.statusBar().showMessage(
                "正在加载 YOLOv8n 模型 ...（将从第 {} 帧开始）".format(start_frame))
        else:
            self.statusBar().showMessage("正在加载 YOLOv8n 模型 ...")

    def on_pause(self):
        """暂停按钮的三态行为（原来的「停止」已并入这里）。

        1. 检测中          -> 暂停
        2. 已暂停          -> 结束本次检测（等价于原来的「停止」）
        3. 不在检测（空闲）-> 从头开始检测
        """
        if self.worker is None:
            # 空闲态：当作「开始检测」用
            self.btn_pause.setText("暂停")
            self.statusBar().showMessage("没有正在进行的检测，改为开始检测 ...", 4000)
            self.on_start()
            return

        if not self.paused:
            # 态 1：暂停
            self.paused = True
            self.worker.set_paused(True)
            self.btn_pause.setText("继续")
            self._set_badge(self.badge_status, "已暂停", "IdleBadge")
            self.statusBar().showMessage(
                "已暂停。点「继续」接着检测；想结束本次检测可直接拖进度条换位置、"
                "或点「重新开始」。", 6000)
            return

        # 态 2：继续
        self.paused = False
        self.worker.set_paused(False)
        self.btn_pause.setText("暂停")
        self._set_badge(self.badge_status, "检测中", "IdleBadge")
        self.statusBar().showMessage("已继续检测", 3000)

    def _finish_detection(self, paused=False):
        """结束本次检测（原「停止」按钮的逻辑，现并入暂停按钮/内部调用）。"""
        w = self.worker
        if w is not None:
            w.stop()
            w.wait(4000)
            self.worker = None
        self._reset_controls()
        # 结束不是"回到开头"：视频停在当前位置，所以「重新开始」应可用
        self._reached_start = False
        self._refresh_restart_enabled()
        self._set_badge(self.badge_status, "已停止", "IdleBadge")
        self.statusBar().showMessage(
            "已结束本次检测。可拖进度条换位置后点「开始检测」，或点「重新开始」回到开头。",
            6000)

    def on_stop(self):
        """兼容入口：原「停止」按钮已去掉，逻辑并入 _finish_detection()。"""
        self._finish_detection()

    def _reset_controls(self):
        self.btn_start.setEnabled(True)
        # 空闲态：暂停按钮仍可点，点它等于开始检测（原「停止」已并入这里）
        self.btn_pause.setEnabled(self.total_frames > 0)
        # 停止/结束时要连文字一起复位，否则会停在「继续」上
        self.btn_pause.setText("暂停")
        # 进度条保持可用：停止后仍能手动调位置、或拖回开头
        self.slider_pos.setEnabled(self.total_frames > 0)
        self.paused = False

    def on_finished(self, total):
        self.worker = None
        self._reset_controls()
        self._set_badge(self.badge_status, "检测完成", "SafeBadge")
        self._refresh_report()
        # 已经播到结尾，「重新开始」这时最有意义
        self._reached_start = False
        self._last_frame_idx = max(self._last_frame_idx, total)
        self._refresh_restart_enabled()
        self.statusBar().showMessage(
            "处理完成，共 {} 帧，报警 {} 次".format(total, len(self.alarms)))
        QMessageBox.information(
            self, "检测完成",
            "本次检测完成 ✔\n\n"
            "处理帧数：{}\n"
            "占用报警：{} 次\n"
            "最长停留：{:.1f} 秒\n\n"
            "可到「报警记录」查看证据截图，或到「数据报告」导出 CSV。".format(
                total, len(self.alarms), self.max_dwell))

    def on_error(self, msg):
        self.worker = None
        self._reset_controls()
        self._set_badge(self.badge_status, "出错", "AlarmBadge")
        QMessageBox.critical(self, "运行错误", msg)

    # ==================================================================
    #  帧回调
    # ==================================================================
    def on_frame(self, frame, res):
        try:
            self.processed_frames = res["frame_idx"]
            self.last_result = res
            self._last_frame_idx = self.processed_frames

            boxes = []
            for d in res["dets"]:
                boxes.append({
                    "box": [int(v) for v in d["box"]],
                    "id": d["id"],
                    "inside": bool(d.get("inside")),
                    "occupied": bool(d.get("occupied")),
                    "dwell": float(d.get("dwell", 0.0)),
                    "label": d.get("label", ""),
                })

            self.canvas.set_frame(frame, keep_view=True)
            self.canvas.set_boxes(boxes)
            self.canvas.set_rois(self.rois)
            self.canvas.set_alert(bool(res.get("occupied")))
            self.canvas.set_hud("Frame {} | 区域内 {} | 报警 {} | 阈值 {:.1f}s".format(
                res["frame_idx"], res["in_roi"], res["alarm_count"], self.spin_dwell.value()))

            # 标定画布也保持同步（但不画检测框，免得太乱）
            self.roi_canvas.set_frame_from_pixmap(self.canvas._pixmap)
            self.roi_canvas.set_rois(self.rois)

            # ---- 右侧实时统计 ----
            self.lbl_frame.setText("{} / {}".format(res["frame_idx"], self.total_frames or "?"))
            self.lbl_inroi.setText(str(res["in_roi"]))
            self.lbl_alarms.setText(str(res["alarm_count"]))
            self.badge_zone.setText("区域内 {}".format(res["in_roi"]))
            self.badge_alarm.setText("报警 {} 次".format(res["alarm_count"]))

            # ---- 区域内目标表 ----
            self._refresh_active_table(res["dets"])

            # ---- 徽章状态 ----
            if res.get("occupied"):
                self._set_badge(self.badge_status, "⚠ 盲道占用", "AlarmBadge")
                self._set_badge(self.badge_alarm, "报警 {} 次".format(res["alarm_count"]),
                                "AlarmBadge")
            elif res["in_roi"] > 0:
                self._set_badge(self.badge_status, "区域内停留中", "WarnBadge")
                self._set_badge(self.badge_alarm, "报警 {} 次".format(res["alarm_count"]),
                                "IdleBadge")
            else:
                self._set_badge(self.badge_status, "检测中 · 正常", "SafeBadge")

            # ---- 新报警 ----
            for rec in res.get("new_alarms", []):
                self._append_alarm(rec)

        except Exception:
            import traceback
            traceback.print_exc()

    def _refresh_active_table(self, dets):
        active = [d for d in dets if d.get("inside")]
        self.lbl_active_count.setText("区域内目标 ({})".format(len(active)))
        self.tbl_active.setRowCount(len(active))
        for i, d in enumerate(active):
            self.tbl_active.setItem(i, 0, QTableWidgetItem("#{}".format(d["id"])))
            self.tbl_active.setItem(i, 1, QTableWidgetItem(d.get("label", "")))
            it = QTableWidgetItem("{:.1f}s".format(d.get("dwell", 0.0)))
            if d.get("occupied"):
                it.setForeground(QBrush(QColor("#C0433F")))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.tbl_active.setItem(i, 2, it)

    def on_progress(self, idx, total):
        """检测过程中收到进度：更新进度条、时间、以及「重新开始」按钮状态。"""
        self._last_frame_idx = idx
        # 注意：这里不改 _reached_start。
        # 该标志只由「重新开始 / 跳到开头」显式设置，用来让按钮在点击后
        # 稳定保持置灰（否则下一帧进度一到就又把按钮点亮了）。
        self._refresh_restart_enabled()

        if self._seek_dragging:
            return
        if total > 0:
            self.slider_pos.blockSignals(True)
            self.slider_pos.setValue(int(idx / total * 1000))
            self.slider_pos.blockSignals(False)
        self._update_time_label(idx)

    def _refresh_restart_enabled(self):
        """统一决定「重新开始」是否可点。

        规则：**视频当前位置在开头（或还没播过）时置灰**，其余情况可点。
        刚点完「重新开始」会先把按钮置灰，等重播推进过前几帧再重新变亮
        —— 这样既满足"点了就变灰"，也不会让它永远点不动。
        """
        if self.total_frames <= 0:
            self.btn_restart.setEnabled(False)
            return
        moved_past_start = self._last_frame_idx > 2
        at_start = (self.slider_pos.value() <= 1) or (self._reached_start and not moved_past_start)
        self.btn_restart.setEnabled(not at_start)

    def _update_time_label(self, idx):
        cur = idx / max(1.0, self.fps)
        tot = (self.total_frames or 0) / max(1.0, self.fps)
        self.lbl_time.setText("{} / {}".format(fmt_time(cur), fmt_time(tot)))

    # ---- 进度条拖动 ----
    def _on_seek_press(self):
        """按住滑块：暂停跟随，避免自动进度把滑块拽回去。"""
        self._seek_dragging = True

    def _on_slider_value_changed(self, _value):
        """拖动过程中实时更新时间显示（还没松手也能看到跳到哪）。"""
        if not self._seek_dragging:
            return
        target = int(self.slider_pos.value() / 1000.0 * (self.total_frames or 0))
        self._update_time_label(target)

    def _on_seek_release(self):
        """松开滑块：真正跳到该时间点。"""
        self._seek_dragging = False
        if self.total_frames <= 0:
            return
        target = int(self.slider_pos.value() / 1000.0 * self.total_frames)
        self._update_time_label(target)
        # 拖到开头等价于"回到最开始"
        self._reached_start = (target <= 1)
        self._refresh_restart_enabled()

        if self.worker is not None:
            # 已在检测：让后台线程跳帧后继续跑
            self.worker.seek(target)
            self.statusBar().showMessage(
                "已跳转到 {:.1f} 秒（第 {} 帧）".format(
                    target / max(1.0, self.fps), target), 4000)
        else:
            # 还没开始检测：记录起始位置，点「开始检测」时从这里播
            self._pending_start_frame = target
            self._show_frame_at(target)
            self.statusBar().showMessage(
                "起始位置已设为 {:.1f} 秒（第 {} 帧），点「开始检测」从这里开始".format(
                    target / max(1.0, self.fps), target), 5000)

    # ---- 重新开始 ----
    def on_restart(self):
        """把视频拉回最开始。

        三种情况：
          1. 检测进行中           -> 跳到第 0 帧继续跑
          2. 检测已停止/已播完     -> 自动从第 0 帧重新开始一次检测
          3. 还没开始检测         -> 只把预览和起始位置归零
        """
        if self.total_frames <= 0:
            return

        if self.worker is not None:
            # 情况 1：正在跑，让后台线程跳回开头
            self.worker.seek(0)
            self._reached_start = True
            # 立刻置灰，并在重播推进前保持置灰
            self.btn_restart.setEnabled(False)
            self.statusBar().showMessage("已回到视频开头，继续检测", 4000)
        elif self._last_frame_idx > 0 or self.processed_frames > 0:
            # 情况 2：跑过了但已结束（停止 / 播完）—— 直接重新开一轮，
            # 否则点了没反应，用户会以为按钮坏了
            self._pending_start_frame = 0
            self._reached_start = True
            self._reset_counters_for_restart()
            self.on_start()
            self.statusBar().showMessage("已回到视频开头，重新开始检测", 4000)
            return
        else:
            # 情况 3：还没开始
            self._pending_start_frame = 0
            self._reached_start = True
            self._show_frame_at(0)
            self.statusBar().showMessage("已回到视频开头", 4000)

        # 位置回到 0：按钮立刻置灰
        self.slider_pos.blockSignals(True)
        self.slider_pos.setValue(0)
        self.slider_pos.blockSignals(False)
        self._update_time_label(0)
        self._refresh_restart_enabled()

    def _reset_counters_for_restart(self):
        """重新开始前把统计清零，避免和上一轮的数据混在一起。"""
        self.alarms = []
        self.tbl_alarms.setRowCount(0)
        self.detected_classes = {}
        self.max_dwell = 0.0
        self.processed_frames = 0
        self.detect_seconds = 0.0
        self.paused = False
        self._selected_alarm = None
        # 关键：位置计数也要归零，否则「重新开始」按钮的状态判断
        # 会拿上一轮的帧号做依据，导致点了之后按钮没变灰
        self._last_frame_idx = 0
        self._refresh_report()
        self._rebuild_thumbnails()

    # ==================================================================
    #  报警记录
    # ==================================================================
    @staticmethod
    def vehicle_summary(rec):
        """把车辆特征压成一行文本：有车牌优先显示车牌，没有则显示颜色+型号。

        这正是需求里说的"有车牌就记录车牌号，没车牌就记录颜色和型号"。
        """
        plate = (rec.get("plate_text") or "").strip()
        if plate:
            pc = rec.get("plate_color") or ""
            return "{} {}".format(plate, "({})".format(pc) if pc and pc != "未知" else "")
        color = rec.get("color") or "未知"
        model = rec.get("model") or rec.get("cls") or "车辆"
        return "{} {}".format(color, model)

    def _append_alarm(self, rec):
        self.alarms.append(rec)
        self.max_dwell = max(self.max_dwell, float(rec.get("dwell", 0.0)))
        cls = rec.get("cls", "车辆")
        self.detected_classes[cls] = self.detected_classes.get(cls, 0) + 1

        r = self.tbl_alarms.rowCount()
        self.tbl_alarms.insertRow(r)
        vals = [str(rec.get("no", r + 1)), rec.get("time", ""),
                "#{}".format(rec.get("id", "?")),
                "{:.1f}s".format(float(rec.get("dwell", 0.0))),
                cls, self.vehicle_summary(rec)]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            it.setData(Qt.UserRole, rec)
            if c == 3:
                it.setForeground(QBrush(QColor("#C0433F")))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            elif c == 5:
                # 有车牌 -> 青色高亮，便于一眼看到
                has_plate = bool((rec.get("plate_text") or "").strip())
                it.setForeground(QBrush(QColor("#23855A") if has_plate
                                        else QColor("#5A7575")))
            self.tbl_alarms.setItem(r, c, it)
        self.tbl_alarms.scrollToBottom()

        self._rebuild_thumbnails()
        self._refresh_report()

    def on_open_evidence(self):
        """用系统看图工具打开当前选中报警的证据图（看最大分辨率）"""
        rec = getattr(self, "_selected_alarm", None)
        path = (rec or {}).get("snapshot") if rec else None
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "提示", "请先在左侧选中一条带证据图的报警记录。")
            return
        try:
            os.startfile(os.path.normpath(path))          # Windows 关联的看图工具
        except Exception:
            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            except Exception as e:
                QMessageBox.warning(self, "打开失败", str(e))

    def on_alarm_selected(self):
        items = self.tbl_alarms.selectedItems()
        if not items:
            return
        rec = self.tbl_alarms.item(items[0].row(), 0).data(Qt.UserRole)
        if not rec:
            return
        self._selected_alarm = rec          # 供「外部打开」按钮使用
        snap = rec.get("snapshot")
        if snap and os.path.exists(snap):
            # 送进可缩放画布：滚轮放大、拖拽平移、双击复位
            self.evidence_canvas.clear_all()
            self.evidence_canvas.set_frame_from_pixmap(QPixmap(snap), keep_view=False)
            self.lbl_ev_zoom.setText("{:.0f}%".format(self.evidence_canvas._scale * 100))
        else:
            self.evidence_canvas.clear_all("该条报警没有证据图\n（「报警截图落盘」未开启）")
            self.lbl_ev_zoom.setText("—")
        plate = (rec.get("plate_text") or "").strip()
        lines = [
            "第 {} 次报警".format(rec.get("no", "?")),
            "时间：{}".format(rec.get("full_time", rec.get("time", ""))),
            "目标：ID {}   类型：{}".format(rec.get("id", "?"), rec.get("cls", "")),
            "停留：{:.1f} 秒   帧号：{}".format(
                float(rec.get("dwell", 0.0)), rec.get("frame", "?")),
        ]
        if plate:
            lines.append("车牌号：{}（{}）".format(
                plate, rec.get("plate_color") or "未知"))
        else:
            lines.append("车牌号：未识别（该目标无牌或角度不可读）")
        lines.append("车身颜色：{}".format(rec.get("color") or "未知"))
        lines.append("车辆型号：{}".format(rec.get("model") or rec.get("cls") or "车辆"))
        if (rec.get("location") or "").strip():
            lines.append("监测地点：{}".format(rec["location"]))
        if rec.get("plate_crop"):
            lines.append("车牌截图：{}".format(rec["plate_crop"]))
        lines.append("证据图：{}".format(snap or "（未保存）"))
        self.lbl_alarm_meta.setText("\n".join(lines))

    def _rebuild_thumbnails(self):
        while self.thumb_grid.count():
            item = self.thumb_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.thumb_buttons = []
        for i, rec in enumerate(self.alarms):
            snap = rec.get("snapshot")
            btn = QPushButton()
            btn.setFixedSize(92, 58)
            btn.setToolTip("第 {} 次报警 · {} · 停留 {:.1f}s\n{}".format(
                rec.get("no", i + 1), rec.get("time", ""),
                float(rec.get("dwell", 0.0)), self.vehicle_summary(rec)))
            if snap and os.path.exists(snap):
                pix = QPixmap(snap).scaled(92, 58, Qt.KeepAspectRatioByExpanding,
                                           Qt.SmoothTransformation)
                # 注意：QPushButton.setIcon 只接受 QIcon，传 QPixmap 会抛 TypeError
                btn.setIcon(QIcon(pix))
                btn.setIconSize(QSize(92, 58))
            else:
                btn.setText("#{}".format(rec.get("no", i + 1)))
            btn.clicked.connect(lambda _, row=i: self._select_alarm_row(row))
            self.thumb_buttons.append(btn)
            self.thumb_grid.addWidget(btn, i // 4, i % 4)
        self.thumb_grid.setRowStretch(self.thumb_grid.rowCount(), 1)

    def _select_alarm_row(self, row):
        if 0 <= row < self.tbl_alarms.rowCount():
            self.tbl_alarms.selectRow(row)
            self.on_alarm_selected()

    def on_open_snapshot_dir(self):
        d = abs_path(self.cfg.get("snapshot_dir", "output/alerts"))
        os.makedirs(d, exist_ok=True)
        try:
            subprocess.Popen(["explorer", os.path.normpath(d)])
        except Exception as e:
            QMessageBox.warning(self, "失败", "无法打开目录：\n{}".format(e))

    def on_open_output_dir(self):
        d = os.path.join(BASE_DIR, "output")
        os.makedirs(d, exist_ok=True)
        try:
            subprocess.Popen(["explorer", os.path.normpath(d)])
        except Exception as e:
            QMessageBox.warning(self, "失败", "无法打开目录：\n{}".format(e))

    def on_clear_alarms(self):
        if not self.alarms:
            return
        r = QMessageBox.question(self, "清空记录",
                                 "确定清空界面上的报警记录吗？\n（已落盘的截图不会被删除）",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.alarms = []
        self.tbl_alarms.setRowCount(0)
        self.detected_classes = {}
        self.max_dwell = 0.0
        self._rebuild_thumbnails()
        self._refresh_report()
        self.evidence_canvas.clear_all("在左侧选择一条报警记录\n这里会显示对应的证据图\n\n"
                                      "显示后可用滚轮放大查看细节")
        self.lbl_ev_zoom.setText("—")
        self.lbl_alarm_meta.setText("—")
        self.statusBar().showMessage("已清空报警记录", 4000)

    def on_export_csv(self):
        if not self.alarms:
            QMessageBox.information(self, "提示", "当前没有报警记录可导出。")
            return
        default = os.path.join(
            BASE_DIR, "output",
            "alarm_evidence_{}.csv".format(datetime.now().strftime("%Y%m%d_%H%M%S")))
        os.makedirs(os.path.dirname(default), exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出证据 CSV", default, "CSV 文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["序号", "完整时间", "时间", "目标ID", "类型",
                            "停留(秒)", "帧号",
                            "车牌号", "车牌类型", "车身颜色", "车辆型号",
                            "监测地点",
                            "证据图路径", "车牌截图路径"])
                for rec in self.alarms:
                    w.writerow([rec.get("no", ""), rec.get("full_time", ""),
                                rec.get("time", ""), rec.get("id", ""),
                                rec.get("cls", ""), rec.get("dwell", ""),
                                rec.get("frame", ""),
                                rec.get("plate_text", "") or "未识别",
                                rec.get("plate_color", "") or "无",
                                rec.get("color", "") or "未知",
                                rec.get("model", "") or rec.get("cls", ""),
                                rec.get("location", "") or "",
                                rec.get("evidence", "") or rec.get("snapshot", ""),
                                rec.get("plate_crop", "")])
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        self.statusBar().showMessage("已导出 {} 条记录到 {}".format(len(self.alarms), path), 6000)
        QMessageBox.information(self, "导出成功",
                                "已导出 {} 条报警证据：\n{}".format(len(self.alarms), path))

    # ==================================================================
    #  数据报告
    # ==================================================================
    def _refresh_report(self):
        n = len(self.alarms)
        self.card_frames.set_value(str(self.processed_frames),
                                   "共 {} 帧".format(self.total_frames) if self.total_frames
                                   else "尚未开始")
        self.card_alarms.set_value(str(n), "未触发" if n == 0 else "已触发报警")
        self.card_maxdwell.set_value("{:.1f} s".format(self.max_dwell),
                                     "阈值 {:.1f}s".format(self.spin_dwell.value()))
        ratio = (n / self.processed_frames * 100.0) if self.processed_frames else 0.0
        self.card_ratio.set_value("{:.1f}%".format(ratio), "报警次数 / 处理帧数")

        self.chart.set_data([(rec.get("no", i + 1), float(rec.get("dwell", 0.0)))
                             for i, rec in enumerate(self.alarms)],
                            self.spin_dwell.value())

        if n == 0:
            self.lbl_summary.setText(
                "尚未产生报警记录。\n\n"
                "若已执行检测但仍无报警，可以试试：\n"
                "· 降低「停留阈值」（例如 3 秒）\n"
                "· 降低「置信度」（例如 0.25）\n"
                "· 确认 ROI 是否框住了车辆所在位置")
        else:
            types = "、".join("{} {} 次".format(k, v) for k, v in self.detected_classes.items())
            # 统计车牌识别与车辆特征情况
            with_plate = [r for r in self.alarms if (r.get("plate_text") or "").strip()]
            plate_lines = ""
            if with_plate:
                seen = []
                for r in with_plate:
                    tag = "{}（{} {}）".format(r["plate_text"],
                                             r.get("color") or "未知",
                                             r.get("model") or r.get("cls") or "车辆")
                    if tag not in seen:
                        seen.append(tag)
                plate_lines = "识别到车牌 {} 次：{}\n".format(
                    len(with_plate), "、".join(seen[:6]))
            no_plate = n - len(with_plate)
            if no_plate:
                feats = []
                for r in self.alarms:
                    if (r.get("plate_text") or "").strip():
                        continue
                    tag = "{} {}".format(r.get("color") or "未知",
                                         r.get("model") or r.get("cls") or "车辆")
                    if tag not in feats:
                        feats.append(tag)
                plate_lines += "其中 {} 次无车牌，已记录颜色+型号：{}\n".format(
                    no_plate, "、".join(feats[:6]))

            self.lbl_summary.setText(
                "本次共处理 {} 帧，触发 {} 次盲道占用报警。\n\n"
                "涉及车辆类型：{}\n"
                "{}\n"
                "最长停留时长：{:.1f} 秒（报警阈值 {:.1f} 秒）\n"
                "证据图均已保存到 output/alerts/（红框标出车辆 + 车辆放大特写）。\n\n"
                "结论：检测到车辆在盲道区域内停留超时，\n"
                "已生成带时间戳与车辆特征的证据图，可用于后续执法/劝导。".format(
                    self.processed_frames, n, types or "—",
                    plate_lines.rstrip("\n"),
                    self.max_dwell, self.spin_dwell.value()))

    # ==================================================================
    #  杂项
    # ==================================================================
    def _set_badge(self, label, text, obj_name):
        label.setText(text)
        if label.objectName() != obj_name:
            label.setObjectName(obj_name)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def _update_badges(self):
        self._set_badge(self.badge_status, "就绪", "IdleBadge")
        self._set_badge(self.badge_alarm, "报警 0 次", "IdleBadge")
        self._set_badge(self.badge_zone, "区域内 0", "IdleBadge")
        dev = self.cfg.get("device", "cpu")
        self.lbl_engine_info.setText("YOLOv8n · {}".format(str(dev).upper()))

    def _tick_clock(self):
        self.lbl_status_right.setText(
            datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(4000)
        self._roi_timer.stop()
        self._clock_timer.stop()
        event.accept()


# ===========================================================================
# 自绘条形图：各次报警的停留时长
# ===========================================================================
class _DwellChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._data = []
        self._threshold = 5.0

    def set_data(self, data, threshold):
        self._data = list(data)
        self._threshold = float(threshold) if threshold else 0.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pad_l, pad_b, pad_t = 40, 34, 14

        if not self._data:
            p.setPen(QPen(CLR_DIM))
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(self.rect(), Qt.AlignCenter, "暂无报警数据")
            return

        max_v = max([d[1] for d in self._data] + [self._threshold, 1.0]) * 1.15
        plot_h = h - pad_b - pad_t
        plot_w = max(1, w - pad_l - 12)
        n = len(self._data)
        slot = plot_w / n
        bar_w = min(46.0, slot * 0.6)

        # 网格 + Y 轴
        # 标签精度按量程走：秒数通常只有个位，一律取整会出现
        # "1 / 1 / 0 / 0" 这种连着重复的刻度（等于刻度失去意义）。
        p.setFont(QFont("Cascadia Mono", 8))
        axis_fmt = "{:.0f}" if max_v >= 3 else "{:.1f}"
        for i in range(5):
            y = pad_t + plot_h * i / 4.0
            val = max_v * (1 - i / 4.0)
            p.setPen(QPen(QColor("#E2EBEB"), 1))
            p.drawLine(int(pad_l), int(y), int(w - 10), int(y))
            p.setPen(QPen(CLR_DIM))
            p.drawText(QRectF(0, y - 8, pad_l - 6, 16),
                       Qt.AlignRight | Qt.AlignVCenter, axis_fmt.format(val))

        # 阈值线（主题强调黄，和参考图的 Create 按钮同色系）
        if self._threshold > 0:
            ty = pad_t + plot_h * (1 - self._threshold / max_v)
            p.setPen(QPen(QColor("#D9A92E"), 1.5, Qt.DashLine))
            p.drawLine(int(pad_l), int(ty), int(w - 10), int(ty))

        # 柱子
        f_val = QFont("Cascadia Mono", 8, QFont.Bold)
        f_axis = QFont("Cascadia Mono", 8)
        bars = []

        # 先算出所有柱子的位置，方便阈值标签避让
        for i, (no, val) in enumerate(self._data):
            x = pad_l + i * slot + (slot - bar_w) / 2.0
            bh = plot_h * (val / max_v)
            y = pad_t + plot_h - bh
            bars.append((x, y, bh, val, no))

        # 找出停留最久的那根，作为"高亮柱"（参考图里只有一根是强调色）
        peak = max([b[3] for b in bars], default=0.0)

        # 再画柱子与数值
        for (x, y, bh, val, no) in bars:
            over = val >= self._threshold
            is_peak = (val >= peak - 1e-9)
            rect = QRectF(x, y, bar_w, max(2.0, bh))

            # 配色跟随参考图：
            #   普通柱 = 淡青（大面积、低饱和，不吵）
            #   高亮柱 = 琥珀黄（只有一根，视线自然被吸过去）
            #   超标但非最高 = 深青，表示"已触发"
            if is_peak:
                fill = QColor("#F2C94C")
            elif over:
                fill = QColor("#0E7A7A")
            else:
                fill = QColor("#A8D8D8")
            p.setBrush(QBrush(fill))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 6, 6)

            p.setPen(QPen(CLR_TEXT))
            p.setFont(f_val)
            p.drawText(QRectF(x - 12, y - 17, bar_w + 24, 15),
                       Qt.AlignCenter, "{:.1f}".format(val))

            p.setPen(QPen(CLR_DIM))
            p.setFont(f_axis)
            p.drawText(QRectF(x - 10, pad_t + plot_h + 6, bar_w + 20, 16),
                       Qt.AlignCenter, "#{}".format(no))

        # 阈值图例：固定在左上角画成小徽章，彻底避开柱子
        if self._threshold > 0:
            p.setFont(f_axis)
            text = "报警阈值 {:.1f}s".format(self._threshold)
            tw = p.fontMetrics().horizontalAdvance(text)
            th = p.fontMetrics().height()
            badge = QRectF(pad_l + 4, pad_t + 2, tw + 16, th + 8)
            p.setBrush(QBrush(QColor(255, 176, 32, 30)))
            p.setPen(QPen(QColor("#8A6410"), 1))
            p.drawRoundedRect(badge, 6, 6)
            p.setPen(QPen(QColor("#8A6410")))
            p.drawText(badge, Qt.AlignCenter, text)


if __name__ == "__main__":
    # 高 DPI
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Blindway Grand")

    # 字体渲染策略：小字号中文用全 hinting 会明显更清晰
    # （借鉴 PyQt-SiliconUI 的 SiFont.getFont 默认 hinting_preference）
    # 注意：必须是 setHintingPreference()。
    # 曾经误写成 setStyleStrategy(QFont.PreferFullHinting)，
    # 两个枚举类型不兼容，一启动就 TypeError 崩溃。
    _f = app.font()
    _f.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(_f)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
