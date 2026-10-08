# -*- coding: utf-8 -*-
"""
检测引擎 (与界面解耦)
=====================
把 YOLOv8n + ROI + 停留计时的核心逻辑封装成可复用引擎，
供 Qt 界面 (ui_app.py) 和命令行 (main.py) 共用。

引擎不负责绘图/界面，逐帧返回结果字典，由调用方决定如何展示。
"""
import json
import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np


def _resolve_base_dir():
    """定位数据目录（config/ videos/ yolov8n.pt 所在处）。

    与 qt_app.py 用同一套规则，保证打包后两边算出的目录一致：
      ① BLINDWAY_ROOT 环境变量
      ② 打包后（sys.frozen）：exe 所在目录（或上一层）
      ③ 源码运行：本文件所在目录
    """
    env_root = os.environ.get("BLINDWAY_ROOT", "").strip()
    if env_root and os.path.isdir(env_root):
        return os.path.abspath(env_root)

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        for cand in (exe_dir, os.path.dirname(exe_dir)):
            if os.path.exists(os.path.join(cand, "config")):
                return cand
        return exe_dir

    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _resolve_base_dir()


def abs_path(p):
    if not p:
        return p
    return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)


# --------------------------------------------------------------------------
# 几何工具
# --------------------------------------------------------------------------
def bottom_center(box):
    """车底中心点 —— 判断是否压在盲道上比几何中心更贴合实际。"""
    x1, y1, x2, y2 = box
    return int((x1 + x2) / 2), int(y2)


def in_any_roi(point, rois):
    """点是否落在任一个 ROI 多边形内 (含边界)。rois 为 None 时整幅画面视为区域。"""
    if rois is None:
        return True
    for roi in rois:
        if cv2.pointPolygonTest(roi, (float(point[0]), float(point[1])), False) >= 0:
            return True
    return False


