# -*- coding: utf-8 -*-
"""
Web 镜像服务 (web_mirror.py)
============================
给桌面版界面做一个「浏览器能访问的链接」。

原理
----
不重写界面，而是：
  1. 照常启动原来的 PyQt 窗口（界面、检测逻辑一行不改）
  2. 用一个 QTimer 定时把窗口画面抓成 JPEG（窗口自己不见光也能抓）
  3. 通过 HTTP MJPEG 把画面连续推给浏览器 —— <img> 标签直接就能播，
     手机浏览器原生支持，不用额外 JS 库
  4. 浏览器里的鼠标/键盘事件通过 WebSocket 回传，转成 Qt 事件注进去，
     所以网页上可以真的操作

为什么用 MJPEG 而不是 WebRTC
---------------------------
WebRTC 延迟更低但要信令服务器、还要处理 NAT；MJPEG 实现简单、兼容性最好、
手机也原生支持。代价是带宽略高，局域网/隧道场景完全够用。

用法::

    python web_mirror.py                  # 默认 8080 端口
    python web_mirror.py --port 9000
    python web_mirror.py --no-app         # 只起服务，测网页用
"""
from __future__ import annotations

import argparse
import io
import os
import socket
import sys
import threading
import time

# ★ 铁律：torch 必须先于 PyQt5 导入，否则 Windows 上 c10.dll 会初始化失败
import torch  # noqa: F401

from PyQt5.QtCore import (Qt, QBuffer, QByteArray, QEvent, QIODevice, QPoint,
                          QPointF, QTimer)
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ---------------------------------------------------------------- 共享状态

class FrameHub:
    """主线程产帧，HTTP 线程取帧 —— 用锁保护，只保留最新一帧"""

    def __init__(self):
        self._lock = threading.Lock()
        self._jpeg = b""
        self._frame_id = 0
        self._size = (0, 0)

    def publish(self, jpeg: bytes, size):
        with self._lock:
            self._jpeg = jpeg
            self._frame_id += 1
            self._size = size

    def latest(self):
        with self._lock:
            return self._jpeg, self._frame_id, self._size


HUB = FrameHub()

# 由 main() 填入
STATE = {
    "window": None,
    "app": None,
    "fps": 0.0,
    "input_enabled": True,
}

# ---------------------------------------------------------------- 抓帧

_GRAB_BUF = QBuffer()


def start_grabber(window, fps: int = 8, quality: int = 72, scale: float = 1.0):
    """定时抓窗口画面并转成 JPEG 放进 HUB。

    说明：用 QWidget.grab() 而不是抓屏幕 —— 抓屏幕需要窗口真的显示在桌面上，
    而 grab() 是让窗口自己渲染一遍，窗口被遮挡、最小化甚至没有显示器都有效。
    """
    counter = {"n": 0, "t0": time.time()}

    def on_tick():
        try:
            pix = window.grab()
            if pix.isNull():
                return
            img = pix.toImage()
            w, h = img.width(), img.height()
            if scale != 1.0:
                img = img.scaled(int(w * scale), int(h * scale),
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "JPEG", quality)
            buf.close()
            HUB.publish(bytes(ba), (w, h))

            counter["n"] += 1
            el = time.time() - counter["t0"]
            if el >= 1.0:
                STATE["fps"] = counter["n"] / el
                counter["n"] = 0
                counter["t0"] = time.time()
        except Exception as exc:
            print("[抓帧] 出错:", exc)

    timer = QTimer(window)
    timer.timeout.connect(on_tick)
    timer.start(max(30, int(1000 / max(1, fps))))
    return timer


# ---------------------------------------------------------------- 输入注入

def _widget_pos(window, nx: float, ny: float):
    """把浏览器里的归一化坐标 (0~1) 换算成窗口内坐标"""
    w = max(1, window.width())
    h = max(1, window.height())
    return QPoint(int(nx * w), int(ny * h))


