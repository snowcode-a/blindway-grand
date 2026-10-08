/* ==========================================================================
   盲道占用检测系统 · 静态展示页交互
   --------------------------------------------------------------------------
   纯前端，无任何依赖。包含：
     · 四个页面切换（支持 URL hash 直达，方便评委直接分享某一页）
     · 证据图缩放/平移（滚轮 + 拖拽 + 双击复位）
     · 报警表格选中联动详情
     · 「播放检测演示」：进度条推进 + 扫描线 + 报警横幅
     · 手机端侧栏抽屉
   ========================================================================== */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ---------------- 轻提示 ---------------- */
  var toastEl = $('#toast'), toastTimer = null;
  function toast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('on'); }, 2200);
  }

  /* ---------------- 页面切换 ---------------- */
  var PAGES = ['monitor', 'roi', 'alarm', 'report'];

  function showPage(name, push) {
    if (PAGES.indexOf(name) < 0) name = 'monitor';
    $$('.page').forEach(function (p) {
      p.classList.toggle('active', p.id === 'page-' + name);
    });
    $$('.nav-btn').forEach(function (b) {
      b.classList.toggle('active', b.dataset.page === name);
    });
    // 手机端：切页后收起抽屉
    var sb = $('#sidebar');
    if (sb) sb.classList.remove('open');
    if (push !== false && location.hash.slice(1) !== name) {
      try { history.replaceState(null, '', '#' + name); } catch (e) { location.hash = name; }
    }
    // ★ 切到区域标定页时必须重算 ROI 手柄位置。
    //   原因：隐藏页（.page{display:none}）里所有元素的
    //   getBoundingClientRect() 都是 0，首次渲染若发生在页面隐藏时，
    //   算出来的手柄坐标就是 (0,0)，全堆在左上角且再也不会更新。
    //   这里用 rAF 等浏览器完成布局后再算。
    if (name === 'roi' && typeof window.__roiRelayout === 'function') {
      requestAnimationFrame(function () { window.__roiRelayout(); });
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  $$('.nav-btn').forEach(function (b) {
    b.addEventListener('click', function () { showPage(b.dataset.page); });
  });

  var mb = $('#menuBtn');
  if (mb) mb.addEventListener('click', function () { $('#sidebar').classList.toggle('open'); });
  document.addEventListener('click', function (e) {
    var sb = $('#sidebar');
    if (!sb || !sb.classList.contains('open')) return;
    if (sb.contains(e.target) || (mb && mb.contains(e.target))) return;
    sb.classList.remove('open');
  });

  // 支持 #roi 这类直达链接
  window.addEventListener('hashchange', function () { showPage(location.hash.slice(1), false); });
  showPage(location.hash.slice(1) || 'monitor', false);

  /* ---------------- 证据图缩放 / 平移 ---------------- */
  function makeZoomable(viewer, img, labelEl, resetBtn, openBtn) {
    if (!viewer || !img) return null;
    var scale = 1, tx = 0, ty = 0, dragging = false, sx = 0, sy = 0;
    var baseW = 0;

    function fit() {
      // 让图片按容器宽度铺满，作为 100% 基准
      var vw = viewer.clientWidth;
      if (!baseW) baseW = img.naturalWidth || vw;
      var h = img.naturalHeight || 1;
      img.style.width = vw + 'px';
      img.style.height = 'auto';
      viewer.style.height = Math.max(150, Math.round(vw * h / baseW)) + 'px';
      scale = 1; tx = 0; ty = 0; apply();
    }
    function apply() {
      img.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
      if (labelEl) labelEl.textContent = Math.round(scale * 100) + '%';
    }
    function zoomAt(cx, cy, factor) {
      var ns = Math.min(8, Math.max(0.5, scale * factor));
      if (ns === scale) return;
      // 以光标为锚点缩放
      tx = cx - (cx - tx) * (ns / scale);
      ty = cy - (cy - ty) * (ns / scale);
      scale = ns; apply();
    }

    viewer.addEventListener('wheel', function (e) {
      e.preventDefault();
      var r = viewer.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    }, { passive: false });

    function down(x, y) { dragging = true; sx = x - tx; sy = y - ty; viewer.classList.add('dragging'); }
    function move(x, y) { if (!dragging) return; tx = x - sx; ty = y - sy; apply(); }
    function up() { dragging = false; viewer.classList.remove('dragging'); }

    viewer.addEventListener('mousedown', function (e) { e.preventDefault(); down(e.clientX, e.clientY); });
    window.addEventListener('mousemove', function (e) { move(e.clientX, e.clientY); });
    window.addEventListener('mouseup', up);

    viewer.addEventListener('touchstart', function (e) {
      if (e.touches.length === 1) { down(e.touches[0].clientX, e.touches[0].clientY); }
    }, { passive: true });
    viewer.addEventListener('touchmove', function (e) {
      if (e.touches.length === 1) { e.preventDefault(); move(e.touches[0].clientX, e.touches[0].clientY); }
    }, { passive: false });
    viewer.addEventListener('touchend', up);

    viewer.addEventListener('dblclick', function () { scale = 1; tx = 0; ty = 0; apply(); });

    if (resetBtn) resetBtn.addEventListener('click', function () { scale = 1; tx = 0; ty = 0; apply(); });
    if (openBtn) openBtn.addEventListener('click', function () { window.open(img.src, '_blank'); });

    window.addEventListener('resize', function () { baseW = 0; fit(); });
    // 图片可能还没加载完
    if (img.complete) fit(); else img.addEventListener('load', fit);
    fit();
    return { zoomAt: zoomAt, reset: function () { scale = 1; tx = 0; ty = 0; apply(); } };
  }

  makeZoomable($('#evViewer'), $('#evImg'), $('#evZoom'), $('#evReset'), $('#evOpen'));

  /* ---------------- 报警记录：行选中联动详情 ---------------- */
  var DETAILS = [
    { t: '2026-10-01 15:01:12', id: 'ID 11', cls: '摩托车', dwell: '1.0 秒', frame: 37, plate: '未识别（该目标无牌或角度不可读）', color: '白色/银灰色' },
    { t: '2026-10-01 15:01:13', id: 'ID 3', cls: '摩托车', dwell: '1.0 秒', frame: 43, plate: '未识别（该目标无牌或角度不可读）', color: '银灰色' },
    { t: '2026-10-01 15:01:14', id: 'ID 15', cls: '摩托车', dwell: '1.1 秒', frame: 45, plate: '未识别（该目标无牌或角度不可读）', color: '白色' }
  ];
  var LOC = '湖北省荆州市 长江大学东校区';

  var detailBox = $('#alarmDetail');
  function selectAlarm(i) {
    $$('.data-table tbody tr').forEach(function (tr) {
      tr.classList.toggle('sel', +tr.dataset.row === i);
    });
    var d = DETAILS[i];
    if (!d || !detailBox) return;
    detailBox.innerHTML =
      '<div class="group-title">第 ' + (i + 1) + ' 次报警</div>' +
      '<div class="detail-lines">' +
      '<div>时间：' + d.t + '</div>' +
      '<div>目标：' + d.id + ' 类型：' + d.cls + '</div>' +
      '<div>停留：' + d.dwell + ' 帧号：' + d.frame + '</div>' +
      '<div>车牌号：' + d.plate + '</div>' +
      '<div>车身颜色：' + d.color + '</div>' +
      '<div>车辆型号：' + d.cls + '</div>' +
      '<div>监测地点：' + LOC + '</div>' +
      '</div>';
  }
  $$('.data-table tbody tr').forEach(function (tr) {
    tr.addEventListener('click', function () { selectAlarm(+tr.dataset.row); });
  });
  selectAlarm(0);

  /* ---------------- 监测地点保存 ---------------- */
  var locInput = $('#locInput'), locHint = $('#locHint'), btnSaveLoc = $('#btnSaveLoc');
  if (btnSaveLoc) {
    btnSaveLoc.addEventListener('click', function () {
      var v = (locInput.value || '').trim();
      LOC = v;
      if (v) {
        locHint.textContent = '已保存：' + v;
        locHint.className = 'hint ok';
        toast('监测地点已保存：' + v);
      } else {
        locHint.textContent = '地点已清空，证据图上不再显示该行';
        locHint.className = 'hint';
        toast('监测地点已清空');
      }
      // 详情里的地点同步更新
      var cur = $('.data-table tbody tr.sel');
      selectAlarm(cur ? +cur.dataset.row : 0);
    });
  }

  /* ---------------- 播放检测演示 ---------------- */
  var btnDemo = $('#btnDemo'), btnStart = $('#btnStart'), btnPause = $('#btnPause'),
      btnRestart = $('#btnRestart'), fill = $('#progressFill'), timeLabel = $('#timeLabel'),
      scanLine = $('#scanLine'), alertBand = $('#alertBand'),
      hud = $('#monitorHud'), chipIn = $('#chipInRoi'), chipAl = $('#chipAlarm'),
      chipState = $('#chipState');

  var TOTAL = 141, FPS = 30, playing = false, paused = false, frame = 0, timer = null;

  // 页面刚打开时先展示"检测已完成"的最终状态 —— 评委点进来的第一眼
  // 应该是「区域内 5 / 报警 3」，而不是一片 0。点播放才从 0 开始跑。
  var SHOWCASE = 45;

  function fmt(f) {
    var s = Math.floor(f / FPS);
    return '00:' + String(s).padStart(2, '0');
  }
  function render(noop) {
    var pct = Math.min(100, frame / TOTAL * 100);
    if (fill) fill.style.width = pct + '%';
    var inRoi = frame < 20 ? 0 : Math.min(5, 1 + Math.floor((frame - 20) / 12));
    var alarms = frame < 37 ? 0 : frame < 43 ? 1 : frame < 45 ? 2 : 3;
    if (timeLabel) timeLabel.textContent = fmt(frame) + ' / ' + fmt(TOTAL);
    if (hud) hud.textContent = 'Frame ' + frame + ' ｜ 区域内 ' + inRoi + ' ｜ 报警 ' + alarms + ' ｜ 阈值 1.0s';
    if (chipIn) chipIn.textContent = inRoi;
    if (chipAl) chipAl.textContent = alarms;
    if (alertBand) alertBand.classList.toggle('on', alarms > 0);
    if (chipState) {
      if (paused) chipState.textContent = '已暂停';
      else if (playing) chipState.textContent = alarms > 0 ? '检测中 · 有报警' : '检测中 · 正常';
      else chipState.textContent = alarms > 0 ? '检测完成 · 有报警' : '就绪';
    }
  }
  function tick() {
    if (paused) return;
    frame += 2;
    if (frame >= TOTAL) {
      frame = TOTAL; render(); stop(true);
      toast('检测完成：处理 ' + TOTAL + ' 帧，触发 3 次盲道占用报警');
      return;
    }
    render();
  }
  function play() {
    if (playing) return;
    playing = true; paused = false;
    if (frame >= TOTAL) frame = 0;
    if (scanLine) scanLine.classList.add('on');
    if (btnPause) { btnPause.disabled = false; btnPause.textContent = '暂停'; }
    if (btnRestart) btnRestart.disabled = false;
    if (btnStart) btnStart.disabled = true;
    clearInterval(timer);
    timer = setInterval(tick, 55);
    render();
  }
  function stop(done) {
    playing = false;
    clearInterval(timer);
    if (scanLine) scanLine.classList.remove('on');
    if (btnStart) btnStart.disabled = false;
    if (!done && btnPause) btnPause.disabled = true;
    if (done) {
      if (btnPause) { btnPause.disabled = true; btnPause.textContent = '暂停'; }
      if (alertBand) alertBand.classList.remove('on');
    } else {
      if (alertBand) alertBand.classList.remove('on');
    }
  }

  if (btnDemo) btnDemo.addEventListener('click', function () {
    showPage('monitor');
    frame = 0; render(); play();
  });
  if (btnStart) btnStart.addEventListener('click', function () {
    frame = 0; render(); play(); toast('开始检测（这是静态演示，界面与真实软件一致）');
  });
  if (btnPause) btnPause.addEventListener('click', function () {
    if (!playing) return;
    paused = !paused;
    btnPause.textContent = paused ? '继续' : '暂停';
    if (chipState) chipState.textContent = paused ? '已暂停' : '检测中 · 正常';
  });
  if (btnRestart) btnRestart.addEventListener('click', function () {
    frame = 0; render(); paused = false;
    if (btnPause) btnPause.textContent = '暂停';
    toast('已回到视频开头');
    btnRestart.disabled = true;
    setTimeout(function () { if (playing) btnRestart.disabled = false; }, 1200);
  });

  // 首屏展示最终状态（区域内 5 / 报警 3），而不是一片 0
  frame = SHOWCASE;
  render();

  /* ---------------- 区域标定页：角点列表可点选高亮 ---------------- */
  var coordItems = $$('#coordList > div');
  coordItems.forEach(function (el, i) {
    el.style.cursor = 'pointer';
    el.addEventListener('click', function () {
      coordItems.forEach(function (o) { o.style.color = ''; o.style.fontWeight = ''; });
      el.style.color = '#0E7A7A';
      el.style.fontWeight = '700';
      toast('已选中第 ' + (i + 1) + ' 个角点（在真实软件里拖动画面上的手柄即可调整）');
    });
  });

  /* ======================================================================
     演示页可用性处理
     ----------------------------------------------------------------------
     这个静态站是桌面软件的"镜像展示"。有一批按钮对应的是浏览器做不到的
     操作（选本地文件、读写 rois.json、打开资源管理器、导出文件）。
     它们看上去和真按钮一样，点了却毫无反应 —— 评委很容易以为"坏了"。

     这里统一处理：
       1) 带 data-demo 的按钮降一档视觉权重，并加"演示"角标
       2) 点击时弹出说明，讲清"在桌面软件里它会做什么"
       3) 少数在浏览器里本来就能做的（ROI 相关），做成真的能用
     ====================================================================== */

  var DEFAULT_ROI = [[649, 208], [719, 203], [608, 719], [436, 719]];

  function setRegionHint(n) {
    var h = $('#roiRegionHint');
    if (h) h.textContent = n > 0 ? ('区域 1：' + n + ' 个顶点') : '（暂无区域）';
  }

  /* ---- 1a) data-demo：演示页做不到的操作，点一下说明它在软件里的作用 ---- */
  $$('[data-demo]').forEach(function (b) {
    b.setAttribute('title', '演示页不可用 —— 点一下看说明');
    b.addEventListener('click', function () {
      toast(b.dataset.demo);
    });
  });

  /* ---- 1b) data-info：演示页真能用，只是补充一句说明 ---- */
  $$('[data-info]').forEach(function (b) {
    b.setAttribute('title', b.dataset.info);
    // 有专属逻辑的按钮（下面的 ROI 那几个）自己会弹提示，这里不重复绑
    if (b.id) return;
    b.addEventListener('click', function () {
      toast(b.dataset.info);
    });
  });

  /* ======================================================================
     区域标定页：真正可拖拽的 ROI 手柄
     ----------------------------------------------------------------------
     背景：这一页原来只放了一张 roicanvas.jpg，四个"角点"是画在图片像素里
     的装饰，看着像手柄但拖不动 —— 用户反馈「roi 的四个角点依旧无法移动」。

     现在按坐标动态生成 4 个手柄 + 一条 SVG 连线，坐标空间就是原图尺寸
     （1280×720，与 config/rois.json 一致），所以右侧显示的坐标是真实坐标，
     不是估计值。指针位置通过图片的 getBoundingClientRect() 换算，
     因此窗口缩放、手机窄屏下都准确。
     ====================================================================== */

  var IMG_W = 1280, IMG_H = 720;        // roicanvas.jpg 的原始尺寸
  // DEFAULT_ROI 在下面统一声明一次（见"演示页可用性处理"段末）

  var roiState = {
    pts: DEFAULT_ROI.map(function (p) { return [p[0], p[1]] }),
    dragIdx: -1,
    mode: 'drag'                        // drag = 拖拽四点；click = 点击加点
  };

  var roiImg = $('#roiImg'),
      roiSvg = $('#roiSvg'),
      roiHandles = $('#roiHandles'),
      roiCanvas = $('#roiCanvas');

  function imgRect() { return roiImg ? roiImg.getBoundingClientRect() : null; }

  function paintCoordList() {
    var box = $('#coordList');
    if (!box) return;
    var pts = roiState.pts;
    if (!pts.length) {
      box.innerHTML = '<div style="color:#829999">（尚未载入多边形）</div>';
      return;
    }
    box.innerHTML = pts.map(function (p, i) {
      return '<div data-pt="' + i + '" style="cursor:pointer">'
        + (i + 1) + '. (' + Math.round(p[0]) + ', ' + Math.round(p[1]) + ')</div>';
    }).join('');
    $$('#coordList > div').forEach(function (el, i) {
      el.addEventListener('click', function () {
        $$('#coordList > div').forEach(function (o) {
          o.style.color = ''; o.style.fontWeight = '';
        });
        el.style.color = '#0E7A7A';
        el.style.fontWeight = '700';
        highlightHandle(i);
        toast('已选中第 ' + (i + 1) + ' 个角点（' + Math.round(pts[i][0])
          + ', ' + Math.round(pts[i][1]) + '）');
      });
    });
  }

  function highlightHandle(i) {
    $$('.roi-handle').forEach(function (h, k) {
      h.style.boxShadow = k === i
        ? '0 0 0 5px rgba(14,122,122,.28), 0 2px 8px rgba(10,50,50,.45)'
        : '';
    });
  }

  /* 把图片像素坐标换算成相对图片左上角的 CSS 像素 */
  function toScreen(p) {
    var r = imgRect();
    if (!r) return { x: 0, y: 0 };
    return { x: r.left + p[0] / IMG_W * r.width,
             y: r.top + p[1] / IMG_H * r.height };
  }

  /* 指针位置 -> 图片像素坐标（并夹在图片范围内） */
  function toImage(clientX, clientY) {
    var r = imgRect();
    if (!r || !r.width) return [0, 0];
    var x = (clientX - r.left) / r.width * IMG_W;
    var y = (clientY - r.top) / r.height * IMG_H;
    return [Math.max(0, Math.min(IMG_W, Math.round(x))),
            Math.max(0, Math.min(IMG_H, Math.round(y)))];
  }

  function renderRoi() {
    var r = imgRect();
    if (!r) return;
    // ★ 页面隐藏时（.page{display:none}）图片 rect 全是 0。
    //   此时如果照样写手柄位置，就会把四个点全钉在 (0,0)，
    //   而且之后不会自动纠正 —— 必须直接返回，等页面可见时再算。
    //   （实测踩过：手柄全堆在画布左上角。）
    if (r.width < 60 || r.height < 30) {
      window.__roiNeedsLayout = true;
      return;
    }
    window.__roiNeedsLayout = false;

    // SVG 覆盖层对齐到图片实际渲染区域（图片按宽度铺满，高度自适应）
    if (roiSvg) {
      roiSvg.style.left = (r.left - roiCanvas.getBoundingClientRect().left) + 'px';
      roiSvg.style.top = (r.top - roiCanvas.getBoundingClientRect().top) + 'px';
      roiSvg.style.width = r.width + 'px';
      roiSvg.style.height = r.height + 'px';
      roiSvg.setAttribute('viewBox', '0 0 ' + IMG_W + ' ' + IMG_H);
      roiSvg.setAttribute('preserveAspectRatio', 'none');
      var poly = roiState.pts.map(function (p) { return p[0] + ',' + p[1]; }).join(' ');
      roiSvg.innerHTML = roiState.pts.length >= 3
        ? '<polygon points="' + poly + '" fill="rgba(14,122,122,.20)" '
          + 'stroke="#0E7A7A" stroke-width="3" stroke-linejoin="round"/>'
        : (roiState.pts.length === 2
          ? '<line x1="' + roiState.pts[0][0] + '" y1="' + roiState.pts[0][1]
            + '" x2="' + roiState.pts[1][0] + '" y2="' + roiState.pts[1][1]
            + '" stroke="#0E7A7A" stroke-width="3"/>'
          : '');
    }

    // 手柄：用**百分比**定位，相对 .roi-handles（其 inset:0 与图片完全重合，
    // 因为 .canvas 无 padding、图片是第一个子元素且 width:100%）。
    // 用百分比的好处：窗口缩放、手机横竖屏切换时手柄自动跟着走，不必重算。
    if (roiHandles) {
      roiHandles.innerHTML = roiState.pts.map(function (p, i) {
        var xp = p[0] / IMG_W * 100;
        var yp = p[1] / IMG_H * 100;
        return '<div class="roi-handle" data-idx="' + i + '" '
          + 'style="left:' + xp.toFixed(4) + '%;top:' + yp.toFixed(4) + '%" '
          + 'title="角点 ' + (i + 1) + '：(' + Math.round(p[0]) + ', '
          + Math.round(p[1]) + ')　按住拖动">' + (i + 1) + '</div>';
      }).join('');
      bindHandles();
    }
  }

  function bindHandles() {
    $$('.roi-handle').forEach(function (h) {
      var idx = +h.dataset.idx;
      h.addEventListener('pointerdown', function (e) {
        if (roiState.mode !== 'drag') return;
        e.preventDefault();
        e.stopPropagation();
        roiState.dragIdx = idx;
        h.classList.add('dragging');
        h.setPointerCapture && h.setPointerCapture(e.pointerId);
      });
      h.addEventListener('pointermove', function (e) {
        if (roiState.dragIdx !== idx) return;
        e.preventDefault();
        var p = toImage(e.clientX, e.clientY);
        roiState.pts[idx] = p;
        // 同样用百分比（与 renderRoi 保持一致）
        h.style.left = (p[0] / IMG_W * 100).toFixed(4) + '%';
        h.style.top = (p[1] / IMG_H * 100).toFixed(4) + '%';
        h.title = '角点 ' + (idx + 1) + '：(' + p[0] + ', ' + p[1] + ')　按住拖动';
        updateCoordRow(idx);
        updateSvgOnly();
      });
      var end = function (e) {
        if (roiState.dragIdx !== idx) return;
        roiState.dragIdx = -1;
        h.classList.remove('dragging');
        h.releasePointerCapture && e.pointerId !== undefined
          && h.hasPointerCapture && h.hasPointerCapture(e.pointerId)
          && h.releasePointerCapture(e.pointerId);
        setRegionHint(roiState.pts.length);
        var p = roiState.pts[idx];
        toast('角点 ' + (idx + 1) + ' 已移到 (' + p[0] + ', ' + p[1] + ')');
      };
      h.addEventListener('pointerup', end);
      h.addEventListener('pointercancel', end);
    });
  }

  /* 只重画连线，别重建手柄 —— 否则拖动过程中会把手柄本身删掉 */
  function updateSvgOnly() {
    if (!roiSvg) return;
    var poly = roiState.pts.map(function (p) { return p[0] + ',' + p[1]; }).join(' ');
    roiSvg.innerHTML = roiState.pts.length >= 3
      ? '<polygon points="' + poly + '" fill="rgba(14,122,122,.20)" '
        + 'stroke="#0E7A7A" stroke-width="3" stroke-linejoin="round"/>'
      : '';
  }

  function updateCoordRow(i) {
    var rows = $$('#coordList > div');
    if (rows[i] && roiState.pts[i]) {
      rows[i].textContent = (i + 1) + '. (' + roiState.pts[i][0] + ', '
        + roiState.pts[i][1] + ')';
    }
  }

  /* 点击加点模式：在图片上点一下加一个顶点 */
  var downPt = null;
  if (roiImg) {
    roiImg.addEventListener('pointerdown', function (e) {
      if (roiState.mode !== 'click') return;
      downPt = [e.clientX, e.clientY];
    });
    roiImg.addEventListener('pointerup', function (e) {
      if (roiState.mode !== 'click' || !downPt) return;
      // 拖动超过 6px 认为是误触（比如想平移图片）
      if (Math.abs(e.clientX - downPt[0]) > 6
        || Math.abs(e.clientY - downPt[1]) > 6) { downPt = null; return; }
      downPt = null;
      roiState.pts.push(toImage(e.clientX, e.clientY));
      paintCoordList();
      renderRoi();
      setRegionHint(roiState.pts.length);
      toast('已添加第 ' + roiState.pts.length + ' 个顶点');
    });
  }

  /* 窗口尺寸变化时重新按比例摆放手柄 */
  var roiResizeTimer = null;
  window.addEventListener('resize', function () {
    clearTimeout(roiResizeTimer);
    roiResizeTimer = setTimeout(renderRoi, 120);
  });

  /* 供 showPage() 调用：切到本页时重新定位手柄 */
  window.__roiRelayout = renderRoi;
  /* 让 imgRect() 在页面隐藏时也能拿到"假设可见"的尺寸 —— 不能真这么做，
     所以改为：只要检测到图片 rect 为 0 就标记"位置待定"，等可见时再算。 */
  window.__roiNeedsLayout = true;

  /* ---- 按钮 ---- */
  var roiLoad = $('#roiLoad');
  if (roiLoad) {
    roiLoad.addEventListener('click', function () {
      roiState.pts = DEFAULT_ROI.map(function (p) { return [p[0], p[1]] });
      paintCoordList(); renderRoi(); setRegionHint(roiState.pts.length);
      toast('已载入已保存的 ROI（4 个角点），可以直接拖动圆点微调');
    });
  }

  var roiClear = $('#roiClear');
  if (roiClear) {
    roiClear.addEventListener('click', function () {
      roiState.pts = [];
      paintCoordList(); renderRoi(); setRegionHint(0);
      toast('已清空当前多边形（已保存的区域不受影响）');
    });
  }

  var roiUndo = $('#roiUndo');
  if (roiUndo) {
    roiUndo.addEventListener('click', function () {
      if (!roiState.pts.length) { toast('当前没有可撤销的顶点'); return; }
      roiState.pts.pop();
      paintCoordList(); renderRoi(); setRegionHint(roiState.pts.length);
      toast('已撤销一个顶点，剩余 ' + roiState.pts.length + ' 个');
    });
  }

  var roiDeleteAll = $('#roiDeleteAll');
  if (roiDeleteAll) {
    roiDeleteAll.addEventListener('click', function () {
      toast(roiDeleteAll.dataset.demo);
    });
  }

  var roiReload = $('#roiReload');
  if (roiReload) {
    roiReload.addEventListener('click', function () {
      roiState.pts = DEFAULT_ROI.map(function (p) { return [p[0], p[1]] });
      paintCoordList(); renderRoi(); setRegionHint(roiState.pts.length);
      toast('已恢复为 config/rois.json 里的区域（4 个顶点）');
    });
  }

  var roiSave = $('#roiSave');
  if (roiSave) {
    roiSave.addEventListener('click', function () {
      toast(roiSave.dataset.demo);
    });
  }

  /* 编辑方式切换 */
  var roiMode = $('#roiMode');
  if (roiMode) {
    roiMode.addEventListener('change', function () {
      roiState.mode = roiMode.value;
      var hint = $('#roiModeHint');
      var tip = document.querySelector('.roi-canvas')
        && document.querySelector('.roi-canvas').previousElementSibling;
      if (roiState.mode === 'click') {
        if (hint) hint.textContent = '在画面上单击添加顶点，右键「撤销上一个顶点」回退。至少 3 个顶点。';
        toast('已切到点击加点模式：在画面上单击即可加顶点');
      } else {
        if (hint) hint.textContent = '画面上有 4 个可拖拽的角点（编号 1~4），按住任意一个拖动即可自由拉伸盲道区域。';
        toast('已切回拖拽四点模式');
      }
    });
  }

  /* 初始渲染：等图片加载完再摆手柄（否则拿不到正确的 rect）。
     注意如果当前不在 ROI 页，renderRoi() 会自行跳过（rect 为 0），
     等 showPage('roi') 时通过 window.__roiRelayout 补算。 */
  if (roiImg) {
    if (roiImg.complete) { renderRoi(); }
    else { roiImg.addEventListener('load', renderRoi); }
  }
  window.addEventListener('load', function () {
    setTimeout(renderRoi, 60);
    // 如果直接把 #roi 写进 URL，showPage 在初次调用时 __roiRelayout 已就绪，
    // 但首帧布局可能还没完成，这里再补一次。
    if (location.hash.slice(1) === 'roi') setTimeout(renderRoi, 150);
  });
})();
