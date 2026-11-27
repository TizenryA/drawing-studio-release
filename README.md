# Drawing Studio (`v1.0.42`)

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](./LICENSE)

**把 Google Colab 按需 GPU、Google Drive 持久化存储与完整 ComfyUI 节点引擎，装进一台轻量 VPS 的个人 AI 影像创作工作台。**

传统的 AI 生图工具往往走入两个极端：要么是开箱即用但能力锁死的云端 SaaS，模型和作品受制于人；要么是直接把原版 ComfyUI 暴露到公网，连线复杂、手机端几乎不可用，且机器关机就失联、大模型和出图堆在昂贵的 GPU 云盘里反复迁移。

**Drawing Studio** 试图用一套极简的自部署控制面解决这个矛盾：**让控制端常驻便宜稳定的 VPS，让资产永远留在你自己的 Google Drive，只在真正需要推理时向 Google Colab 借一块 GPU。** 无论你是在手机浏览器里随手改几个分镜、在平板上做多格漫画排版，还是通过 OpenAI 兼容接口接入外部 Agent 自动化流水线，都能获得一致且可控的创作体验。

---

## 设计理念

### 1. 算力、存储、控制面彻底解耦

- **控制面常驻 VPS（轻量无状态负担）**：FastAPI + SQLite WAL + React/Vite 单容器即可运行。哪怕 Colab GPU 处于关机状态，你依然可以随时打开网页整理图库、建项目文件夹、调试提示词草稿、检查历史参数或排队同步模型元数据——**看图和管理不花一分算力钱**。
- **算力按需借用 Google Colab**：通过独立授权的 Colab 隧道协议申请并接管运行时，实时读取可用 GPU 型号（如 T4 / L4 / A100 / G4 等）、剩余算力（CU）与小时消耗速率。支持临时模式空闲自动关机、持久模式常驻，以及在实例被 Colab 回收或断连时自动冻结运行计时、清空过期显存指标，防止误判和重复申请实例。
- **资产与权重永远归你自己的 Google Drive**：Colab 与 Drive 支持**完全独立的两个 Google 账号**。所有大模型、LoRA、VAE 与最终生成的原图、参数旁车 JSON 全部落盘到你自己的 Google Drive（`DrawingStudio/` 目录）。Colab 临时磁盘仅作推理缓存，**永不作为唯一副本**。

### 2. 不阉割原生 ComfyUI，而是给它穿上顺手的创作外衣

- 节点式工作流很强，但在移动端改一个参数或批量跑几页分镜非常痛苦。Drawing Studio 的做法是：**保留原生 ComfyUI 的 100% 完整度，同时把高频操作抽象成清晰表单。**
- 你可以导入任意 ComfyUI **API Format JSON** 或 **LiteGraph UI Workflow JSON**（服务侧在 GPU 就绪时请求 `/object_info` 自动转为 API 图），把画幅、步数、CFG、Sampler、Shift、提示词映射到简洁面板；需要魔改底层连线时，直接点击「ComfyUI」入口进入受保护的 `/comfy/` 原生界面。
- **提交即快照**：任务进入队列的那一刻，当前参数、模型解析记录与完整 API 计算图就会被冻结写入 SQLite。排队过程中你可以放心切换其他工作流、修改面板参数或继续写下一批提示词，绝不会串扰已提交的任务。

### 3. 面向“从创意到成片”的创作闭环，而非单次抽卡器

实际做一套漫画、分镜或短视频时，单张文生图只是第一步。Drawing Studio 围绕真实创作链路内置了一整套衔接能力：

- **分镜文稿 → 批量出图**：把大模型写好的整篇 Markdown 分镜稿直接贴进解析器，自动拆成多组正负向提示词与参数，微调后一键批量入队；
- **单页海报 / 多格漫画精确构图**：内置 **MOSS 可视化坐标排版编辑器**，直接在画布上拖框摆位置、写台词和画面描述，编译为 OpenMOSS 原生单行 JSON；
- **出图 → 局部修补 / 精修**：不满意某处手部或细节？图库里点一下即可带原图进在线画笔涂抹重绘（`MaskEditor`），或接入 **SeedVR2** 做单图放大修复；
- **静态图 → 连续视频成片**：围绕 **MiniMax H3** 打通多关键帧衔接（路线 A）、视频尾段惯性续写（路线 B）、RIFE 48/60fps 补帧、视频抽首尾帧、多段视频裁重叠帧无痕拼接，以及 **SeedVR2 视频时序精修**；
- **项目化归档**：作品按项目自动归入 Google Drive 对应文件夹，VPS 本地只常驻轻盈的 WebP 缩略图与高清预览，原图用时再按需回源，既不撑爆 VPS 硬盘又能秒开千张级图库。

