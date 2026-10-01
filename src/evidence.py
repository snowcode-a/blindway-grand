# -*- coding: utf-8 -*-
"""
盲道占用 · 取证图生成模块
==========================
在报警瞬间生成一张**可直接作为执法/劝导依据**的证据图，并提取车辆特征。

做三件事
--------
1. **车牌定位**（纯 OpenCV，不依赖 OCR 引擎）
   中国车牌有极强的颜色先验：小型车蓝底白字、新能源绿底黑字、
   大型车黄底黑字。先用 HSV 把候选色块抠出来，再用
   ① 长宽比（约 3:1）② 尺寸 ③ 字符边缘密度（车牌子区域边缘远多于车身漆面）
   三重条件筛掉误检。定位到之后会**单独裁出并放大**贴到证据图上，
   便于人工核对或后续接 OCR。

2. **车辆颜色识别**
   在车辆框内取中部"车身"区域（避开顶部天空和底部地面），
   用 HSV 直方图投票选出主色，再映射成中文色名。
   另外统计白/黑/灰的像素占比，给出更细的描述（如「白/黑色」）。

3. **型号识别**
   由 YOLO 的 COCO 类别给出中文车型（轿车/摩托车/公交车/卡车/自行车）。

OCR 说明
--------
本机未安装 OCR 引擎（tesseract / easyocr / paddleocr 都没有），
所以模块**不硬编码依赖**：有 OCR 就把车牌文字填进 `plate_text`，
没有就把车牌**裁图放大存下来**（`plate_crop`）并标记为待人工核对。
装上 OCR 后本模块会自动启用，不需要改其它代码。

用法::

    from evidence import VehicleEvidence
    ev = VehicleEvidence()                 # 自动定位字体、探测 OCR
    info = ev.describe(frame, box, class_name)   # 车牌/颜色/型号
    img  = ev.build(frame, box, info, meta)      # 合成证据图 (BGR)
"""
import os
import re
import shutil

import cv2
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception:                                   # pragma: no cover
    _HAS_PIL = False


# ---------------------------------------------------------------------------
# 中国车牌格式校验
#
# 为什么必须校验：HyperLPR3 这类识别器对**非车牌目标**会产生幻觉 ——
# 实测把共享单车车身上的黄色广告字（"美团App 限时…"）读成了
# "冀DA00183F"（置信仅 0.56）。若不做格式校验就直接写进证据，
# 等于伪造证据，比不写更糟。所以这里要求：
#   ① 结构完全符合民用车牌格式
#   ② 识别置信度足够高
# 两条都满足才采信，否则一律记为"未识别"。
# ---------------------------------------------------------------------------
_PROVINCES = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼"
_RE_PLATE_STD = re.compile(
    r"^[" + _PROVINCES + r"][A-Z][A-Z0-9]{5}$")          # 普通车牌：7 位
_RE_PLATE_NEW = re.compile(
    r"^[" + _PROVINCES + r"][A-Z][A-Z0-9]{6}$")          # 新能源：8 位
_RE_PLATE_TRAILER = re.compile(
    r"^[" + _PROVINCES + r"][A-Z][A-Z0-9]{4}挂$")        # 挂车

MIN_PLATE_CONF = 0.70          # 识别置信度门槛（实测幻觉样本为 0.56）


def is_valid_plate(text, conf=None):
    """车牌号格式校验 + 置信度校验。返回 (是否采信, 原因)"""
    if not text:
        return False, "无文本"
    t = re.sub(r"[^0-9A-Z\u4e00-\u9fff]", "", str(text)).upper()
    if len(t) not in (7, 8):
        return False, "长度{}不合规".format(len(t))
    if not (_RE_PLATE_STD.match(t) or _RE_PLATE_NEW.match(t)
            or _RE_PLATE_TRAILER.match(t)):
        return False, "格式不合规"
    if conf is not None and conf < MIN_PLATE_CONF:
        return False, "置信{:.2f}低于{:.2f}".format(conf, MIN_PLATE_CONF)
    return True, "OK"


