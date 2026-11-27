# Drawing Studio (`v1.0.42`)

移动端优先的私人 **Colab GPU + ComfyUI** 图像与视频生成工作台，独立部署于自有 VPS（Docker + FastAPI + SQLite + React/Vite），计算通过已授权 Google Colab 算力按需执行，模型与生成作品独立归档至 Google Drive，并提供原生 ComfyUI 完整透传与 OpenAI 兼容图像 API（`/v1/images/*`）。

---

## 核心特性

- **Colab GPU 按需生命周期管理与 CU 计费监控**
  - 自动探测账号实际可用 GPU（如 T4 / L4 / A100 / G4 等）与剩余算力（CU）及实时消耗速率，支持临时模式（空闲自动关机）与持久模式。
  - 断连与实例回收保护（`v1.0.42`）：支持瞬断容错、自动检测 Colab 运行时回收/释出、冻结累计时长与清空过期显存数据，避免漏算或误触发重复开机。
- **Google Drive 独立存储与模型按需同步**
  - Colab 与 Drive 支持独立 OAuth 账号；模型按目录（`checkpoints` / `loras` / `vae` / `diffusion_models` / `text_encoders` / `seedvr2` / `vfi_models` 等）扫描建档。
  - 生成任务仅按需将缺失模型从 Drive 分块断点（`.part` + MD5 校验）同步到 Colab 本地盘，支持 2 路并发批量同步与随时取消。
- **双端口调度与原生 ComfyUI 透传**
  - 支持 ComfyUI `8188 / 8189` 双 Worker：同型轻量生图任务可并发执行，视频或需要释放显存的重任务自动串行互斥。
  - 原生 ComfyUI UI 与 WebSocket 经 `/comfy/` 与 `/comfy/ws` 鉴权反向代理完整可用。
- **MOSS Image（Base & SFT）与可视化坐标排版**
  - 内置 `MOSS Image Base (BF16)` 与 `MOSS Image SFT (INT8)` 参数防呆校验与一键切换，支持 `ModelSamplingAuraFlow` `shift` 参数与动态多 LoRA 串接。
  - 提供可视化 **MOSS 坐标排版编辑器**（`MossLayoutModal`），支持画布对齐（16 倍数）、`abs_centralized` / `yxyx` 单行 JSON 编译、6 套排版预设及跨分辨率自动等比缩放。
- **MiniMax H3 视频全链路（参考图 / 多关键帧 / 尾段续写 / 补帧 / 拼接）**
  - 支持 **文生/参考图视频（Ref2V）**、**路线 A 多关键帧时间轴衔接（最多 9 帧）**、**路线 B 视频尾段 5/22 帧惯性续写**。
  - 自动执行 H3 硬约束校验（分辨率 `32` 倍数、24fps、帧数向上对齐 `5 + 17k`），可选 RIFE `48fps / 60fps` 补帧与自定义 CRF（`0..30`）。
  - 图库支持视频首/尾帧无损提取与多段视频无缝裁剪拼接。
- **SeedVR2 AI 精修（单图 & 视频）**
  - 支持生图后自动串联 SeedVR2 超分精修，以及对已有图库视频执行独立 `video-seedvr2` 时序精修（校验 `4k+1` 批帧与重叠帧数）。
- **批量提示词智能解析**
  - 绘图页内置混排文档解析器，支持从 Markdown / 多段草稿中自动提取多组正向/负向提示词、尺寸、步数、CFG、采样器与 LoRA 权重并一键批量提交。
- **三层渐进式图库 & Drive 项目文件夹同步**
  - 列表加载轻量 `summary_json` 与 `512×768` WebP 缩略图（`/thumbnail`），详情加载 `1600px` q=88 高清 WebP 预览（`/preview`，512 MB LRU 缓存），原图按需回源（`/download`，256 MB LRU 缓存）。
  - 支持项目文件夹（`DrawingStudio/outputs/projects/<项目名>/`）创建、重命名、跨项目移动、外部入库同步（`POST /api/gallery/sync`）与归档后自动瘦身本地原图。
- **OpenAI 兼容接口 & 实时事件流**
  - 提供 `/v1/models`、`/v1/images/generations`、`/v1/images/edits`（支持 `comfy` 扩展字段透传），以及 `GET /api/events` SSE 实时推送任务、模型与运行时状态。

---

## 系统架构