### 4. 单人私有、极简克制、人机协同

- **零重型中间件**：不依赖 PostgreSQL、Redis、RabbitMQ 或 Celery，单个 Docker 容器 + 一个 `studio.sqlite3` 文件即可稳定驱动全站队列、图库索引与双 Worker 调度；数据迁移只需安全备份 `data/` 与 `config/private/`。
- **最小暴露面**：除 `/api/health`、`/api/auth/status`、`/api/auth/login` 三个端点外，全部 `/api/*`、`/v1/*`、`/comfy/*` 与 WebSocket `/comfy/ws` 均受 `scrypt` 登录会话或可吊销 `ds-...` Bearer Key 保护；Colab 与 VPS 间走已认证 Jupyter Kernel 通道，不把 ComfyUI 裸端口暴露在公网上。
- **人机同一条流水线**：网页操作、`/v1/images/*`（OpenAI 兼容格式，支持 `comfy` 扩展字段）与 `/api/tasks` + SSE `/api/events` 走完全同一套任务队列、模型库和 Drive 图库。你可以自己手点界面，也可以把接口交给外部 Agent 跑自动化批产，两边结果实时互通。

---

## 核心功能详解

### 1. 图像生成与精确构图（绘图工作台）

- **多生态底模与模式支持**：
  - 原生支持 **SDXL**、**MOSS Image（Base BF16 / SFT INT8）**、**Krea2 Turbo + MOSS 双采**、**Anima**、**Illustrious**；支持 `txt2img`、`img2img`、`inpaint`、`hires` 潜空间高清重绘、`upscale`（ESRGAN）以及串联 `SeedVR2` 精修。
  - **1K / 2K / 4K / 8K** 四档分辨率与 7 种常用比例（`1:1`、`2:3`、`3:2`、`3:4`、`4:3`、`16:9`、`9:16`）快捷切换，也支持自由输入宽高；自带高分辨率显存风险提示。
- **MOSS Base 与 SFT 专项工作流适配**：
  - 针对 MOSS Base（色彩厚涂、适合高 Shift/高步数）与 MOSS SFT（肢体结构与多格分镜更稳、适合低 Shift）提供快捷切换开关，自动应用各自推荐的 `shift` / `steps` / `cfg` / `sampler` / `scheduler`，并对异常参数组合实时给出防呆预警（如 Base 下 `shift < 4.3`、单图像素过高预警等）。
- **MOSS 可视化坐标排版编辑器（`MossLayoutModal`）**：
  - 以所见即所得方式拖拽调整 `character`（角色）、`object`（物体/局部特写）与 `text`（文字/标题/气泡）包围盒，自动吸附 `16` 倍数画布，支持单页与多格分镜（`single` / `multi_panel`）。
  - 内置 **6 套开箱即用排版预设**：国潮书法海报、电影主视觉海报、潮流杂志封面、破框爆裂三格、左通栏四格张力、撞色双格封面。
  - 实时编译为 OpenMOSS 规范的中心化 `abs_centralized` / `yxyx` 单行 JSON；当画布分辨率改变时支持检测越界并一键等比缩放重算坐标，同时自动剔除负向提示词里会抑制文字渲染的冲突词（如 `text`、`speech bubbles`）。
- **混排提示词批量解析器（`PromptParserModal`）**：
  - 把带标题、代码块、Markdown 表格、思考备注、NovelAI/WebUI 格式或中英文混杂的多段文稿直接粘贴进来，自动拆分为多条待生图记录，精准提取正负向提示词、尺寸、步数、CFG、采样器/调度器、`shift` 与 LoRA 权重（同时隔离备注文字防污染）。支持批量调整全局或单条参数并一键按序提交。
- **多 LoRA 独立槽位与动态串接**：
  - 导入的工作流中若本身包含多个 LoRA 节点，支持按 `node_id` 独立开关或调整模型/CLIP 权重，互不干扰；额外在面板新选的公开 LoRA 会自动按 `ModelSamplingAuraFlow` / `UNETLoader` / `CheckpointLoaderSimple` 链尾安全串接，无需手改工作流 JSON。