# ---------------------------------------------------------------------------
# 中文颜色表：HSV 中心 + 名称（H 单位 0~179）
# ---------------------------------------------------------------------------
COLOR_TABLE = [
    # (H下限, H上限, S下限, V下限, 名称)
    (0,   10,  90,  90,  "红色"),
    (170, 180, 90,  90,  "红色"),
    (11,  25,  90,  90,  "橙色"),
    (26,  34,  90,  90,  "黄色"),
    (35,  85,  60,  60,  "绿色"),
    (86,  130, 60,  60,  "蓝色"),
    (131, 169, 50,  50,  "紫色"),
]

CN_CLASS = {
    "car": "轿车", "motorcycle": "摩托车", "bus": "公交车",
    "truck": "卡车", "bicycle": "自行车", "person": "行人",
}
CN_CLASS_ID = {2: "轿车", 3: "摩托车", 5: "公交车", 7: "卡车", 1: "自行车"}

# 中国车牌颜色先验（HSV 范围，H 0~179）
PLATE_COLORS = [
    ("蓝牌", (100, 43, 46), (124, 255, 255), (255, 0, 0)),      # 小型汽车
    ("绿牌", (35, 43, 46), (85, 255, 255), (0, 200, 0)),        # 新能源
    ("黄牌", (20, 80, 90), (34, 255, 255), (0, 215, 255)),      # 大型汽车
]


# ---------------------------------------------------------------------------
def _font_path():
    """找一个能画中文的字体"""
    cands = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\Deng.ttf",
    ]
    for p in cands:
        if os.path.exists(p):
            return p
    return None


def _to_pil(bgr):
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def _to_cv(img):
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