def _target_widget(window, pos: QPoint):
    """找到该位置下真正接收事件的子控件（点击要落到按钮上）"""
    child = window.childAt(pos)
    return child if child is not None else window


def handle_input(window, msg: dict):
    """处理来自浏览器的一个输入事件"""
    if not STATE["input_enabled"] or window is None:
        return
    kind = msg.get("t")
    try:
        if kind in ("down", "up", "move"):
            pos = _widget_pos(window, float(msg.get("x", 0)), float(msg.get("y", 0)))
            target = _target_widget(window, pos)
            local = target.mapFrom(window, pos)

            ev_type = {"down": QEvent.MouseButtonPress,
                       "up": QEvent.MouseButtonRelease,
                       "move": QEvent.MouseMove}[kind]
            btn = Qt.LeftButton if msg.get("b", 0) == 0 else Qt.RightButton
            buttons = Qt.LeftButton if kind != "up" else Qt.NoButton

            ev = QEvent(ev_type)
            from PyQt5.QtGui import QMouseEvent
            qm = QMouseEvent(ev_type, QPointF(local), QPointF(local),
                             QPointF(pos), btn, buttons, Qt.NoModifier)
            QApplication.sendEvent(target, qm)

        elif kind == "wheel":
            from PyQt5.QtGui import QWheelEvent
            pos = _widget_pos(window, float(msg.get("x", 0)), float(msg.get("y", 0)))
            target = _target_widget(window, pos)
            local = target.mapFrom(window, pos)
            delta = int(msg.get("d", 0))
            qw = QWheelEvent(QPointF(local), QPointF(pos), QPoint(0, 0),
                             QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                             Qt.NoScrollPhase, False)
            QApplication.sendEvent(target, qw)

        elif kind in ("key", "keyup"):
            from PyQt5.QtGui import QKeyEvent
            key = int(msg.get("k", 0))
            text = msg.get("s", "") or ""
            ev_type = QEvent.KeyPress if kind == "key" else QEvent.KeyRelease
            qk = QKeyEvent(ev_type, key, Qt.NoModifier, text)
            focus = window.focusWidget() or window
            QApplication.sendEvent(focus, qk)

    except Exception as exc:
        print("[输入] 处理失败:", kind, exc)


