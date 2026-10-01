# 源码说明 · Blindway Grand

> 盲道占用检测系统 —— 完整 Python 源码。
> 界面演示（无需安装任何东西）：**https://snowcode-a.github.io/blindway-grand/**

---

## 快速运行

**Windows：双击 `run.bat`**

脚本会自动：
1. 找 Python 解释器（支持任意安装路径，不写死）
2. 检查依赖，缺了自动 `pip install`
3. 启动图形界面

**手动运行：**

```bash
pip install torch torchvision opencv-python PyQt5 ultralytics numpy Pillow hyperlpr3 onnxruntime
python qt_app.py
```

> `yolov8n.pt`（YOLO 权重，约 6MB）未上传，首次运行时 ultralytics 会自动下载。
> 演示视频也未上传，请自备一段俯拍盲道的视频，或在界面里点「选择视频…」。

---

## 目录结构

```
.
├── qt_app.py              主程序：PyQt5 界面（约 3000 行）
│                          ├─ VideoCanvas   可缩放/平移/四点拖拽标定的画布
│                          ├─ DetectWorker  后台检测线程
│                          ├─ StatCard      指标卡
│                          └─ MainWindow    四页主窗口
├── detector.py            检测引擎（与界面解耦）
│                          └─ GuardEngine   YOLO + ROI + 停留计时 + 报警去重
├── evidence.py            取证模块
│                          └─ VehicleEvidence  车牌识别 + 车色 + 车型 + 证据图合成
├── web_mirror.py          网页镜像服务（把界面推到浏览器，供远程查看）
│
├── ui/
│   ├── style.qss          样式表（浅色青绿主题，设计令牌集中在此）
│   ├── selftest.py        界面自检（25 项断言，含真实推理）
│   ├── startup_check.py   启动自检（真实子进程启动，覆盖 __main__ 盲区）
│   └── check_layout.py    布局几何体检（查重叠/裁切）
│
├── config/
│   ├── settings.json      检测参数（阈值、置信度、确认帧数、追踪器等）
│   └── rois.json          盲道 ROI 多边形（由界面标定后写入）
│
├── launcher/              VS2022 启动器（C++，可用 F5 调试整个工程）
│   ├── launcher.cpp
│   └── launcher.vcxproj
├── BlindwayGrand.sln      VS2022 解决方案
│
├── build.spec             PyInstaller 打包配置
├── pyi_hooks/             两个自定义 hook（解决 onnxruntime / hyperlpr3 打包问题）
└── run.bat / 启动网页版.bat
```

---

## 核心实现要点

### 1. 判定逻辑：底边中点，不是框交叠

`detector.py` 里对每个检测框取**底边中点**，判断它是否落在 ROI 多边形内：

```python
def bottom_center(box):
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int(y2))
```

**为什么这样做**：检测框会覆盖大片背景，直接用框和区域算交叠，
**停在盲道旁边的车也会被误判成占用**。底边中点（车轮着地点）
才是车辆真正压在地面的位置。

### 2. 停留计时 + 报警去重

```python
if inside:
    st["hit"] += 1
    if st["hit"] >= self.confirm_frames:      # 连续 3 帧才算进入
        if st["enter"] is None:
            st["enter"] = now
        st["inside"] = True
else:
    st["hit"] = max(0, st["hit"] - 1)
    if st["hit"] == 0:                        # 真离开了才重置
        st["inside"] = False
        st["enter"] = None
        st["alarmed"] = False

dwell = (now - st["enter"]) if st["inside"] else 0.0
occupied = st["inside"] and dwell >= self.dwell_limit
```

- `confirm_frames=3`：快速经过不算占用
- `dwell_limit=1.0s`：停留超时才报警
- `alarmed` 标志：同一辆车在一次连续占用中**只报一次**，离开后重新驶入才再报

### 3. 追踪器选型：BoT-SORT

`BoT-SORT` 带相机运动补偿。实拍手持视频对比：

| 追踪器 | 有效追踪帧数 |
|---|---|
| ByteTrack | 39 |
| **BoT-SORT** | **205（+426%）** |

静态场景也提升 +2%，速度几乎不变。

### 4. 车牌识别的防误报设计

`evidence.py` 里做了**双重校验**，实测把美团单车车身上的广告文字
误识成车牌（`冀DA00183F`，9 个字符）的情况挡掉了：

```python
MIN_PLATE_CONF = 0.70          # 置信度门槛

def is_valid_plate(text, conf):
    # 严格校验中国车牌格式：7 位（普通）或 8 位（新能源）
    # 省份简称 + 字母 + 字母数字
    ...
```

**设计取舍：宁可不识别，也不编造车牌号。**
演示视频里车辆多无牌或角度不可读，系统如实输出"未识别"，
改用**车身颜色 + 车型**作为替代特征。

### 5. 证据图合成

`VehicleEvidence.build()` 用 Pillow 合成一张可供执法的证据图：

```
┌──────────────────────────┬─────────────────┐
│  报警时刻原图             │  车辆放大特写    │
│  （红框标出涉事车辆）      │  车牌区          │
│                          │  车辆特征栏：     │
│                          │   车牌号/类型     │
│                          │   车身颜色/车型   │
│                          │   停留时长/时间   │
│                          │   帧号/监测地点   │
├──────────────────────────┴─────────────────┤
│  判定依据：车辆底边中点落在盲道 ROI 内       │
│            且停留超过阈值 -> 判定为占用      │
└────────────────────────────────────────────┘
```

> 布局是动态算的：长地点文本会自动折行，空间不够时把"帧号"移到顶部信息条，
> 保证各个区块**永不重叠**。

---

## 界面四个页面

| 页面 | 功能 |
|---|---|
| 实时监控 | 检测画面 + 区域内目标表 + 实时统计 + 参数在线调节 |
| 区域标定 | 拖动 4 个角点框出盲道，保存后立即生效 |
| 报警记录 | 报警明细表 + 证据大图（可缩放看车牌）+ CSV 导出 |
| 数据报告 | 处理帧数 / 报警次数 / 最长停留 / 占用占比 + 柱状图 |

---

## 自检脚本

改完代码建议跑一遍：

```bash
python ui/startup_check.py    # ★ 真实子进程启动，覆盖 __main__ 盲区
python ui/selftest.py         # 25 项界面断言 + 真实推理
python ui/check_layout.py     # 布局几何体检
```

> `startup_check.py` 是特意补的：`selftest.py` 直接 `MainWindow()` 构造窗口，
> **从不执行 `qt_app.py` 的 `if __name__ == "__main__":` 块**，
> 所以 `__main__` 里一旦有错，selftest 全绿但用户一双击就崩。
> 这个脚本就是补这个盲区（开发中确实踩过这个坑）。

---

## 实测数据

| 指标 | 数值 |
|---|---|
| 检测速度 | 51~71 ms/帧（纯 CPU，无显卡） |
| 车牌识别 | 约 7 ms/次 |
| 演示视频有效检测 | 141 帧中 116~139 帧检测到目标落在盲道区域内 |
| 停留判定阈值 | 1.0 秒（确认帧数 3 帧） |
| 代码规模 | 约 5500 行（界面 + 引擎 + 取证 + 测试） |

---

## 依赖

```
Python      >= 3.10
PyQt5       == 5.15.*
torch       （CPU 版即可）
ultralytics >= 8.4
opencv-python
numpy
Pillow
hyperlpr3   （中文车牌识别）
onnxruntime （hyperlpr3 的推理后端）
```

---

## 许可

本项目为参赛作品，源码仅供查看与评审使用。
如需引用其中的实现或数据，请先联系作者。