```
+---------------------------+        HTTPS / SSE / WS         +------------------------------------+
|  Browser PWA (React/Vite) | <-----------------------------> |  Drawing Studio Server (FastAPI)   |
|  External Client (/v1/*)  |   Cookie / Bearer (ds-...)      |  - Auth (scrypt + API Keys)        |
+---------------------------+                                 |  - SQLite WAL (records + indexes)  |
                                                              |  - Task Queue & Model Sync Control |
                                                              |  - 3-Tier Gallery & LRU Cache      |
                                                              +-----------------+------------------+
                                                                                |
                       +--------------------------------------------------------+-------------------------+
                       | Colab Tunnel + Authenticated Jupyter Kernel WS                                   |
                       v                                                                                  v
+----------------------------------------------+                                     +----------------------------------+
|  Google Colab GPU Runtime                    | <---- OAuth Token / Range DL -----> |  Google Drive (Independent)      |
|  - colab/bootstrap.py                        |                                     |  DrawingStudio/                  |
|  - ComfyUI Workers (8188 / 8189)             | <---- Archive Originals & JSON ---- |   ├── models/<kind>/             |
|  - Custom Nodes (SeedVR2 / RIFE / H3)        |                                     |   ├── workflows/                 |
+----------------------------------------------+                                     |   └── outputs/projects/<name>/   |
                                                                                     +----------------------------------+
```

---

## 快速部署

### 1. 准备 OAuth 凭据

将已授权的 Colab CLI 与 Google Drive OAuth 私有凭据放到项目目录的 `config/private/` 下（推荐目录权限 `0700`、文件权限 `0600`）：

- `config/private/colab.json`
- `config/private/drive.json`

两个账号可以不同，凭据 JSON 结构需包含：

```json
{
  "client_id": "YOUR_OAUTH_CLIENT_ID",
  "client_secret": "YOUR_OAUTH_CLIENT_SECRET",
  "refresh_token": "YOUR_REFRESH_TOKEN"
}
```

> 服务运行时会自动刷新 `access_token` 并原子写回原文件，因此容器挂载的 `./config` 必须可写。

### 2. 启动容器

```bash
mkdir -p data config/private
chmod 700 config/private
chmod 600 config/private/colab.json config/private/drive.json

docker compose up -d --build
```

默认监听 `127.0.0.1:8790`（见 [compose.yaml](./compose.yaml)）。首次启动若未通过环境变量 `DRAWING_PASSWORD` 指定初始密码，会自动生成随机管理员密码并写入 `data/admin-password`（权限 `0600`）。

### 3. 反向代理（Caddy / Nginx）

建议放置在 HTTPS 反向代理后访问，并确保开启 WebSocket 透传且禁用 SSE 响应缓冲（以保证 `/api/events` 与 `/comfy/ws` 正常工作）：

```caddyfile
drawing.example.com {
    reverse_proxy 127.0.0.1:8790 {
        flush_interval -1
    }
}
```

### 4. 一键导入公开工作流

登录前端右上角 **设置 → API 密钥** 创建 `ds-...` Bearer Key，然后运行批量导入脚本（详见 [ops/import_workflows.py](./ops/import_workflows.py)）：

```bash
export DRAW_BASE="http://127.0.0.1:8790"
export DRAW_API_KEY="ds-your-api-key"

# 预览将导入的 30 个公开工作流
python3 ops/import_workflows.py --dry-run

# 批量写入服务端 data/workflows/
python3 ops/import_workflows.py
```

---

## 预置工作流与公开模型清单

本公开版包含 **8 个代码内置工作流** 与 **30 个预置 JSON 工作流**（位于 [workflows/](./workflows/)）。所有工作流均已剔除个人私有 LoRA，仅依赖可公开获取的模型文件。

### 工作流分类

| 类别 | 数量 | 代表 ID | 说明 |
| :--- | :---: | :--- | :--- |
| **代码内置基础工作流** | 8 | `sdxl-txt2img` / `sdxl-img2img` / `sdxl-inpaint` / `sdxl-hires` / `esrgan` / `seedvr2` / `video-seedvr2` / `supir` | SDXL / MOSS 自动分支识别、ESRGAN、SeedVR2 单图/视频精修 |
| **MOSS Image & 双采** | 3 | `moss-base` / `moss-sft` / `krea2-moss-dual` | MOSS Base BF16、MOSS SFT INT8、Krea2 Turbo + MOSS 双采 |
| **MOSS Civitai 风格** | 6 | `moss-civitai-3415950` ~ `3441491` | CGI、3D 角色渲染、2.5D/3D、Blender 3D V2、角色风格 |
| **Anima / Illustrious 组合** | 5 | `pack-anima-seedvr2` / `pack-illustrious-seedvr2` / `hsr-style-1852115` / `3066017` / `3069004` | Anima / Illustrious 底模出图与 SeedVR2 自动精修 |
| **MiniMax H3 视频** | 16 | `h3-fl2va-keyframes-*` / `h3-fl2va-continue-*` / `h3-studio-*-mystic-v5*` / `h3-full-mystic-v5-*` | 路线 A 多关键帧、路线 B 尾段续写、参考图与文生视频（含 BF16 / INT8 与 8 步 Turbo / 20 步高画质） |

