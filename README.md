# Smart Bookshelf

家庭智慧书架 Web / API 项目，集成书架格口管理、OCR 存书、YOLO ROI 定位、语音交互、家庭成员管理、阅读统计和推荐能力。

## 功能概览

- 书架格口管理：查看格口占用状态、查询格口内图书。
- 拍照存书：浏览器摄像头拍照，YOLO 定位 ROI，PaddleOCR 识别文字，再匹配图书并分配空格口。
- 书脊扫描：前端可勾选“启用扫描书脊”，切换到书脊 YOLO 模型。
- 实时 YOLO 框：摄像头弹窗会持续画出 YOLO 检测框，识别失败时不会自动关闭，会继续扫描直到成功或手动取消。
- 取书：支持按格口取书、按文本模糊匹配取书。
- 图书管理：支持图书查询、创建、更新。
- 家庭与成员：支持家庭、账号、成员关系和当前成员切换。
- 阅读分析：支持阅读目标、借阅日志、周报、月报、徽章、推荐和阅读事件埋点。
- 语音能力：支持语音转文本、唤醒词、TTS 播报和 SSE 语音事件流。

## 目录结构

```text
bookshelf/
├─ app.py                    Flask 启动入口
├─ config.py                 配置与环境变量
├─ api/                      Flask API 蓝图
├─ services/                 业务服务层
├─ db/                       数据库操作
├─ ai/                       AI 对话、图书匹配、语音模块
├─ ocr/
│  ├─ paddle_ocr.py          PaddleOCR 封装
│  ├─ video_ocr.py           本机摄像头 OCR 流程
│  └─ yolo_roi.py            YOLO ROI 检测与 ROI OCR
├─ YOLO_model/
│  ├─ book_cover.pt          封面 title / author ROI 模型
│  └─ book_spine.pt          书脊 ROI 模型
├─ static/                   前端 JS / CSS
├─ templates/                HTML 模板
├─ data/                     本地 SQLite 数据目录，数据库文件不提交
├─ tests/                    测试用例
└─ requirements.txt          Python 依赖
```

## YOLO 模型

项目默认从本仓库内的 `YOLO_model` 目录读取模型：

| 用途 | 默认路径 | 环境变量覆盖 |
| --- | --- | --- |
| 封面标题/作者 ROI | `YOLO_model/book_cover.pt` | `BOOK_COVER_YOLO_MODEL` |
| 书脊 ROI | `YOLO_model/book_spine.pt` | `BOOK_SPINE_YOLO_MODEL` |

默认存书流程使用封面模型。前端勾选“启用扫描书脊”后，请求会带上 `scan_spine=1`，后端会切换到书脊模型。

可调参数：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YOLO_OCR_CONF` | `0.15` | YOLO 置信度阈值 |
| `YOLO_OCR_IOU` | `0.7` | YOLO NMS IoU |
| `YOLO_OCR_MAX_DET` | `20` | 单张图最大检测框数 |
| `YOLO_OCR_PADDING_RATIO` | `0.04` | OCR 裁剪 ROI 时的边距比例 |

## 语音模型

语音相关的大模型和本地运行时不随仓库提交，按需放在本地路径或用环境变量指定：

| 用途 | 默认本地路径 | 环境变量覆盖 |
| --- | --- | --- |
| Vosk 中文 ASR 模型 | `models/vosk-cn` | `VOSK_MODEL_PATH` |
| Piper 可执行文件 | `tools/piper/piper/piper.exe` | `PIPER_BIN` |
| Piper 中文 TTS 模型 | `tools/piper/models/zh_CN-huayan-medium.onnx` | `PIPER_MODEL` |
| Piper TTS 配置 | `tools/piper/models/zh_CN-huayan-medium.onnx.json` | `PIPER_CONFIG` |

如果这些本地模型不存在，对应的离线 ASR / Piper TTS 能力会不可用；Edge TTS 或系统 TTS 仍可按当前环境继续尝试。

## 安装

建议使用 Python 3.9。

```bash
pip install -r requirements.txt
```

主要依赖：

- `paddleocr==2.7.3`
- `ultralytics==8.3.225`
- `opencv-python`
- `numpy==1.26.4`
- `thefuzz`
- `vosk`
- `edge-tts`
- `sounddevice`

## 运行

```bash
python app.py
```

默认监听：

```text
http://0.0.0.0:5000
```

浏览器访问：

```text
http://127.0.0.1:5000
```

如需启用后台唤醒监听：

```powershell
$env:ENABLE_WAKE_LISTEN="1"
python app.py
```

## 常用环境变量

| 变量名 | 默认值 | 作用 |
| --- | --- | --- |
| `VOICE_MODE` | `auto` | 语音模式分发 |
| `VOICE_MODEL_DISPATCH` | `0` | 是否启用模型分发 |
| `VOSK_MODEL_PATH` | `models/vosk-cn` | Vosk 离线语音识别模型目录 |
| `PIPER_BIN` | 自动查找 | Piper 可执行文件路径 |
| `PIPER_MODEL` | 自动查找 | Piper `.onnx` 语音模型路径 |
| `PIPER_CONFIG` | 自动查找 | Piper 模型 `.json` 配置路径 |
| `ENABLE_WAKE_LISTEN` | 未启用 | 是否启动后台唤醒监听线程 |
| `WAKE_DEBUG_LOG` | `0` | 是否输出唤醒调试日志 |
| `CAMERA_SOURCE` | `http://10.165.117.25:8080` | 后端摄像头来源，数字表示本机摄像头编号，HTTP 根地址会自动尝试 `/video` |
| `PADDLEOCR_SHOW_LOG` | `0` | 是否显示 PaddleOCR 日志 |
| `PUBLIC_BASE_URL` | 空 | 对外访问基础地址 |
| `PI_BRIDGE_BASE_URL` | `http://127.0.0.1:8765` | 树莓派桥接服务地址 |