# ===========================================================================
class VehicleEvidence:
    """车辆特征提取 + 证据图合成"""

    def __init__(self, snapshot_dir="output/alerts"):
        self.snapshot_dir = snapshot_dir
        self.font_path = _font_path()
        self._fonts = {}
        self.ocr = self._init_ocr()

    # ------------------------------------------------------------------
    # 车牌识别引擎探测（按可靠性排序）
    # ------------------------------------------------------------------
    def _init_ocr(self):
        # 1) HyperLPR3 —— 专做中文车牌，权重随包下载，离线可跑（首选）
        try:
            import hyperlpr3
            catcher = hyperlpr3.LicensePlateCatcher(
                detect_level=hyperlpr3.DETECT_LEVEL_LOW)
            return {"kind": "hyperlpr3", "mod": catcher}
        except Exception:
            pass
        # 2) pytesseract
        try:
            import pytesseract
            exe = shutil.which("tesseract")
            if not exe:
                for c in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                          r"D:\Tesseract-OCR\tesseract.exe"):
                    if os.path.exists(c):
                        exe = c
                        break
            if exe:
                pytesseract.pytesseract.tesseract_cmd = exe
                return {"kind": "tesseract", "mod": pytesseract}
        except Exception:
            pass
        return None

    @property
    def ocr_available(self):
        return self.ocr is not None

    def ocr_status(self):
        if not self.ocr:
            return "未装车牌识别引擎（将只记录车牌区域截图，不做文字识别）"
        return "车牌识别引擎：{}".format(self.ocr["kind"])

    # ------------------------------------------------------------------
    def _read_plate_hyperlpr(self, img):
        """用 HyperLPR3 在图上找车牌。

        返回 (text, conf, plate_box_in_img) 或 (None, None, None)
        **注意**：这里只做"读出候选"，是否采信由 is_valid_plate() 决定。
        """
        try:
            res = self.ocr["mod"](img)
        except Exception:
            return None, None, None
        if not res:
            return None, None, None
        best = None
        for item in res:
            try:
                code, conf, _ptype, box = item
            except Exception:
                continue
            if conf is None:
                continue
            if best is None or float(conf) > float(best[1]):
                best = (code, float(conf), [int(v) for v in box])
        if best is None:
            return None, None, None
        return best[0], best[1], best[2]

    # ------------------------------------------------------------------
    # 车牌定位
    # ------------------------------------------------------------------
    @staticmethod
    def _edge_density(gray):
        """区域边缘密度：车牌内部有多个字符，边缘远多于纯色车漆"""
        if gray.size == 0:
            return 0.0
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        return float((mag > 80).mean())

    def find_plate(self, frame, box=None):
        """在 frame（或 box 区域内）找车牌。

        返回 dict 或 None::

            {"rect": (x, y, w, h), "color": "绿牌", "score": 0.91,
             "crop": ndarray, "text": "冀DA00183F" 或 None,
             "text_ok": True/False, "note": "..."}

        策略：**HyperLPR3 优先**（有模型时），它自带车牌检测+识别；
        没有模型时退化为 HSV 颜色启发式（只定位不识别）。
        无论哪条路径，文字都要过 is_valid_plate() 才采信。
        """
        if frame is None:
            return None
        H, W = frame.shape[:2]

        # 只在车辆框内找（给一点外扩，避免车牌被裁掉）
        if box is not None:
            bx1, by1, bx2, by2 = [int(v) for v in box]
            px = int((bx2 - bx1) * 0.06)
            py = int((by2 - by1) * 0.06)
            x0, y0 = max(0, bx1 - px), max(0, by1 - py)
            x1, y1 = min(W, bx2 + px), min(H, by2 + py)
            roi = frame[y0:y1, x0:x1]
        else:
            x0, y0 = 0, 0
            roi = frame

        if roi.size == 0 or roi.shape[0] < 20 or roi.shape[1] < 40:
            return None

        # ============ 路线 A：HyperLPR3（推荐）============
        if self.ocr and self.ocr["kind"] == "hyperlpr3":
            text, conf, pbox = self._read_plate_hyperlpr(roi)
            if pbox is not None:
                rx1, ry1, rx2, ry2 = pbox
                rx1 = max(0, min(rx1, roi.shape[1] - 1))
                rx2 = max(rx1 + 1, min(rx2, roi.shape[1]))
                ry1 = max(0, min(ry1, roi.shape[0] - 1))
                ry2 = max(ry1 + 1, min(ry2, roi.shape[0]))
                crop = roi[ry1:ry2, rx1:rx2].copy()
                gx, gy = x0 + rx1, y0 + ry1
                gw, gh = rx2 - rx1, ry2 - ry1

                ok, reason = is_valid_plate(text, conf)
                # 车牌底色大致判断（用于记录"绿牌/蓝牌"）
                pcolor = self._plate_bg_color(crop)
                return {
                    "rect": (int(gx), int(gy), int(gw), int(gh)),
                    "color": pcolor,
                    "score": float(conf or 0.0),
                    "aspect": float(gw) / max(1.0, float(gh)),
                    "crop": crop,
                    "text": text if ok else None,
                    "text_ok": bool(ok),
                    "note": ("识别通过" if ok
                             else "候选“{}”未采信（{}）".format(text or "空", reason)),
                }
            # HyperLPR3 没找到就继续走路线 B 兜底（只定位不识别）

        return self._find_plate_by_color(roi, x0, y0, H, W)

    # ------------------------------------------------------------------
    @staticmethod
    def _plate_bg_color(crop):
        """由车牌裁图的底色判断牌种类"""
        if crop is None or crop.size == 0:
            return "未知"
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h = hsv[:, :, 0].astype(np.float32)
        s = hsv[:, :, 1].astype(np.float32)
        v = hsv[:, :, 2].astype(np.float32)
        colored = (s > 60) & (v > 60)
        if colored.mean() < 0.15:
            return "白牌"
        hm = float(np.median(h[colored]))
        if 100 <= hm <= 130:
            return "蓝牌"
        if 35 <= hm <= 85:
            return "绿牌"
        if 15 <= hm <= 34:
            return "黄牌"
        return "未知"

    # ------------------------------------------------------------------
    def _find_plate_by_color(self, roi, x0, y0, H, W):
        """HSV 颜色启发式（无模型时的兜底，只定位不识别）

        注意：这条路线的假阳性率不低（实测会把车筐金属网、路面反光当车牌），
        所以门槛设得比较严，宁可漏检也不误报。
        """
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        rh, rw = roi.shape[:2]
        roi_area = float(rh * rw)

        best = None
        for name, lo, hi, _bgr in PLATE_COLORS:
            mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
            # 横向闭运算：把车牌内部的字符缝隙连成整块
            k = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))

            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                x, y, w, h = cv2.boundingRect(c)

                # ---- 尺寸：车牌必须是"长条"，太扁太方都不算 ----
                if w < 56 or h < 16:
                    continue
                if w > rw * 0.75 or h > rh * 0.30:
                    continue
                area_ratio = (w * h) / roi_area
                if not (0.0012 < area_ratio < 0.12):
                    continue

                # ---- 长宽比：中国车牌约 3:1，收紧到 2.4~3.8 ----
                ar = w / float(h)
                if not (2.4 < ar < 3.8):
                    continue

                fill = cv2.contourArea(c) / float(w * h)
                if fill < 0.62:                    # 实心矩形才算
                    continue

                # ---- 文字边缘密度：车牌上有 7~8 个字符，边缘一定很密 ----
                sub = gray[y:y + h, x:x + w]
                edge = self._edge_density(sub)
                if edge < 0.20:                    # 车漆/车筐/反光都过不了这一关
                    continue

                # ---- 极值分布：车牌是"底+字"两色，不能是一片过渡的糊影 ----
                # 要求存在明显的高亮（字）与低亮（底）两极
                hi_ratio = float((sub > 150).mean())
                lo_ratio = float((sub < 90).mean())
                if hi_ratio < 0.06 or lo_ratio < 0.10:
                    continue

                ar_score = 1.0 - min(1.0, abs(ar - 3.0) / 1.4)
                score = (0.40 * ar_score
                         + 0.35 * min(1.0, edge / 0.40)
                         + 0.15 * fill
                         + 0.10 * min(1.0, hi_ratio / 0.30))

                if best is None or score > best["score"]:
                    gx, gy = x0 + x, y0 + y
                    best = {
                        "rect": (int(gx), int(gy), int(w), int(h)),
                        "color": name,
                        "score": float(score),
                        "edge": float(edge),
                        "aspect": float(ar),
                    }

        # 分数太低宁可不报，避免把车筐/反光当车牌（假阳性比漏检更糟）
        if best is None or best["score"] < 0.55:
            return None

        # 这条路只做定位，不做识别 —— 文字一律留空，绝不编造
        gx, gy, gw, gh = best["rect"]
        pad = 3
        rx1 = max(0, gx - pad)
        ry1 = max(0, gy - pad)
        rx2 = min(roi.shape[1], gx + gw + pad)
        ry2 = min(roi.shape[0], gy + gh + pad)
        best["crop"] = roi[ry1:ry2, rx1:rx2].copy()
        best["text"] = None
        best["text_ok"] = False
        best["note"] = "仅颜色定位（无识别模型），需人工核对"
        return best

    # ------------------------------------------------------------------
    def _read_plate_tesseract(self, crop):
        """tesseract 兜底识别（有就试，读不出就返回 None）"""
        if crop is None or crop.size == 0 or not self.ocr:
            return None
        if self.ocr["kind"] != "tesseract":
            return None
        try:
            big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            g = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
            g = cv2.bilateralFilter(g, 9, 75, 75)
            cfg = "--psm 7 -c tessedit_char_whitelist=" \
                  "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼" \
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            txt = self.ocr["mod"].image_to_string(g, config=cfg)
            txt = re.sub(r"\s+", "", txt or "").upper()
            ok, _ = is_valid_plate(txt, None)
            return txt if ok else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 车辆颜色
    # ------------------------------------------------------------------
    @staticmethod
    def describe_color(frame, box, plate_rect=None):
        """判断车身主色，返回中文色名（可能带 '/'，如「白/黑色」）。"""
        if frame is None:
            return "未知"
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(W, x2), min(H, y2)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return "未知"

        # 取中部：上下各去 28%（去车顶/地面），左右各去 12%（去背景）
        bw, bh = x2 - x1, y2 - y1
        cx1 = x1 + int(bw * 0.12)
        cx2 = x2 - int(bw * 0.12)
        cy1 = y1 + int(bh * 0.28)
        cy2 = y2 - int(bh * 0.28)
        if cx2 - cx1 < 6 or cy2 - cy1 < 6:
            return "未知"

        patch = frame[cy1:cy2, cx1:cx2].copy()

        # 挖掉车牌区域，免得蓝牌/黄牌把车身颜色带偏
        if plate_rect:
            px, py, pw, ph = plate_rect
            ox, oy = px - cx1, py - cy1
            ex0, ey0 = max(0, ox - 2), max(0, oy - 2)
            ex1, ey1 = min(patch.shape[1], ox + pw + 2), min(patch.shape[0], oy + ph + 2)
            if ex1 > ex0 and ey1 > ey0:
                patch[ey0:ey1, ex0:ex1] = 0

        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hh, ss, vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        # 只统计有效像素（挖掉的黑色区域排除）
        valid = vv > 12
        total = int(valid.sum())
        if total < 50:
            return "未知"

        # 先判白 / 黑 / 灰（低饱和）
        low_sat = (ss < 55) & valid
        n_low = int(low_sat.sum())
        n_white = int((low_sat & (vv > 165)).sum())
        n_black = int((low_sat & (vv < 70)).sum())
        n_gray = n_low - n_white - n_black
        n_color = total - n_low

        parts = []
        low_ratio = n_low / float(total)
        if low_ratio > 0.42:
            # 无彩色占主导：白 / 黑 / 银灰，取最多的那个（允许并列）
            buckets = sorted([("白色", n_white), ("黑色", n_black), ("银灰色", n_gray)],
                             key=lambda t: -t[1])
            parts.append(buckets[0][0])
            if buckets[1][1] > n_low * 0.30:
                parts.append(buckets[1][0])
            return "/".join(parts)

        # 有彩色占主导：按色相投票
        votes = {}
        for (h0, h1, s_min, v_min, name) in COLOR_TABLE:
            if h0 <= h1:
                m = (hh >= h0) & (hh <= h1) & (ss >= s_min) & (vv >= v_min)
            else:                                   # 红色跨 0
                m = (((hh >= h0) | (hh <= h1)) & (ss >= s_min) & (vv >= v_min))
            n = int(m.sum())
            if n:
                votes[name] = votes.get(name, 0) + n
        if not votes:
            return "未知"
        top = sorted(votes.items(), key=lambda kv: -kv[1])
        name, n = top[0]
        if len(top) > 1 and top[1][1] > n * 0.55:
            return "{}/{}".format(name, top[1][0])
        return name

    # ------------------------------------------------------------------
    # 一次性提取全部特征
    # ------------------------------------------------------------------
    def describe(self, frame, box, cls_id=None, cls_name=None):
        """返回该车辆的特征字典（车牌 / 颜色 / 型号）。"""
        plate = self.find_plate(frame, box)
        color = self.describe_color(frame, box, plate["rect"] if plate else None)
        if cls_name is None:
            cls_name = CN_CLASS_ID.get(cls_id, "车辆")
        return {
            "plate": plate,
            "plate_text": (plate or {}).get("text"),
            "plate_color": (plate or {}).get("color"),
            "plate_rect": (plate or {}).get("rect"),
            "color": color,
            "model": cls_name,
        }

    # ------------------------------------------------------------------
    # 证据图合成
    # ------------------------------------------------------------------
    def _font(self, size):
        if size not in self._fonts:
            if self.font_path and _HAS_PIL:
                try:
                    self._fonts[size] = ImageFont.truetype(self.font_path, size)
                except Exception:
                    self._fonts[size] = ImageFont.load_default()
            else:
                self._fonts[size] = ImageFont.load_default()
        return self._fonts[size]

    def build(self, frame, box, info, meta=None):
        """合成证据图（BGR，可直接 cv2.imwrite）。

        布局::

            ┌──────────────────────────┬──────────────┐
            │  原始画面（红框标出车辆）  │  车辆放大特写 │
            │  + 顶部信息条             │  + 车牌放大   │
            │                          │  + 特征文字   │
            └──────────────────────────┴──────────────┘
        """
        meta = meta or {}
        if frame is None or not _HAS_PIL:
            # 没有 PIL 时退化为简单的 cv2 画框（至少红框要有）
            out = frame.copy() if frame is not None else np.zeros((480, 640, 3), np.uint8)
            x1, y1, x2, y2 = [int(v) for v in box]
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 255), 4)
            return out

        H, W = frame.shape[:2]
        panel_w = max(360, int(W * 0.38))          # 右侧信息栏宽度
        canvas = Image.new("RGB", (W + panel_w, H), (16, 20, 28))
        draw = ImageDraw.Draw(canvas)

        # ---------------- 左：原图 + 红框 ----------------
        left = frame.copy()
        x1, y1, x2, y2 = [int(v) for v in box]
        # 红色主框 + 外侧白色描边（深色背景上也醒目）
        cv2.rectangle(left, (x1, y1), (x2, y2), (255, 255, 255), 7)
        cv2.rectangle(left, (x1, y1), (x2, y2), (0, 0, 255), 4)

        plate = info.get("plate")
        if plate:
            px, py, pw, ph = plate["rect"]
            cv2.rectangle(left, (px, py), (px + pw, py + ph), (0, 255, 255), 3)

        # 顶部信息条
        left_pil = _to_pil(left)
        d2 = ImageDraw.Draw(left_pil)
        bar_h = max(46, H // 14)
        d2.rectangle([0, 0, W, bar_h], fill=(150, 20, 20))
        f_bar = self._font(max(20, bar_h // 2))
        title = "盲道占用报警"
        if meta.get("time"):
            title += "   {}".format(meta["time"])
        d2.text((16, bar_h // 2), title, font=f_bar, fill=(255, 255, 255), anchor="lm")
        right_txt = "停留 {:.1f}s".format(float(meta.get("dwell", 0.0)))
        if meta.get("track_id") is not None:
            right_txt = "ID {}   ".format(meta["track_id"]) + right_txt
        d2.text((W - 16, bar_h // 2), right_txt, font=f_bar,
                fill=(255, 230, 230), anchor="rm")
        canvas.paste(left_pil, (0, 0))

        # ---------------- 右：信息栏 ----------------
        x = W
        y = 0
        draw.rectangle([x, 0, W + panel_w - 1, H], fill=(16, 20, 28))
        draw.rectangle([x, 0, x + 3, H], fill=(150, 20, 20))
        pad = 16
        x += pad + 8
        inner_w = panel_w - 2 * pad - 8

        f_h = self._font(24)
        f_k = self._font(19)
        f_v = self._font(21)

        y += pad
        draw.text((x, y), "车辆特征", font=f_h, fill=(255, 255, 255))
        y += 40
        draw.line([x, y, x + inner_w, y], fill=(70, 82, 102), width=2)
        y += 14

        # --- 车辆放大特写 ---
        pad_px = max(10, int(max(x2 - x1, y2 - y1) * 0.18))
        cx1, cy1 = max(0, x1 - pad_px), max(0, y1 - pad_px)
        cx2, cy2 = min(W, x2 + pad_px), min(H, y2 + pad_px)
        crop = frame[cy1:cy2, cx1:cx2]
        if crop.size:
            # 车牌高亮（在特写里也画出来）
            if plate:
                px, py, pw, ph = plate["rect"]
                cc = crop.copy()
                cv2.rectangle(cc,
                              (max(0, px - cx1), max(0, py - cy1)),
                              (min(crop.shape[1], px - cx1 + pw),
                               min(crop.shape[0], py - cy1 + ph)),
                              (0, 255, 255), 2)
                crop = cc
            target_w = inner_w
            target_h = int(crop.shape[0] * target_w / max(1, crop.shape[1]))
            # 照片太高会把下面的特征条与"判定依据"挤掉，这里收紧到 30%
            if target_h > H * 0.30:
                target_h = int(H * 0.30)
                target_w = int(crop.shape[1] * target_h / max(1, crop.shape[0]))
            crop_big = cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
            canvas.paste(_to_pil(crop_big), (x, y))
            draw.rectangle([x - 1, y - 1, x + target_w, y + target_h],
                           outline=(90, 105, 130), width=1)
            draw.text((x, y + target_h + 6), "车辆特写（已放大）", font=self._font(15),
                      fill=(150, 163, 184))
            y += target_h + 34

        # --- 车牌放大 ---
        if plate and plate.get("crop") is not None and plate["crop"].size:
            pc = plate["crop"]
            scale = min(6.0, inner_w / max(1.0, pc.shape[1]))
            pw2 = max(1, int(pc.shape[1] * scale))
            ph2 = max(1, int(pc.shape[0] * scale))
            if ph2 > 120:
                ph2 = 120
                pw2 = int(pc.shape[1] * ph2 / max(1.0, pc.shape[0]))
            pc_big = cv2.resize(pc, (pw2, ph2), interpolation=cv2.INTER_CUBIC)
            canvas.paste(_to_pil(pc_big), (x, y))
            draw.rectangle([x - 2, y - 2, x + pw2 + 2, y + ph2 + 2],
                           outline=(0, 220, 255), width=2)
            y += ph2 + 8
            ptxt = info.get("plate_text")
            note = "车牌（{}）：{}".format(plate.get("color", "?"),
                                        ptxt if ptxt else "已定位，待人工核对")
            draw.text((x, y), note, font=self._font(16), fill=(255, 200, 90))
            y += 30
        else:
            draw.text((x, y), "未检测到车牌", font=self._font(17), fill=(255, 150, 120))
            y += 30

        # --- 特征条目 ---
        y += 6
        rows = [
            ("车牌号", info.get("plate_text") or ("未识别" if plate is None else "待核对")),
            ("车牌类型", (plate or {}).get("color") or "无"),
            ("车身颜色", info.get("color") or "未知"),
            ("车辆型号", info.get("model") or "车辆"),
            ("停留时长", "{:.1f} 秒".format(float(meta.get("dwell", 0.0)))),
            ("报警时间", meta.get("time") or "-"),
            ("帧号", str(meta.get("frame", "-"))),
        ]
        loc = str(meta.get("location", "") or "").strip()

        def wrap_text(text, font, max_w):
            """按栏宽折行（中文按字符折即可）"""
            lines, cur = [], ""
            for ch in str(text):
                if font.getlength(cur + ch) <= max_w or not cur:
                    cur += ch
                else:
                    lines.append(cur)
                    cur = ch
            if cur:
                lines.append(cur)
            return lines or [""]

        value_x = x + 96
        value_w = max(60, x + inner_w - value_x)

        # 先算清楚每行需要多高；空间不够就把次要信息（帧号）移到顶部信息条
        base_rows = [
            ("车牌号", info.get("plate_text") or ("未识别" if plate is None else "待核对")),
            ("车牌类型", (plate or {}).get("color") or "无"),
            ("车身颜色", info.get("color") or "未知"),
            ("车辆型号", info.get("model") or "车辆"),
            ("停留时长", "{:.1f} 秒".format(float(meta.get("dwell", 0.0)))),
            ("报警时间", meta.get("time") or "-"),
            ("帧号", str(meta.get("frame", "-"))),
        ]
        if loc:
            base_rows.append(("监测地点", loc))

        BOTTOM_H = 92                       # 判定依据块高度
        avail = H - BOTTOM_H - 16           # 留给特征条目的高度
        f_num_moved = False
        rows = list(base_rows)
        while True:
            need = 0
            for k, v in rows:
                n = len(wrap_text(v, f_v, value_w))
                need += 30 if n <= 1 else 30 + 22 * min(2, n)
            if need <= avail or len(rows) <= 5:
                break
            # 把「帧号」挪到左上角信息条去
            rows = [r for r in rows if r[0] != "帧号"]
            f_num_moved = True

        for k, v in rows:
            draw.text((x, y), k, font=f_k, fill=(130, 145, 170))
            for i, ln in enumerate(wrap_text(v, f_v, value_w)[:2]):
                draw.text((value_x, y + i * 22), ln, font=f_v,
                          fill=(235, 240, 250))
            y += 30 if len(wrap_text(v, f_v, value_w)) <= 1 else 52

        # --- 底部：判定依据（位置按上面实际用掉的高度推算）---
        y = max(y + 18, H - BOTTOM_H)
        draw.line([x, y - 12, x + inner_w, y - 12], fill=(70, 82, 102), width=2)
        draw.text((x, y), "判定依据", font=self._font(17), fill=(255, 255, 255))
        y += 26
        draw.text((x, y), "车辆底边中点落在盲道 ROI 内", font=self._font(15),
                  fill=(150, 163, 184))
        y += 22
        draw.text((x, y), "且停留超过阈值 -> 判定为占用", font=self._font(15),
                  fill=(150, 163, 184))

        # 若帧号被挪走，补在左上角信息条上
        if f_num_moved:
            d2 = ImageDraw.Draw(canvas)
            f_small = self._font(max(15, H // 48))
            d2.text((16, H - 26), "帧 {}".format(meta.get("frame", "-")),
                    font=f_small, fill=(235, 235, 235))

        return _to_cv(canvas)