# ---------------------------------------------------------------- Web 服务

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>盲道占用检测 · Blindway Grand</title>
<style>
  :root { --teal:#0E7A7A; --teal2:#14807F; --bg:#F2F7F7; --ink:#1B3A3A; --line:#D3E2E2; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; padding:0; background:var(--bg); color:var(--ink);
    font-family:"Microsoft YaHei UI","PingFang SC","Segoe UI",sans-serif; }
  header { background:var(--teal2); color:#fff; padding:10px 14px;
    display:flex; align-items:center; gap:10px; position:sticky; top:0; z-index:9; }
  header .logo { width:30px; height:30px; border-radius:9px; background:var(--teal);
    display:flex; align-items:center; justify-content:center; font-weight:700; font-size:15px;
    border:1px solid rgba(255,255,255,.35); flex:0 0 auto; }
  header h1 { font-size:15px; margin:0; font-weight:700; letter-spacing:-.2px; }
  header .sub { font-size:11px; opacity:.8; margin-top:1px; }
  #stat { margin-left:auto; font-size:11px; background:rgba(255,255,255,.16);
    padding:4px 10px; border-radius:11px; white-space:nowrap; }
  .wrap { padding:10px; }
  .stage { background:#123333; border-radius:12px; overflow:auto;
    border:1px solid var(--line); }
  .stage img { display:block; width:100%; height:auto; touch-action:none; cursor:crosshair; }
  .hint { font-size:12px; color:#5A7575; margin:9px 2px; line-height:1.7; }
  .hint b { color:var(--teal); }
  .btns { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  button { flex:1 1 auto; min-width:88px; min-height:40px; border-radius:20px;
    border:1px solid var(--line); background:#fff; color:var(--ink);
    font-size:13px; font-family:inherit; cursor:pointer; }
  button:active { background:#E2F1F0; }
  button.p { background:var(--teal); border-color:var(--teal); color:#fff; font-weight:600; }
  button:disabled { opacity:.45; }
  #qr { display:none; margin-top:12px; padding:14px; background:#fff; border:1px solid var(--line);
    border-radius:12px; text-align:center; }
  #qr canvas { max-width:210px; width:100%; height:auto; }
  #qr p { font-size:12px; color:#5A7575; margin:8px 0 0; word-break:break-all; }
  .fullscreen .stage img { width:auto; height:calc(100vh - 96px); max-width:none; }
</style>
</head>
<body>
<header>
  <div class="logo">盲</div>
  <div>
    <h1>盲道占用检测</h1>
    <div class="sub">Blindway Grand · 实时镜像</div>
  </div>
  <div id="stat">连接中…</div>
</header>

<div class="wrap">
  <div class="stage" id="stage">
    <img id="scr" src="/stream" alt="实时画面">
  </div>

  <div class="hint">
    <b>在画面上点按 / 拖动即可操作</b>，和本机使用一致。<br>
    手机上建议<b>横屏</b>，或点下面「放大横屏」。滚轮缩放 = 双指缩放。
  </div>

  <div class="btns">
    <button class="p" onclick="location.href='/page?s=0'">实时监控</button>
    <button onclick="location.href='/page?s=1'">区域标定</button>
    <button onclick="location.href='/page?s=2'">报警记录</button>
    <button onclick="location.href='/page?s=3'">数据报告</button>
  </div>
  <div class="btns">
    <button onclick="toggleFull()">放大横屏</button>
    <button onclick="showQR()">手机扫码</button>
    <button onclick="sendKey('space')">暂停/继续</button>
  </div>

  <div id="qr">
    <img id="qrimg" alt="扫码打开" src="">
    <p id="qrurl"></p>
  </div>
</div>

<script>
const img = document.getElementById('scr');
const stage = document.getElementById('stage');
const stat = document.getElementById('stat');
let ws = null, retry = 0;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/ws');
  ws.onopen = () => { retry = 0; stat.textContent = '已连接'; };
  ws.onclose = () => {
    retry++; stat.textContent = '重连中…';
    setTimeout(connect, Math.min(4000, 500 * retry));
  };
  ws.onerror = () => { stat.textContent = '连接异常'; };
}
connect();

function send(o) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(o));
}

function norm(e) {
  const r = img.getBoundingClientRect();
  const cx = (e.touches && e.touches[0]) ? e.touches[0].clientX : e.clientX;
  const cy = (e.touches && e.touches[0]) ? e.touches[0].clientY : e.clientY;
  return { x: (cx - r.left) / r.width, y: (cy - r.top) / r.height };
}

let dragging = false;
function down(e) { e.preventDefault(); dragging = true; const p = norm(e); send({t:'down', x:p.x, y:p.y, b:0}); }
function move(e) { if (!dragging) return; e.preventDefault(); const p = norm(e); send({t:'move', x:p.x, y:p.y}); }
function up(e)   { if (!dragging) return; dragging = false; e.preventDefault(); const p = norm(e); send({t:'up', x:p.x, y:p.y, b:0}); }

img.addEventListener('mousedown', down);
img.addEventListener('mousemove', move);
img.addEventListener('mouseup', up);
img.addEventListener('mouseleave', up);
img.addEventListener('touchstart', down, {passive:false});
img.addEventListener('touchmove', move, {passive:false});
img.addEventListener('touchend', up, {passive:false});

img.addEventListener('wheel', (e) => {
  e.preventDefault();
  const p = norm(e);
  send({t:'wheel', x:p.x, y:p.y, d: e.deltaY < 0 ? 120 : -120});
}, {passive:false});