- **涂鸦蒙版编辑器（`MaskEditorModal`）**：
  - 支持直接在参考图上用可调笔刷涂抹局部重绘区域并导出蒙版上传，配合 `grow_mask_by` 与重绘幅度快速修手、修脸或局部换装。

### 2. MiniMax H3 视频全链路与 SeedVR2 AI 精修

- **三条视频创作路线（16 个预置工作流覆盖 BF16 全量与 INT8 轻量、8 步 Turbo 与 20 步高画质）**：
  1. **文生 / 参考图视频（Ref2V）**：支持最多 **9 张**参考图按序输入（`ref_images.ref_image_0..8`），保持多角色或场景外观一致性。
  2. **路线 A · 多关键帧时间轴衔接（`keyframes`）**：上传 1 张做纯首帧 I2V，2 张做首尾帧平滑过渡，3~9 张由后端自动均匀锚定（或按你的手工帧号固定到时间轴），大幅减少镜头突变和快切。
  3. **路线 B · 视频尾段惯性续写（`continue`）**：直接从图库选中上一段视频，自动提取最后 **5 帧或 22 帧**经 `MiniMaxH3AddGuide` 锚定到新片段开头保留运动速度与镜头惯性，并支持额外追加目标关键帧引导后半段走向。
- **硬约束自动对齐与补帧拼接**：
  - 强制校对 H3 分辨率为 **`32` 的正整数倍**、基准帧率 `24fps`，请求秒数或帧数自动向上对齐到 **`5 + 17k`**（上限 3600 帧）并展示实际生成时长。
  - 可选开启 **RIFE 48fps / 60fps** 视频补帧与 **CRF（`0..30`）** 编码画质控制。
  - 图库内置 **首/尾帧提取** 与 **多段视频拼接**：在图库勾选多段视频按顺序合并，可设置重叠裁剪帧数（按 24fps 基准换算，兼容 24/48/60fps 片段），实现路线 B 分段长视频无痕合片。
- **SeedVR2 AI 精修（单图 & 视频）**：
  - 支持图生图/文生图结束后自动衔接 `seedvr2` 单图 AI 精修（放大并修复皮肤/纹理细节）。
  - 提供独立 `video-seedvr2` 视频精修面板，支持短边分辨率（`128..4096` 偶数）、最大边限制、`4k+1` 时序批帧（`1..65`）、批次重叠帧、色彩校正（`lab` / `wavelet` / `hsv` / `adain` 等）与 `blocks_to_swap` 显存卸载控制。

### 3. 三层分级图库与 Google Drive 项目同步

- **三层渐进式媒体架构（快开省流，按需无损）**：
  - **列表卡片层（`/thumbnail`）**：本地常驻 `512×768` WebP 缩略图 + SQLite `summary_json` 二级索引分页，翻阅数千张作品依然毫秒响应。
  - **详情预览层（`/preview`）**：点开大图加载长边上限 `1600px`、质量 `88` 的高清 WebP（支持透明通道；视频直接流式播放）。若本地尚未缓存高清预览，后端会自动从 Drive 拉回转码并缓存在 **512 MB** LRU 空间中。
  - **无损原片层（`/download` / `/media`）**：点击下载或用于二次重绘、视频拼接时，若 VPS 本地已瘦身清理，则即时从 Drive 异步回源并纳入 **256 MB** LRU 缓存；视频流完整支持 HTTP `206 Range` 拖动进度条。
- **归档后自动瘦身**：作品成功上传 Google Drive 并核对完整后，默认开启 `prune_local_after_archive` 自动清理 VPS 上的大体积原图与冗余参数文件，大幅降低 VPS 磁盘压力。
- **Drive 项目文件夹管理（Scheme A）**：
  - 每个项目对应 Google Drive `DrawingStudio/outputs/projects/<项目名>/` 真实文件夹，原图与同名 `<id>.json` 元数据旁车共同存放于此。
  - 支持在网页上新建、重命名、删除项目，单张或批量将作品移入指定项目（Drive 端同步迁移）；如果往 Drive 手动丢入外部图片/视频，点击「同步 Drive」即可在不拉取大原图的情况下快速入库建索引。
  - 图库详情内直接集成 **复用参数 / 局部修复 / 再次生成 / 继续超分 / 视频精修 / 尾段续写 / 抽取首尾帧** 快捷入口，并支持多选打包下载 ZIP（含原图与参数 JSON）。