### 公开模型存放目录（Google Drive `DrawingStudio/models/`）

| Drive 子目录 | 核心模型文件 | 公开来源 |
| :--- | :--- | :--- |
| `diffusion_models/` | `moss_image_base_bf16.safetensors` / `moss_image_sft_int8_convrot.safetensors` | OpenMOSS / Comfy-Org |
| `diffusion_models/` | `krea2_turbo_fp8.safetensors` / `animayume_v05.safetensors` | HuggingFace |
| `diffusion_models/` | `minimax_h3_fl2va_bf16.safetensors` / `minimax_h3_fl2va_pruned_int8_convrot.safetensors`<br>`minimax_h3_ref2va_bf16.safetensors` / `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | `Comfy-Org/MiniMax-H3` |
| `checkpoints/` | `wai_illustrious_sdxl_v17.safetensors` / `sd_xl_base_1.0.safetensors` | Civitai / HuggingFace |
| `text_encoders/` | `qwen3.5_9b_int8_convrot.safetensors` / `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` / `qwen3vl_4b_fp8_scaled.safetensors` / `qwen_3_06b_base.safetensors` | HuggingFace |
| `vae/` | `flux2-vae.safetensors` / `qwen_image_vae.safetensors` / `minimax_h3_audio_vae_fp32.safetensors` / `minimax_h3_video_vae_fp16.safetensors` / `minimax_h3_video_vae_dully_seamless_v1_int8convrot.safetensors` | HuggingFace |
| `loras/` | `minimax_h3_fl2v_turbo_8step_v1.2_768p_comfyui_bf16.safetensors` / `minimax_h3_ref2v_turbo_8step_v1.1_768p_comfyui_bf16.safetensors` | `lightx2v/Minimax-h3-Turbo` |
| `loras/` | `MysticXXX_MMH3-V5-ref2va.safetensors`、Civitai MOSS/Anima/Illustrious 系列 LoRA | Civitai（见各工作流 `import_notes`） |
| `seedvr2/` | `seedvr2_ema_3b_fp8_e4m3fn.safetensors` / `ema_vae_fp16.safetensors` | SeedVR2 官方权重 |
| `latent_upscale_models/` | `minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors` | HuggingFace |
| `vfi_models/` | `rife49.pth` | `Fannovel16/ComfyUI-Frame-Interpolation` |

---

## API 调用示例

### OpenAI SDK 同步生图

```python
from openai import OpenAI

client = OpenAI(
    api_key="ds-your-api-key",
    base_url="https://your-domain.example/v1",
)

result = client.images.generate(
    model="moss_image_sft_int8_convrot.safetensors",
    prompt="1girl, ink wash anime editorial illustration, lantern courtyard",
    size="832x1248",
    extra_body={
        "project": "角色设定",
        "comfy": {
            "workflow_id": "moss-sft",
            "steps": 28,
            "cfg": 3.5,
            "shift": 3.5,
            "sampler": "er_sde",
            "scheduler": "simple",
        },
    },
)
```

### 异步任务提交（推荐用于视频 / 长任务）

```bash
export DRAW_BASE="https://your-domain.example"
export DRAW_API_KEY="ds-your-api-key"

curl -sS -X POST "$DRAW_BASE/api/tasks" \
  -H "Authorization: Bearer $DRAW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "moss-base",
    "project": "海报合集",
    "prompt": "1girl, dramatic chiaroscuro rim light, painterly illustration",
    "width": 896,
    "height": 1344,
    "steps": 36,
    "cfg": 3.2,
    "shift": 5.0
  }'
```

更多接口字段、参数校验与交接细节请参阅 [HANDOVER.md](./HANDOVER.md)。

---

## 本地开发与测试

```bash
# 前端单元测试（Node 22+ 原生 test runner）
node --test web/src/*.test.js

# 前端依赖安装与构建
cd web && npm ci && npm run build

# 后端单元测试（需 Python 3.12 + requirements.txt + Pillow/httpx）
python3 -m unittest discover -s backend -p "test_*.py"
```

---

## 友情链接

- **[LINUX DO](https://linux.do/)** — 真诚、友善、团结、专业的新理想型技术社区
- **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** — 模块化扩散模型节点推理引擎
- **[OpenMOSS](https://github.com/OpenMOSS)** — MOSS Image 开源图像生成生态
