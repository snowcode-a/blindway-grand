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

  function paintCoordList(pts) {
    var box = $('#coordList');
    if (!box) return;
    if (!pts.length) {
      box.innerHTML = '<div style="color:#829999">（尚未载入多边形）</div>';
      return;
    }
    box.innerHTML = pts.map(function (p, i) {
      return '<div style="cursor:pointer">' + (i + 1) + '. (' + p[0] + ', ' + p[1] + ')</div>';
    }).join('');
    // 重新绑定点击高亮
    var items = $$('#coordList > div');
    items.forEach(function (el, i) {
      el.addEventListener('click', function () {
        items.forEach(function (o) { o.style.color = ''; o.style.fontWeight = ''; });
        el.style.color = '#0E7A7A';
        el.style.fontWeight = '700';
        toast('已选中第 ' + (i + 1) + ' 个角点'
          + (pts.length === 4 ? '（在真实软件里拖动画面上的手柄即可调整）' : ''));
      });
    });
  }

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

  /* ---- 2) ROI 相关：在浏览器里做成真能用的 ---- */
  var roiLoad = $('#roiLoad');
  if (roiLoad) {
    roiLoad.addEventListener('click', function () {
      paintCoordList(DEFAULT_ROI);
      setRegionHint(DEFAULT_ROI.length);
      toast('已载入当前 ROI 的 4 个角点（真实软件里可直接拖动画面上的手柄微调）');
    });
  }

  var roiClear = $('#roiClear');
  if (roiClear) {
    roiClear.addEventListener('click', function () {
      paintCoordList([]);
      setRegionHint(0);
      toast('已清空当前编辑的多边形（已保存的区域不受影响）');
    });
  }

  var roiDeleteAll = $('#roiDeleteAll');
  if (roiDeleteAll) {
    roiDeleteAll.addEventListener('click', function () {
      paintCoordList([]);
      setRegionHint(0);
      toast('演示页不会真的删除 —— 这是作者在该路段实测标定的区域');
    });
  }

  var roiReload = $('#roiReload');
  if (roiReload) {
    roiReload.addEventListener('click', function () {
      paintCoordList(DEFAULT_ROI);
      setRegionHint(DEFAULT_ROI.length);
      toast('已恢复为 config/rois.json 里的区域（4 个顶点）');
    });
  }

  var roiSave = $('#roiSave');
  if (roiSave) {
    roiSave.addEventListener('click', function () {
      toast('演示页不写文件。真实软件里这一步会把多边形写入 config/rois.json 并立即生效。');
    });
  }
})();