function sendKey(which) {
  const map = { space: 32, left: 16777234, right: 16777236, esc: 16777216 };
  send({t:'key', k: map[which] || 0, s: which === 'space' ? ' ' : ''});
}
window.addEventListener('keydown', (e) => {
  send({t:'key', k: e.keyCode, s: e.key.length === 1 ? e.key : ''});
  if (e.keyCode === 32) e.preventDefault();
});

function toggleFull() {
  document.body.classList.toggle('fullscreen');
  const on = document.body.classList.contains('fullscreen');
  try { if (on && document.documentElement.requestFullscreen)
          document.documentElement.requestFullscreen(); } catch(_) {}
}

function showQR() {
  const box = document.getElementById('qr');
  if (box.style.display === 'block') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  const url = location.href;
  document.getElementById('qrurl').textContent = url;
  // 二维码由后端用 qrcode 库生成（PNG），前端只管显示
  document.getElementById('qrimg').src = '/qr?host=' + encodeURIComponent(url);
}
</script>
</body>
</html>
"""


def build_app(window, allow_input=True):
    """构建 FastAPI 应用"""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

    api = FastAPI(title="Blindway Grand Mirror", docs_url=None, redoc_url=None)

    @api.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @api.get("/health")
    def health():
        return JSONResponse({
            "ok": True,
            "fps": round(STATE["fps"], 1),
            "size": list(HUB.latest()[2]),
            "input": STATE["input_enabled"],
        })

    @api.get("/shot")
    def shot():
        """单帧截图（给海报/PPT 取材用）"""
        jpeg, _, _ = HUB.latest()
        return StreamingResponse(io.BytesIO(jpeg), media_type="image/jpeg")

    @api.get("/qr")
    def qr(host: str = ""):
        """生成当前访问地址的二维码（手机扫码直接打开）。

        用成熟的 `qrcode` 库，不自己实现 —— 手写二维码踩过坑：
        位序、行列朝向、格式信息坐标极易写错，而且错了以后表现是
        「码能定位但解不出内容」，很难排查。用库 + OpenCV 逐项验证才靠谱。
        """
        target = host.strip() or _guess_url()
        try:
            import qrcode
            from qrcode.constants import ERROR_CORRECT_M
            q = qrcode.QRCode(error_correction=ERROR_CORRECT_M,
                              box_size=10, border=3)
            q.add_data(target)
            q.make(fit=True)
            img = q.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return StreamingResponse(io.BytesIO(buf.getvalue()), media_type="image/png")
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @api.get("/stream")
    def stream():
        boundary = "blindwayframe"
        def gen():
            last = -1
            idle = 0
            while True:
                jpeg, fid, _ = HUB.latest()
                if jpeg and fid != last:
                    last = fid
                    idle = 0
                    yield (b"--" + boundary.encode() + b"\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                           + jpeg + b"\r\n")
                else:
                    idle += 1
                    if idle > 600:      # 长时间无新帧就断开，避免连接堆积
                        break
                time.sleep(0.02)
        return StreamingResponse(
            gen(), media_type="multipart/x-mixed-replace; boundary=" + boundary,
            headers={"Cache-Control": "no-store", "Connection": "close"})

    @api.get("/page")
    def switch_page(s: int = 0):
        """网页上的页面切换按钮 -> 直接调 Qt 的 switch_page"""
        try:
            window.switch_page(int(s))
            return {"ok": True, "page": int(s)}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @api.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                msg = await ws.receive_json()
                # 输入注入必须回到 Qt 主线程执行，不能在 uvicorn 线程里碰控件
                QTimer.singleShot(0, lambda m=msg: handle_input(window, m))
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            print("[WS] 断开:", exc)

    return api


# ---------------------------------------------------------------- 启动

def local_ips():
    """列出本机可用的局域网 IP，方便手机访问"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))       # 不会真的发包，只为拿到出口网卡 IP
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def _guess_url():
    """猜一个「别人能访问」的地址，用于生成二维码。

    优先级：
      1. BLINDWAY_PUBLIC_URL 环境变量 —— 内网穿透给出的公网地址写这里最稳
      2. 项目目录下 public_url.txt 的内容 —— 供不方便设环境变量时使用
      3. 局域网 IP —— 同一 WiFi 下手机能访问
      4. 127.0.0.1 兜底
    """
    env = os.environ.get("BLINDWAY_PUBLIC_URL", "").strip()
    if env:
        return env.rstrip("/") + "/"

    # 也支持写在文件里，改起来比设环境变量省事
    try:
        f = os.path.join(HERE, "public_url.txt")
        if os.path.exists(f):
            # ★ 必须用 utf-8-sig：Windows 上用 PowerShell / 记事本存的中文
            #   文本文件常常带 BOM，用普通 utf-8 读会把 BOM 当正文，
            #   结果第一行注释被误当成地址（实测踩过）。
            with open(f, encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip().lstrip("\ufeff").strip()
                    if line and not line.startswith("#"):
                        return line.rstrip("/") + "/"
    except Exception as exc:
        print("[配置] 读取 public_url.txt 失败:", exc)

    ips = local_ips()
    host = ips[0] if ips else "127.0.0.1"
    return "http://{}:{}/".format(host, STATE.get("port", 8080))


def main():
    ap = argparse.ArgumentParser(description="Blindway Grand 网页镜像服务")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--fps", type=int, default=6,
                    help="推流帧率（默认 6；越小越省带宽）")
    ap.add_argument("--quality", type=int, default=68, help="JPEG 质量 1-100")
    ap.add_argument("--scale", type=float, default=0.55,
                    help="画面缩放（默认 0.55，兼顾清晰度与带宽；"
                         "局域网可调到 1.0 更清楚）")
    ap.add_argument("--no-app", action="store_true", help="不启动界面，只起服务")
    ap.add_argument("--readonly", action="store_true", help="只让看，不允许远程操作")
    args = ap.parse_args()
    STATE["port"] = args.port

    # ---- 启动原界面（代码一行不改）----
    window = None
    if not args.no_app:
        import qt_app

        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        app = QApplication(sys.argv)
        app.setApplicationName("Blindway Grand (Web Mirror)")
        from PyQt5.QtGui import QFont
        f = app.font()
        f.setHintingPreference(QFont.PreferFullHinting)
        app.setFont(f)

        window = qt_app.MainWindow()
        window.resize(1480, 920)
        window.show()
        STATE["window"] = window
        STATE["app"] = app
        STATE["input_enabled"] = not args.readonly
        start_grabber(window, fps=args.fps, quality=args.quality, scale=args.scale)
        print("[镜像] 界面已启动，正在推流")
    else:
        STATE["input_enabled"] = False
        print("[镜像] --no-app 模式：只起 Web 服务")

    # ---- 起 Web 服务（后台线程）----
    import uvicorn
    api = build_app(window, allow_input=STATE["input_enabled"])
    cfg = uvicorn.Config(api, host=args.host, port=args.port,
                         log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(1.2)

    host_name = socket.gethostname()
    print()
    print("=" * 62)
    print("  网页镜像已就绪，用浏览器打开下面任一地址：")
    print()
    print("    本机   : http://127.0.0.1:{}/".format(args.port))
    for ip in local_ips():
        print("    局域网 : http://{}:{}/        <- 手机/别的电脑用这个".format(ip, args.port))
    print()
    print("  单帧截图 : http://127.0.0.1:{}/shot".format(args.port))
    print("  健康检查 : http://127.0.0.1:{}/health".format(args.port))
    print("=" * 62)
    print("  按 Ctrl+C 停止")
    print()

    if window is not None:
        return STATE["app"].exec_()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