### 4. Colab GPU 调度、模型按需同步与并发控制

- **GPU 生命周期与安全防护**：
  - 通过原子占位锁防止快速连点或并发请求同时申请两台 GPU；若开机或释放遭遇超时/网络抖动，进入 `allocation_unknown` 待协调状态并保留句柄，避免盲目重开造成多余扣费。
  - 实时统计本次运行时长、剩余 CU 余额与小时烧钱速率，估算或核对本次会话实际 CU 消耗；`v1.0.42` 加强了 Colab 实例被后台回收或 Jupyter 通道失联的自动探测与计数冻结。
- **模型管理与 2 路并发断点续传**：
  - 支持扫描 Drive `DrawingStudio/models/` 下 13 个标准分类目录，在网页端维护模型家族、封面、标签、触发词、推荐权重与默认参数。
  - 推理前根据 ComfyUI `/object_info` 精确核查所需文件是否已在 GPU 就绪，只对缺失权重发起 Drive 直连下载；使用 **2 路并发信号量**、`.part` 断点续传与 MD5/大小校验，支持在界面随时取消排队或下载中的任务（保留 `.part` 供下次续传）。
- **双端口调度与队列恢复**：
  - 支持在设置中将 Colab ComfyUI 并发 Worker 设为 `1` 或 `2`（监听 `8188 / 8189`）：轻型生图任务可在双端口并行执行，而视频生成或显存清理型重任务（如 SeedVR2）自动串行独占执行，避免爆显存。
  - 服务重启后自动恢复队列、接管已派发 ComfyUI 的 `prompt_id` 不重复扣算力；实时记录每个任务的排队、准备/同步模型、推理、上传归档耗时拆解。

---

## 系统架构

```
+-------------------------------+       HTTPS / SSE / WS       +--------------------------------------+
|  移动端 / 桌面 Browser (PWA)  | <--------------------------> |  Drawing Studio 控制面 (FastAPI)     |
|  外部客户端 / Agent (/v1/*)   |   Session Cookie / ds-...    |  - Auth (scrypt + 可吊销 API Key)    |
+-------------------------------+                              |  - SQLite WAL (主表 + 双二级索引)    |
                                                               |  - 持久化任务队列 & 双端口调度       |
                                                               |  - 三层分级图库 & 512M/256M LRU 缓存 |
                                                               +------------------+-------------------+
                                                                                  |
                           +------------------------------------------------------+------------------------+
                           | Colab Tunnel + Authenticated Jupyter Kernel WS                                |
                           v                                                                               v
+----------------------------------------------+                                     +----------------------------------+
|  Google Colab GPU 运行时                     | <---- OAuth Token / Range DL -----> |  Google Drive (独立存储账号)     |
|  - colab/bootstrap.py                        |                                     |  DrawingStudio/                  |
|  - ComfyUI Workers (8188 / 8189)             | <---- 上传无损原片 + 参数 JSON ---- |   ├── models/<13 类模型目录>/    |
|  - Custom Nodes (SeedVR2 / RIFE / H3 等)     |                                     |   ├── workflows/                 |
+----------------------------------------------+                                     |   └── outputs/projects/<项目名>/ |
                                                                                     +----------------------------------+
```

---

## 快速部署

### 1. 准备 Colab 与 Google Drive OAuth 凭据

将已有 Colab CLI 和 Google Drive OAuth 授权的私有凭据放入项目的 `config/private/` 目录（Colab 与 Drive 可以使用两个不同的 Google 账号）：

- `config/private/colab.json`
- `config/private/drive.json`

凭据 JSON 需包含 `client_id`、`refresh_token` 及可选的 `client_secret`、`access_token`、`expires_at`。运行时若 token 过期会自动刷新并原子写回原文件，因此容器挂载的 `./config` 目录必须可写。

```bash
mkdir -p data config/private
chmod 700 config/private
chmod 600 config/private/colab.json config/private/drive.json
```

### 2. 启动容器服务

```bash
docker compose up -d --build
```

默认监听宿主 `127.0.0.1:8790`（详见 [compose.yaml](./compose.yaml)）。首次启动若未设置环境变量 `DRAWING_PASSWORD`，系统会随机生成管理员密码并写入 `data/admin-password`（权限 `0600`），登录后可在右上角 **设置 → 管理员密码** 修改。