## OCR 存书流程

1. 前端打开摄像头。
2. 前端定时上传当前帧到 `/api/ocr/rois`。
3. 后端只运行 YOLO，返回 ROI 框。
4. 前端在摄像头画面上画框。
5. 检测到 ROI 后，前端周期性调用 `/api/ocr/ingest`。
6. 后端用同一模式的 YOLO 裁剪 ROI，再调用 PaddleOCR 识别 ROI 内文字。
7. 识别出的文本进入图书匹配逻辑，匹配成功后生成存书动作。
8. 前端提交动作并刷新书架。

识别失败时，摄像头弹窗不会自动关闭，会继续扫描。用户可以调整角度、距离和光线，直到识别成功或手动取消。

## 关键 API

### 书架与存取

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/compartments` | 查询全部格口状态 |
| `POST` | `/api/store` | 本机摄像头流程存书 |
| `POST` | `/api/take` | 按格口取书 |
| `POST` | `/api/take_by_text` | 按文本模糊匹配取书 |
| `POST` | `/api/ocr/rois` | 上传一帧图片，只返回 YOLO ROI 框 |
| `POST` | `/api/ocr/ingest` | 上传图片并执行 YOLO ROI OCR 存书 |

`/api/ocr/ingest` 参数：

- `image`：`multipart/form-data` 文件字段，必填。
- `source`：可选，`ui` / `web` 时不推送语音事件。
- `audio=1`：可选，返回 TTS 音频。
- `scan_spine=1`：可选，启用书脊模型。

### 图书与用户

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/books` | 查询图书列表 |
| `POST` | `/api/books` | 创建图书 |
| `GET` | `/api/books/<book_id>` | 查询图书详情 |
| `PUT` | `/api/books/<book_id>` | 更新图书 |
| `GET` | `/api/users` | 查询家庭成员 |
| `POST` | `/api/users` | 创建家庭成员 |
| `GET` | `/api/users/current` | 获取当前活跃成员 |
| `POST` | `/api/users/switch` | 切换当前活跃成员 |

### 对话、语音与 TTS

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/chat` | 文本对话 |
| `GET` | `/api/chat/history` | 当前用户对话历史 |
| `POST` | `/api/chat/clear` | 清空对话历史 |
| `POST` | `/api/voice/ingest` | 上传音频并路由语音意图 |
| `GET` | `/api/voice_events` | 获取最近语音事件 |
| `GET` | `/api/voice_stream` | SSE 语音事件流 |
| `POST` | `/api/tts_say` | 文本转音频 |

## 数据与模型提交策略

- `YOLO_model/*.pt` 会提交到仓库，便于 clone 后直接运行 ROI 检测。
- `models/`、`tools/piper/models/`、`tools/piper/piper/` 和 `tools/piper/*.zip` 不提交到仓库，用于放置本地 Vosk / Piper 语音模型与运行时。
- `data/*.db` 不提交到仓库，避免把本地业务数据上传到 GitHub。
- `.wake.lock`、`__pycache__`、`_ppt_slide_exports/`、临时文件和大体积运行产物不提交。

## 调试建议

- 如果摄像头画面有框但 OCR 失败，优先调整距离、角度和光线。
- 如果一直没有框，可以降低 `YOLO_OCR_CONF`，例如 `0.15`。
- 第一次打开扫描时 YOLO 模型需要加载，可能会慢一点，后续会走进程内缓存。
- Windows 下如果出现 `torch\lib\shm.dll` 加载错误，本项目已通过延迟导入 PaddleOCR 避免 PaddleOCR 与 Torch 的 DLL 加载顺序冲突；修改相关导入时要保留这一点。

## 测试

基础语法检查：

```bash
python -c "import pathlib; files=['config.py','api/voice.py','services/shelf_service.py','ocr/yolo_roi.py']; [compile(pathlib.Path(f).read_text(encoding='utf-8'), f, 'exec') for f in files]; print('syntax ok')"
node --check static/main.js
```

项目中已有部分 pytest 用例，可按需运行：

```bash
pytest
```

## 已知边界

- 数据库文件默认不随仓库提交，新环境需要准备本地 `data/bookshelf.db` 或执行初始化脚本。
- 部分语音能力依赖本机麦克风、扬声器和本地模型。
- TTS / ASR / PaddleOCR / YOLO 首次启动都可能有模型加载耗时。
- APP 正式上线前仍建议补齐更严格的认证、权限和数据库迁移流程。