def load_rois(path=None):
    path = path or os.path.join(BASE_DIR, "config", "rois.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    regions = data.get("regions", [])
    return [np.array(r["polygon"], np.int32) for r in regions] or None


def load_settings(path=None):
    path = path or os.path.join(BASE_DIR, "config", "settings.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 报警声音
# --------------------------------------------------------------------------
def play_alarm_sound():
    try:
        import winsound
        winsound.Beep(1000, 350)
        winsound.Beep(1400, 350)
    except Exception:
        pass


# --------------------------------------------------------------------------
# 检测引擎
# --------------------------------------------------------------------------
_MODEL_CACHE = {}   # 模型路径 -> YOLO 实例 (跨引擎/线程复用，避免重复加载)


def resolve_device(requested):
    """把「用户想要的设备」解析成「这台机器真能用的设备」。

    为什么需要这个：
      ultralytics 会把 device 直接交给 torch，如果用户在下拉框选了 cuda
      而机器没有可用的 CUDA，会直接抛
          Invalid CUDA 'device=0' requested
      整个检测线程崩掉、界面报"运行错误"。而这本该是个可以自动兜住的
      配置问题 —— 把 device 设成 0（int）也会踩同样的坑。

    返回 (可用设备, 提示信息或 None)。提示信息非空时调用方应展示给用户，
    让用户知道"你选的 cuda 用不上，已回退到 CPU"，而不是默默降级。
    """
    req = requested

    # 探测 CUDA 是否真的可用（torch 已在本模块导入，成本极低）
    try:
        import torch as _torch
        cuda_ok = bool(_torch.cuda.is_available())
        cuda_n = int(_torch.cuda.device_count()) if cuda_ok else 0
    except Exception:
        cuda_ok, cuda_n = False, 0

    # ---- 已经是 CPU ----
    if req is None or (isinstance(req, str) and req.strip().lower() == "cpu"):
        return "cpu", None

    # ---- 文本形式的 CUDA 请求 ----
    if isinstance(req, str):
        low = req.strip().lower()
        if low.startswith("cuda"):
            if cuda_ok:
                return req, None
            return "cpu", ("你选择的是 GPU（{}），但这台机器没有可用的 CUDA "
                           "（torch.cuda.is_available() = False），已自动改用 CPU 运行。"
                           "想用 GPU 需安装 CUDA 版 PyTorch。").format(req)
        # 纯数字文本，如 "0" / "1"
        if low.isdigit():
            idx = int(low)
            if cuda_ok and idx < cuda_n:
                return idx, None
            return "cpu", ("你指定的 CUDA 设备 {} 不可用（本机检测到 {} 个 GPU），"
                           "已自动改用 CPU 运行。").format(idx, cuda_n)
        # 其它无法识别的写法（例如 "0,1"）交给上层，但先兜一层
        return "cpu", "无法识别的设备写法 {!r}，已改用 CPU 运行。".format(req)

    # ---- 整数形式的 CUDA 请求（最容易踩坑：device = 0）----
    if isinstance(req, int):
        if cuda_ok and 0 <= req < cuda_n:
            return req, None
        return "cpu", ("你指定的 CUDA 设备 {} 不可用（本机检测到 {} 个 GPU），"
                       "已自动改用 CPU 运行。").format(req, cuda_n)

    return "cpu", "设备写法 {!r} 无法识别，已改用 CPU 运行。".format(req)


class GuardEngine:
    """逐帧检测引擎。使用方式:

        eng = GuardEngine(cfg, rois)
        eng.load_model()
        while True:
            ret, frame = cap.read()
            if not ret: break
            res = eng.process(frame)     # res 含 boxes / alarm / stats
    """

    def __init__(self, cfg, rois=None):
        self.cfg = cfg
        self.rois = rois
        self.model = None
        self.fps = cfg.get("_fps", 25.0)
        self.frame_idx = 0
        self.tracks = {}          # tid -> 状态
        self.alarm_count = 0
        self.alarm_log = []       # 报警记录列表(供界面表格展示)
        self._t0 = time.time()

        self.dwell_limit = float(cfg.get("dwell_threshold_sec", 5.0))
        self.target_classes = sorted(int(c) for c in cfg.get("target_classes", [2, 5, 7]))
        self.confirm_frames = int(cfg.get("confirm_frames", 3))
        self.lost_grace = float(cfg.get("lost_grace_sec", 1.5))
        self.cooldown = float(cfg.get("alarm_cooldown_sec", 8.0))
        # 是否允许"同一辆车在一次占用中重复报警"。默认关闭（见 process() 里的说明）
        self.alarm_repeat = bool(cfg.get("alarm_repeat_enabled", False))
        self.confidence = float(cfg.get("confidence", 0.35))
        # 设备：不能直接把用户选的传给 ultralytics —— 选了 cuda 但机器没 GPU 时
        # 会抛 "Invalid CUDA 'device=0' requested" 直接把检测线程搞崩。
        # 这里统一解析成「这台机器真能用的设备」，并留下提示信息供界面显示。
        self.device, self.device_note = resolve_device(cfg.get("device", "cpu"))
        self.class_names_cn = cfg.get("class_names_cn", {})
        self.snapshot_dir = abs_path(cfg.get("snapshot_dir", "output/alerts"))
        self.save_snapshot = bool(cfg.get("save_snapshot", True))
        # 追踪器选择：botsort 自带相机运动补偿，在手持/晃动镜头下明显更稳。
        # 实测（47 帧手持片段）：bytetrack 平均 1.0 个目标 -> botsort 4.4 个，
        # 而静态监控机位下两者几乎无差别，且耗时相同（约 31ms/帧）。
        self.tracker = cfg.get("tracker", "botsort.yaml")
        # 取证模块（车牌/颜色/型号 + 证据图），由界面注入；None 表示不取证
        self.evidence = None
        # 监测地点：由界面填入，会写进证据图，便于后续执法/归档定位
        self.location = str(cfg.get("location", "") or "").strip()

    # ---------------------------------------------------------------
    def load_model(self, progress_cb=None):
        from ultralytics import YOLO
        model_name = self.cfg.get("model", "yolov8n.pt")
        key = abs_path(model_name) if os.path.exists(abs_path(model_name)) else model_name
        if key not in _MODEL_CACHE:
            if progress_cb:
                progress_cb(f"正在加载模型 {model_name} ...")
            _MODEL_CACHE[key] = YOLO(key)
        self.model = _MODEL_CACHE[key]
        return self.model

    def reset(self, fps=None):
        self.tracks.clear()
        self.alarm_count = 0
        self.alarm_log.clear()
        self.frame_idx = 0
        self._t0 = time.time()
        if fps:
            self.fps = fps

    def clock(self):
        """视频时间轴(帧号/fps)，不受推理速度影响。"""
        return self.frame_idx / self.fps if self.fps else 0.0

    # ---------------------------------------------------------------
    def process(self, frame, annotate=False):
        """处理一帧，返回结果字典。

        annotate=True 时直接在 frame 上绘制(供命令行/截图)；
        GUI 场景可传 False，自己用 boxes/rois 绘制。
        """
        if self.model is None:
            raise RuntimeError("模型尚未加载，请先调用 load_model()")

        self.frame_idx += 1
        now = self.clock()

        results = self.model.track(
            frame, persist=True, verbose=False,
            conf=self.confidence, device=self.device,
            classes=self.target_classes,
            tracker=self.tracker,
        )

        dets = []
        r = results[0]
        if r.boxes is not None and r.boxes.id is not None:
            xyxy = r.boxes.xyxy.cpu().numpy()
            ids = r.boxes.id.cpu().numpy().astype(int)
            clss = r.boxes.cls.cpu().numpy().astype(int)
            confs = r.boxes.conf.cpu().numpy()
            for box, tid, cls, cf in zip(xyxy, ids, clss, confs):
                dets.append({
                    "box": box.astype(int),
                    "id": int(tid),
                    "cls": int(cls),
                    "conf": float(cf),
                })

        seen = set()
        new_alarms = []

        for d in dets:
            tid = d["id"]
            seen.add(tid)
            cls = d["cls"]
            box = d["box"]
            bottom = bottom_center(box)
            inside = in_any_roi(bottom, self.rois)

            st = self.tracks.get(tid)
            if st is None:
                st = {"enter": None, "inside": False, "hit": 0,
                      "alarmed": False, "alarm_time": -999.0,
                      "last_seen": now, "cls": cls}
                self.tracks[tid] = st
            st["last_seen"] = now
            st["cls"] = cls

            if inside:
                st["hit"] += 1
                if st["hit"] >= self.confirm_frames:
                    if st["enter"] is None:
                        st["enter"] = now
                    st["inside"] = True
            else:
                st["hit"] = max(0, st["hit"] - 1)
                if st["hit"] == 0:
                    st["inside"] = False
                    st["enter"] = None
                    st["alarmed"] = False

            dwell = (now - st["enter"]) if (st["inside"] and st["enter"] is not None) else 0.0
            occupied = st["inside"] and dwell >= self.dwell_limit

            # ---- 报警去重策略 ----
            # 默认（alarm_repeat_enabled=False）：**同一辆车在一次连续占用过程中只报一次**。
            #   即 st["alarmed"] 一旦置位，直到它离开 ROI（上面 else 分支会清掉）
            #   都不再重复报警。这样一辆车长时间压着盲道也只会留下一条记录，
            #   报警记录清爽、证据不重复。
            # 可选（alarm_repeat_enabled=True）：冷却期结束后允许再次报警，
            #   用于"占道越久越严重、需要反复提醒"的场景。
            if self.alarm_repeat:
                may_alarm = (not st["alarmed"]) or \
                            ((now - st["alarm_time"]) >= self.cooldown)
            else:
                may_alarm = not st["alarmed"]

            if occupied and may_alarm:
                st["alarmed"] = True
                st["alarm_time"] = now
                self.alarm_count += 1
                ts = datetime.now()
                cls_cn = self.class_names_cn.get(str(cls), f"class{cls}")
                rec = {
                    "no": self.alarm_count,
                    "time": ts.strftime("%H:%M:%S"),
                    "full_time": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "id": tid,
                    "cls": cls_cn,
                    "dwell": round(dwell, 1),
                    "frame": self.frame_idx,
                    "snapshot": None,
                    # 车辆特征（由证据模块填充；未启用时保持默认值）
                    "plate_text": None,
                    "plate_color": None,
                    "color": "未知",
                    "model": cls_cn,
                    "evidence": None,
                    "location": self.location,
                }

                # ---- 提取车辆特征 + 生成证据图 ----
                if self.evidence is not None:
                    try:
                        info = self.evidence.describe(frame, box, cls_id=cls)
                        rec["plate_text"] = info.get("plate_text")
                        rec["plate_color"] = info.get("plate_color")
                        rec["color"] = info.get("color") or "未知"
                        rec["model"] = info.get("model") or cls_cn
                        if self.save_snapshot:
                            os.makedirs(self.snapshot_dir, exist_ok=True)
                            stamp = ts.strftime("%Y%m%d_%H%M%S")
                            base = os.path.join(
                                self.snapshot_dir,
                                f"alarm_{stamp}_id{tid}_{dwell:.0f}s")
                            # 证据图：红框标车辆 + 车辆放大特写 + 车牌区 + 特征
                            ev_img = self.evidence.build(frame, box, info, meta={
                                "time": rec["full_time"],
                                "dwell": rec["dwell"],
                                "frame": self.frame_idx,
                                "track_id": tid,
                                "location": self.location,
                            })
                            ev_path = base + "_evidence.jpg"
                            cv2.imwrite(ev_path, ev_img,
                                        [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                            rec["evidence"] = ev_path
                            rec["snapshot"] = ev_path      # 界面/CSV 统一用证据图
                            # 车牌单独存一张放大图，便于人工核对
                            pl = info.get("plate")
                            if pl and pl.get("crop") is not None and pl["crop"].size:
                                pc = cv2.resize(pl["crop"], None, fx=4, fy=4,
                                                interpolation=cv2.INTER_CUBIC)
                                pl_path = base + "_plate.jpg"
                                cv2.imwrite(pl_path, pc)
                                rec["plate_crop"] = pl_path
                    except Exception:
                        import traceback
                        traceback.print_exc()

                # 兜底：证据模块不可用或出错时，至少落一张原始截图
                if self.save_snapshot and not rec["snapshot"]:
                    os.makedirs(self.snapshot_dir, exist_ok=True)
                    snap = os.path.join(
                        self.snapshot_dir,
                        f"alarm_{ts.strftime('%Y%m%d_%H%M%S')}_id{tid}_{dwell:.0f}s.jpg")
                    cv2.imwrite(snap, frame)
                    rec["snapshot"] = snap

                self.alarm_log.append(rec)
                new_alarms.append(rec)
                if self.cfg.get("play_sound", True):
                    play_alarm_sound()

            d.update({
                "inside": st["inside"],
                "dwell": round(dwell, 1),
                "occupied": occupied,
                "label": self.class_names_cn.get(str(cls), f"class{cls}"),
            })

        # 清理长时间消失的目标
        for tid in [t for t, s in self.tracks.items()
                    if t not in seen and (now - s["last_seen"]) > self.lost_grace]:
            del self.tracks[tid]

        if annotate:
            self.draw(frame, dets)

        return {
            "frame_idx": self.frame_idx,
            "dets": dets,
            "new_alarms": new_alarms,
            "alarm_count": self.alarm_count,
            "in_roi": sum(1 for s in self.tracks.values() if s["inside"]),
            "occupied": any(s["inside"] and s["enter"] is not None
                            and (now - s["enter"]) >= self.dwell_limit
                            for s in self.tracks.values()),
            "rois": self.rois,
            "now": now,
        }

    # ---------------------------------------------------------------
    def draw(self, frame, dets):
        """在帧上绘制检测框 / ROI / 状态栏 (命令行与截图使用)。"""
        h, w = frame.shape[:2]

        for d in dets:
            x1, y1, x2, y2 = d["box"]
            if d["occupied"]:
                color, tag = (0, 0, 255), f"OCCUPIED {d['dwell']:.1f}s"
            elif d["inside"]:
                color, tag = (0, 165, 255), f"{d['dwell']:.1f}s"
            else:
                color, tag = (0, 200, 0), d["label"]
            label = f"ID {d['id']} {tag}"
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3 if d["occupied"] else 2)
            cv2.circle(frame, bottom_center(d["box"]), 6, color, -1)
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            cv2.rectangle(frame, (x1, y1 - th - 12), (x1 + tw + 10, y1), color, -1)
            cv2.putText(frame, label, (x1 + 5, y1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        if self.rois is not None:
            overlay = frame.copy()
            for roi in self.rois:
                cv2.fillPoly(overlay, [roi], (0, 0, 255))
            cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
            for roi in self.rois:
                cv2.polylines(frame, [roi], True, (0, 0, 255), 3)
                cv2.putText(frame, "BLINDWAY ROI", (roi[0][0], max(24, roi[0][1] - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        cv2.rectangle(frame, (0, 0), (w, 40), (35, 35, 35), -1)
        cv2.putText(frame,
                    f"Frame {self.frame_idx} | In-ROI: "
                    f"{sum(1 for s in self.tracks.values() if s['inside'])} | "
                    f"Alarms: {self.alarm_count} | Threshold: {self.dwell_limit}s",
                    (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        if any(s["alarmed"] and s["inside"] for s in self.tracks.values()):
            cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 0, 255), 8)

        return frame