### 3. 配置 HTTPS 反向代理

建议置于 HTTPS 反向代理后使用，需注意保留 WebSocket 头并关闭 SSE 响应缓冲，以保证 `/api/events` 实时进度推送和 `/comfy/ws` 原生节点通信正常工作。Caddy 示例：

```caddyfile
drawing.example.com {
    reverse_proxy 127.0.0.1:8790 {
        flush_interval -1
    }
}
```

### 4. 一键导入预置公开工作流

登录界面后，在 **设置 → API 密钥** 中创建一个 `ds-...` 密钥，运行 [ops/import_workflows.py](./ops/import_workflows.py) 即可将仓库自带的 30 个公开工作流写入服务端 `data/workflows/`：

```bash
export DRAW_BASE="http://127.0.0.1:8790"
export DRAW_API_KEY="ds-your-api-key"

# 预览即将导入的 30 个公开工作流
python3 ops/import_workflows.py --dry-run

# 批量导入
python3 ops/import_workflows.py
```

---

## 预置工作流与公开模型来源

本版本内置 **8 个代码原生工作流**，并在 [workflows/](./workflows/) 目录提供 **30 个开箱即用的公开模型工作流**（已完全剔除个人私有 LoRA，均可直接从 HuggingFace / Civitai 获取对应权重）。

### 工作流清单

| 类别 | 数量 | 工作流 ID | 说明 |
| :--- | :---: | :--- | :--- |
| **代码内置基础工作流** | 8 | `sdxl-txt2img` / `sdxl-img2img` / `sdxl-inpaint` / `sdxl-hires` / `esrgan` / `seedvr2` / `video-seedvr2` / `supir` | 自动识别 SDXL Checkpoint 与 MOSS UNET 分支；支持 ESRGAN 与 SeedVR2 单图/视频精修 |
| **MOSS Image & 双采** | 3 | `moss-base` / `moss-sft` / `krea2-moss-dual` | 纯净 MOSS Base (BF16)、MOSS SFT (INT8)、Krea2 Turbo + MOSS Base 双采，可在面板自由叠加公开 LoRA |
| **MOSS Civitai 风格** | 6 | `moss-civitai-3415950` / `3426609` / `3430983` / `3433283` / `3436193` / `3441491` | Oblivion CGI、3D Character Render、2.5D/3D、Blender 3D V2 等公开 MOSS 风格工作流 |
| **Anima / Illustrious 组合** | 5 | `pack-anima-seedvr2` / `pack-illustrious-seedvr2` / `hsr-style-1852115` / `3066017` / `3069004` | Anima / Illustrious 出图及自动串联 SeedVR2 精修（含 Civitai 公开画风工作流） |
| **MiniMax H3 视频全套** | 16 | `h3-fl2va-keyframes-*` (4) / `h3-fl2va-continue-*` (4) / `h3-studio-*-mystic-v5*` (6) / `h3-full-mystic-v5-*` (2) | 覆盖路线 A 多关键帧、路线 B 尾段续写、参考图与文生视频；均提供 BF16 全量与 INT8 轻量、8 步 Turbo 与 20 步高画质 |

### Google Drive 模型目录规划（`DrawingStudio/models/`）

| Drive 子目录 | 对应核心权重示例 | 公开来源 |
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

## API 使用示例

### OpenAI SDK 同步生图（`/v1/images/generations`）

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

### 异步任务提交（推荐用于视频、批量生图与长任务）

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

---

## 开发、测试与交接文档

详细的模块导航、全量 API 路由表、SQLite 索引结构、参数硬约束、脱敏记录与备份回滚操作手册，请阅读完整交接文档：[HANDOVER.md](./HANDOVER.md)。

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

---

## 开源协议

本项目基于 **[GNU Affero General Public License v3.0 (AGPL-3.0)](./LICENSE)** 开源发布。

- 你可以自由使用、研究、修改并分发本项目的源码，但任何基于本项目的衍生作品或修改版本在分发时须继续采用 **AGPL-3.0** 协议开源。
- 若你修改本项目并将其作为网络服务（Web / SaaS / API）部署供他人通过网络交互使用，也须向用户提供修改后的完整对应源代码。
- 完整协议条款请参阅仓库根目录的 [LICENSE](./LICENSE) 文件。
