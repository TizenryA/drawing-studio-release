# Drawing Studio (`v1.0.42`) 源码与架构交接文档

本文档面向接手维护、二次开发或独立部署 **Drawing Studio** 的工程师，基于 `v1.0.42` 干净公开版源码编写，所有路由、字段、模块职责、工作流清单与约束条件均与当前源码逐一对应。

---

## 1. 版本基线与演进历史

- **源码基线版本**：`1.0.42`（`backend/main.py` 中的 `FastAPI(..., version='1.0.42')`、`GET /api/health` 返回 `{"ok": true, "version": "1.0.42"}`、`web/package.json` 版本号 `1.0.42`）。
- **`1.0.41` → `1.0.42` 核心变更**：
  - 增强 Colab 运行时断连与算力释放判定（`backend/runtime.py` 增加 `_mark_disconnected()`、冻结 `disconnected_at` 时刻的 `uptime` 计算、清空过期 `memory_used`/`memory_total` 并避免轮询时产生重复断连日志）。
  - 抽出独立前端状态机辅助模块 `web/src/machineState.js` 与测试 `web/src/machineState.test.js`，在 `web/src/pages/Machine.jsx` 中清晰区分「运行时已断开 / 停止计费」与「运行中 / 启动中 / 待重置」状态。
- **`1.0.27` → `1.0.42` 演进里程碑**：
  - `1.0.30 - 1.0.31`：并发模型同步控制（全局 `asyncio.Semaphore(2)` 并发与取消保留 `.part`）、Colab 配额（CU 余额、烧钱速率、本次实际/预估消耗）、任务按媒体类型拆分耗时指标（排队/准备/推理/上传）。
  - `1.0.32 - 1.0.36`：Krea2 Turbo + MOSS Base 双采工作流、MOSS Image Base/SFT 分支与参数防呆、SeedVR2 视频超分参数校验、MOSS 可视化坐标排版编辑器（`MossLayoutModal`）、混合提示词批量解析器（`promptParser.js`）。
  - `1.0.37 - 1.0.39`：图库 SQLite 二级索引与轻量 DTO 分页（`gallery_index`）、Google Drive 项目文件夹管理（Scheme A）、归档后自动瘦身本地原图（`prune_local_after_archive`）、三层分级取图（`512×768` 缩略图 / `1600px` q=88 高清 WebP 预览 + 512 MB LRU / 原图回源 + 256 MB 异步 LRU）。
  - `1.0.40 - 1.0.41`：任务列表 SQLite 二级索引分页（`tasks_index`）、SSE 实时任务更新、Comfy `/prompt` 状态合并轮询与双端口并发防撞调度。
  - `1.0.42`：Colab 运行时回收断连状态识别、计费冻结与前端 `Machine` 视图状态机抽离。

---

## 2. 完整功能清单（按页面与子系统）

### 2.1 绘图工作台（`web/src/pages/Draw.jsx`）

1. **三场景工作模式切换**：
   - **图像生成（`image`）**：支持内置 SDXL / MOSS 自动构图，以及已导入的 MOSS Base / MOSS SFT / Krea2+MOSS 双采 / Anima / Illustrious 等 API 工作流；支持 `txt2img`、`img2img`、`inpaint`（含独立 `MaskEditorModal` 涂鸦蒙版绘制）、`hires` 潜空间重绘与串联精修（`upscale.mode`: `off` / `standard` / `latent` / `ai`）。
   - **视频生成（`video`）**：支持 MiniMax H3 的 3 条路线快捷切换（`keyframes` 路线 A 多关键帧 / `continue` 路线 B 尾段续写 / `ref` 参考图或文生视频）与精度切换（`BF16` vs `INT8`、8 步 Turbo vs 20 步高画质）。
   - **AI 精修（`refine`）**：快捷进入 `video-seedvr2` 视频 AI 精修或 `seedvr2` 单图 AI 精修。
2. **MOSS Base vs SFT 专属适配**：
   - 在 `moss-base` 与 `moss-sft` 配对工作流间提供一键切换芯片（`switchMossVariant`），自动重置对应的 `shift` / `steps` / `cfg` / `sampler` / `scheduler` / 推荐分辨率，同时保留用户当前提示词、所选项目与额外挂载 LoRA。
   - 参数防呆提示（`getMossParamWarnings`）：在 MOSS Base 下若 `shift < 4.3`、`steps < 30`、`cfg < 2.9` 或像素积 `> 2,200,000` 发出预警；在 MOSS SFT 下若 `shift > 4.3` 或 `< 2.5` 发出预警。
3. **MOSS 可视化坐标排版编辑器（`web/src/components/MossLayoutModal.jsx` + `web/src/mossLayout.js`）**：
   - 将多区域视觉构图（`character` / `object` / `text`）编译为 OpenMOSS 规范的单行 JSON Layout 字符串（`bbox_coord_space: "abs_centralized"`, `bbox_format: "yxyx"`）。
   - 支持画布对齐 16 倍数（`snap16`）、6 套单页/多格漫画内置排版预设、越界检测与跨分辨率等比缩放（`rescaleCompiledMossLayout`）、在负向提示词中自动剥离与渲染文字冲突的关键词（如 `text`、`speech bubbles`）。
4. **混合提示词批量解析器（`web/src/components/Pickers.jsx` `PromptParserModal` + `web/src/promptParser.js`）**：
   - 支持从长篇 Markdown 笔记或大模型输出中自动拆分多个分镜/关键帧代码块，提取正向提示词、负向提示词、尺寸、步数、CFG、Sampler/Scheduler、`shift` 与 LoRA 权重，支持逐条微调并批量提交队列。
5. **H3 视频关键帧网格与参数控制（`web/src/components/KeyframeGrid.jsx` + `web/src/h3Video.js`）**：
   - 支持最多 9 张参考图/关键帧拖拽或按键排序、可选手动帧号指定、路线 B 续写重叠帧选择（`5` 帧 / `22` 帧）、补帧选项（`off` / `48` / `60` fps）与 `video_crf`（`0..30`）。
6. **预设管理**：保存、加载和删除常用参数组合（`/api/presets`），提交后保留项目/LoRA 等上下文或从图库作品一键「复用参数」「局部重绘」「尾段续写」。

### 2.2 任务队列与实时监控（`web/src/pages/Tasks.jsx` + `backend/tasks.py`）

1. **持久化队列与恢复**：
   - 任务记录在 SQLite `records`（`kind='tasks'`）及 `tasks_index` 中。服务重启时，`Tasks.start()` 自动恢复 `queued` 任务、接管已下发但未完成的 `prompt_id` 避免重复生图，并将提交前中断的任务标记为可重试失败态。
2. **双端口并发调度（`8188 / 8189`）**：
   - 当 Colab 开启 `comfy_workers=2` 时，普通轻量出图任务可在双端口并行执行；任何 `media_kind == "video"` 或 `free_gpu_before_run == True`（如 SeedVR2 精修）的重任务会强制独占互斥执行。
3. **SSE 实时进度与计时统计**：
   - 通过 `/api/events` 推送采样步数（`sampling_step` / `total_steps`）、模型同步进度、分段耗时（`queue_seconds` / `prep_seconds` / `infer_seconds` / `upload_seconds`）与当次估算 CU 消耗，支持随时取消排队或运行中任务（向 ComfyUI 发 `/interrupt`）。
4. **分页与筛选**：支持按状态（`active` / `completed` / `failed` / `cancelled`）、媒体类型（`image` / `video`）与关键字分页检索（`tasks_index`）。

### 2.3 模型与工作流管理（`web/src/pages/Models.jsx` + `backend/workflows.py`）

1. **Drive 模型扫描与元信息编辑**：
   - `POST /api/models/scan` 扫描 Drive `DrawingStudio/models/<kind>/` 目录（支持 13 种标准目录：`checkpoints`、`loras`、`vae`、`embeddings`、`controlnet`、`upscale_models`、`diffusion_models`、`text_encoders`、`clip`、`clip_vision`、`seedvr2`、`latent_upscale_models`、`vfi_models`），保留本地收藏、标签、家族（`family`）、触发词与备注。
2. **模型按需同步与断点续传**：
   - 同步通过 Colab Jupyter 控制通道在 GPU 内核侧直接向 Google Drive 下载，采用 `asyncio.Semaphore(2)` 限制并发数；写入 `.part` 临时文件并校验 MD5/字节数，取消时保留 `.part` 以备续传。
   - 在提交任务时，后端读取 ComfyUI `/object_info` 检查所有加载器声明的模型文件是否已在 GPU 就绪，仅同步缺失模型。
3. **自定义工作流导入与自动映射**：
   - 支持上传 ComfyUI **API Format JSON** 或 **LiteGraph UI Workflow JSON**（有就绪 GPU 时自动调用 `/object_info` 转换为 API 图）。
   - 支持动态 LoRA 槽位匹配（优先按 `node_id` 精确匹配，其次按文件名匹配；未命中槽位且已启用的 LoRA 自动串接追加到模型链尾部）。

### 2.4 图库、三层媒体与 Drive 项目文件夹（`web/src/pages/Gallery.jsx` + `backend/gallery.py`）

1. **Google Drive Scheme A 目录结构**：
   - `DrawingStudio/outputs/projects/<项目名>/`：存放按项目归档的原始媒体文件与同名 `<id>.json` 元数据旁车；
   - `DrawingStudio/outputs/originals/` 与 `upscaled/`：存放未分类作品及旁车。
   - 支持新建项目、重命名项目、删除项目（可选先迁移为未分类）、跨项目移动单张或批量作品，同步维护 Drive 远端文件夹。
   - 支持 `POST /api/gallery/sync` 扫描 Drive 文件夹，在不下载大体积原图的前提下补充或纠偏本地数据库索引。
2. **三层媒体访问与缓存策略**：
   - `/api/gallery/{id}/thumbnail`：`512×768` WebP（质量 85），本地常驻；
   - `/api/gallery/{id}/preview`：长边上限 `1600px` WebP（质量 88，保留 RGBA 透明通道；视频回落原视频流），缺失时从 Drive 自动回源生成并纳入 `512 MB`（`PREVIEW_CACHE_MAX_BYTES`）LRU 缓存；
   - `/api/gallery/{id}/download`：带 `Content-Disposition` 附件下载原始 `.png` / `.mp4` / `.webm`，缺失时回源并纳入 `256 MB`（`LOCAL_CACHE_MAX_BYTES`）异步 LRU 缓存；
   - `/api/gallery/{id}/media`：内联媒体流，支持 HTTP Range 分片播放视频；
   - 所有 4 个媒体端点统一返回 `Cache-Control: private, max-age=31536000, immutable`。
3. **本地自动瘦身**：`prune_local_after_archive` 默认开启（`true`），在 Drive 归档上传校验通过后清理本地大体积原图和本地 `.json` 旁车（保留缩略图与预览），也可随时通过 `POST /api/gallery/prune-local` 手动触发清理。
4. **批处理与视频后处理**：
   - 批量操作（`POST /api/gallery/batch`）：打包下载 ZIP（含原图/原视频与 `.json` 旁车）、批量改项目、批量打标、批量收藏、批量删除（同步移入 Drive 回收站）。
   - 抽取关键帧（`POST /api/gallery/{id}/extract-frame`）：抽取视频首帧或尾帧为 PNG 存入 `/api/uploads/` 供图生图/视频续写直接引用。
   - 多段视频拼接（`POST /api/gallery/concat`）：通过 `ffmpeg` 按序合并多个图库视频，支持 `trim_overlap_frames`（`0..240`，以 24fps 为时间基准自动折算重叠裁剪）和 `video_crf` 控制，自动继承首段视频的项目归属。

### 2.5 机器控制台与设置（`web/src/pages/Machine.jsx` + `web/src/pages/Settings.jsx`）

1. **Colab 生命周期状态机**：
   - 状态枚举：`off` → `allocating` → `bootstrapping` → `syncing` → `starting` → `starting_comfy` → `ready`，异常/结束态包括 `stopping`、`disconnected`、`allocation_unknown`、`error`。
   - `start()` 前通过 `reserve_start()` 原子占位防止并发重复申请 GPU；若开机或释放遇到模棱两可的网络异常，切入 `allocation_unknown` 并保留句柄等待人工核对/重置，不盲目重开第二个实例。
2. **安全设置与独立 OAuth2 授权管理（`backend/oauth_flow.py` + `web/src/components/OAuthPanel.jsx`）**：
   - 管理员密码使用随机 16 字节盐 + `hashlib.scrypt(n=16384, r=8, p=1)` 存储；登录颁发 30 天有效 `drawing_session` HttpOnly Strict Cookie。
   - 内置独立的 Google Colab 与 Google Drive 授权向导，支持三种绑定方式：**PKCE 手动复制授权码/回调 URL**、**同源 HTTPS 弹窗回调**（`/api/oauth/{purpose}/callback`，由一次性高熵 `state` 令牌防护 CSRF）、**直接导入已有 JSON 凭据并联网验活**，以及 **测试连通性** 与 **解绑清理**。
   - 凭据通过 `private_json` 严格以 `0600` 权限落盘至 `colab.json` / `drive.json`；`GET /api/settings` 与 `/api/oauth/*` 自动脱敏所有 `*token*`、`*secret*`、`*credential*`、`password_hash` 字段，仅返回掩码后 `client_id_preview` 与 `account_email`。
   - 支持创建/查看前缀/随时吊销 `ds-...` 格式 API Key；`/v1/*` 请求记录耗时、状态码和 Key ID（`GET /api/requests`）。

---

## 3. 代码结构导航

| 路径 | 职责说明 |
| :--- | :--- |
| [backend/main.py](./backend/main.py) | FastAPI 应用入口、生命周期管理（`lifespan`）、中间件鉴权、全部 `/api/*` 与 `/v1/*` 路由、`/comfy/*` HTTP/WS 代理、SSE `/api/events`、空闲自动关机看门狗 `idle_watchdog` |
| [backend/auth.py](./backend/auth.py) | `Auth`：scrypt 密码校验、30 天 Session Cookie、可吊销 `ds-...` API Key |
| [backend/oauth_flow.py](./backend/oauth_flow.py) | `OAuthFlowManager`：Google Colab / Drive 独立 OAuth2 PKCE、同源回调、凭据导入验活与连通性测试 |
| [backend/database.py](./backend/database.py) | `Database`：SQLite WAL 封装、主表 `records(kind, id, payload)`、二级索引 `gallery_index` 与 `tasks_index`、轻量摘要构造 |
| [backend/runtime.py](./backend/runtime.py) | `OAuth` 与 `Runtime`：Colab OAuth 自动刷新、隧道交互（`/tun/m/*`）、Jupyter Kernel WebSocket 命令执行、断连检测、CU 计费与显存探测 |
| [backend/comfy.py](./backend/comfy.py) | `Comfy`：在 Colab 内核侧执行 ComfyUI HTTP/WS 代理请求、多端口健康探测、Drive 模型分块断点下载到 Colab 磁盘 |
| [backend/drive.py](./backend/drive.py) | `Drive`：Google Drive API v3 客户端、文件夹缓存与项目目录管理、分块上传与下载校验 |
| [backend/workflows.py](./backend/workflows.py) | `WorkflowStore`：8 个内置工作流生成（`_builtin`）、UI→API 图转换（`ui_to_api`）、参数映射、H3 控制与 `17k+5` 帧对齐、SeedVR2 参数校验、LoRA 槽位精确更新与尾链动态追加 |
| [backend/tasks.py](./backend/tasks.py) | `Tasks`：持久化任务队列、双端口并发与重任务互斥调度、输入素材上传、ComfyUI 轮询与结果收集归档 |
| [backend/gallery.py](./backend/gallery.py) | `Gallery`：图库归档、WebP 缩略图与高清预览生成、Drive 同步与项目迁移、256MB/512MB LRU 缓存修剪、`ffmpeg` 抽帧与视频拼接 |
| [colab/bootstrap.py](./colab/bootstrap.py) | 注入 Colab GPU 执行的启动脚本：克隆/更新 ComfyUI、安装白名单 GitHub 自定义节点、链接 RIFE 权重、按指定 `workers`（1 或 2）启动 ComfyUI 进程 |
| [web/src/App.jsx](./web/src/App.jsx) | 应用壳层、登录态、导航（绘图/任务/模型/图库 + 机器/设置弹窗）、SSE 事件总线分发与草稿持久化 |
| [web/src/pages/Draw.jsx](./web/src/pages/Draw.jsx) | 绘图/视频/精修主表单、MOSS Base/SFT 切换、H3 路线切换、参数预警与批量提交 |
| [web/src/pages/Tasks.jsx](./web/src/pages/Tasks.jsx) | 任务列表、实时状态、任务详情快照、耗时/CU 拆解、取消与一键复用 |
| [web/src/pages/Models.jsx](./web/src/pages/Models.jsx) | 模型检索、家族/状态筛选、GPU 同步/批量同步/取消/清缓存、工作流管理与编辑器 |
| [web/src/pages/Gallery.jsx](./web/src/pages/Gallery.jsx) | 项目文件夹侧栏/筛选、分页图库、详情弹窗（缩略图→高清预览→全屏原图）、批量操作、导入与视频拼接弹窗 |
| [web/src/pages/Machine.jsx](./web/src/pages/Machine.jsx) | GPU 选择与开机/关机/重启/重置控制、CU 配额与显存进度、未授权快捷跳转与运行时日志 |
| [web/src/pages/Settings.jsx](./web/src/pages/Settings.jsx) | 主题切换、内嵌 Colab/Drive OAuth 授权面板、Drive 根目录与自动瘦身、并发数、API Key 与密码修改 |
| [web/src/components/OAuthPanel.jsx](./web/src/components/OAuthPanel.jsx) | Google Colab & Drive 独立授权向导面板（PKCE 手动授权 / Web 回调 / JSON 导入 / 连通性测试 / 解绑） |
| [web/src/oauthHelpers.js](./web/src/oauthHelpers.js) | GCP `client_secret*.json` 与已有凭据 JSON 解析校验辅助 |
| [web/src/machineState.js](./web/src/machineState.js) | 运行时状态判定与前端展示语义映射（`v1.0.42` 新增） |
| [web/src/mossLayout.js](./web/src/mossLayout.js) | MOSS 坐标排版预设、16 倍数吸附、编译/反编译、缩放与冲突负向词清理 |
| [web/src/promptParser.js](./web/src/promptParser.js) | 混排文档多提示词批量解析器（支持 Markdown/代码围栏/参数表与防笔记污染） |
| [web/src/promptBatchParams.js](./web/src/promptBatchParams.js) | 批量参数合并、MOSS Base/SFT 检测与参数警报规则 |
| [web/src/h3Video.js](./web/src/h3Video.js) | H3 视频模式识别、分辨率与帧率选项及提交载荷构建 |
| [web/src/taskData.js](./web/src/taskData.js) | 任务类型识别、工作流分类徽章、规格摘要、耗时与 CU 格式化 |
| [ops/import_workflows.py](./ops/import_workflows.py) | 无第三方依赖的公开工作流批量导入脚本（支持 `--dry-run`） |
| [ops/register_h3_ab.py](./ops/register_h3_ab.py) | 专项注册 H3 工作流并核对缺失 FL2VA 模型的脚本 |
| [ops/install_frame_interpolation.py](./ops/install_frame_interpolation.py) | 在就绪 GPU 上在线安装 `ComfyUI-Frame-Interpolation` 并重启 ComfyUI |
| [ops/h3_gpu_control.py](./ops/h3_gpu_control.py) | 容器内通过 stdin 向运行中 Colab GPU 内核发送受控诊断代码 |
| [ops/backup.sh](./ops/backup.sh) | 使用 SQLite Online Backup API 安全备份数据库、配置与工作流 |

---

## 4. API 端点与数据结构摘要

### 4.1 公开与鉴权路由

- **公开路由**：`GET /api/health`、`GET /api/auth/status`、`POST /api/auth/login`；以及由一次性高熵 `state` 令牌防护的 Google OAuth 回调端点 `GET /api/oauth/colab/callback`、`GET /api/oauth/drive/callback`。
- **其余全部路由**（`/api/*`、`/v1/*`、`/comfy/*`、WS `/comfy/ws`）必须携带有效 `drawing_session` Cookie 或 `Authorization: Bearer ds-...`。

### 4.2 核心 API 分组

| 分组 | 端点 |
| :--- | :--- |
| 鉴权与设置 | `POST /api/auth/logout`、`GET/PATCH /api/settings`、`GET/POST /api/keys`、`DELETE /api/keys/{key_id}`、`GET /api/requests`、`GET /api/events` |
| OAuth 独立授权 | `GET /api/oauth/status`、`POST /api/oauth/{purpose}/start`、`POST /api/oauth/{purpose}/exchange`、`GET /api/oauth/{purpose}/callback`、`POST /api/oauth/{purpose}/import`、`POST /api/oauth/{purpose}/test`、`DELETE /api/oauth/{purpose}` |
| 运行时 | `GET /api/runtime`、`GET /api/runtime/gpus`、`GET /api/runtime/logs`、`POST /api/runtime/start`、`POST /api/runtime/stop`、`POST /api/runtime/reset`、`POST /api/runtime/recover`、`POST /api/runtime/restart` |
| 模型与工作流 | `GET /api/models`、`POST /api/models/scan`、`PATCH/DELETE /api/models/{model_id}`、`POST /api/models/{model_id}/sync`、`POST /api/models/sync-batch`、`POST /api/models/{model_id}/sync/cancel`、`DELETE /api/models/{model_id}/cache`、`GET/POST /api/workflows`、`GET/DELETE /api/workflows/{workflow_id}`、`GET/POST/DELETE /api/presets` |
| 任务与上传 | `GET/POST /api/tasks`、`GET /api/tasks/{task_id}`、`POST /api/tasks/{task_id}/cancel`、`POST/GET /api/uploads` |
| 图库与媒体 | `GET /api/gallery`、`GET/POST /api/gallery/projects`、`PATCH/DELETE /api/gallery/projects/{project_name}`、`POST /api/gallery/sync`、`POST /api/gallery/prune-local`、`POST /api/gallery/import`、`POST /api/gallery/batch`、`POST /api/gallery/concat`、`GET/PATCH/DELETE /api/gallery/{id}`、`GET /api/gallery/{id}/thumbnail`、`GET /api/gallery/{id}/preview`、`GET /api/gallery/{id}/download`、`GET /api/gallery/{id}/media`、`POST /api/gallery/{id}/extract-frame`、`POST /api/gallery/{id}/archive` |
| OpenAI & ComfyUI | `GET /v1/models`、`POST /v1/images/generations`、`POST /v1/images/edits`、`ANY /comfy/{path}`、`WS /comfy/ws` |

### 4.3 SQLite 存储结构（`data/studio.sqlite3`）

- **`records(kind, id, payload)`**：核心 JSON 文档表，存放 `settings`、`keys`、`models`、`presets`、`projects`、`tasks`、`gallery`、`requests`。
- **`gallery_index`**：由 `Database` 自动维护与回填的图库检索索引，剥离庞大的 `api_workflow` / `model_snapshot`，加速按 `project`、`media_type`、`favorite`、`model`、`lora`、`tag`、`date`、`q` 的分页查询。
- **`tasks_index`**：任务检索与状态计数索引，支持快速分页与活动任务统计。
- **工作流存储**：自定义与导入工作流以独立 `.json` 文件持久化在 `data/workflows/<id>.json`。

---

## 5. 参数硬约束速查

1. **MiniMax H3 视频**：
   - `width`、`height` 须为 **`32` 的正整数倍**（`32..16384`）。
   - 采样 `fps` 固定 **`24`**；`length`（`5..3600`）与 `duration_seconds` 自动向上对齐至 **`5 + 17k`**。
   - `continue_frames`（路线 B 尾段续写）须满足 `17k + 5` 且严格小于本段总 `length`。
   - `ref_images` / `keyframes` 最多 **9 张**。
2. **SeedVR2 视频精修（`video-seedvr2`）**：
   - `resolution` 为 `128..4096` 偶数（默认 `1080`）；`max_resolution` 为 `0` 或 `128..8192` 偶数。
   - `frame_batch_size` 须满足 **`4k + 1`**（`1..65`，默认 `5`）；`temporal_overlap`（`0..16`）须严格小于 `frame_batch_size`。
   - `color_correction` 允许值：`lab`、`wavelet`、`wavelet_adaptive`、`hsv`、`adain`、`none`。
3. **MOSS 排版与流调度**：
   - 坐标排版画布宽高推荐为 **`16` 的正整数倍**；`shift` 范围 `0..100`（MOSS Base 默认 `5.0`，MOSS SFT 默认 `3.5`）。
4. **项目名称校验**：
   - 长度 `<= 80` 字符，不允许包含 `/`、`\`、控制字符（`< 32`）以及保留值 `__none__`、`.`、`..`。

---

## 6. 公开版脱敏与清理记录

为确保代码仓库可安全分发与后续开源，本次整理已完成以下清理：

1. **彻底剔除两个自制私有 LoRA 及专属工作流**：
   - 剔除原私人画风 LoRA（古风绘本风格 LoRA 系列）及其 5 个绑定工作流（原 MOSS Base/SFT、Krea2+MOSS 双采、Anima/Illustrious SeedVR2 打包工作流），并替换为可直接基于公开底模运行的 5 个工作流：`moss-base.json`、`moss-sft.json`、`krea2-moss-dual.json`、`pack-anima-seedvr2.json`、`pack-illustrious-seedvr2.json`。
   - 确保不含自制游戏立绘 CG LoRA 及对应特调工作流；保留 `hsr-style-3066017.json` 中来自 Civitai 的公开模型 `anima_hsr_cg.safetensors`（Civitai 2727585）。
   - 同步清理前端预设/排版/徽章及前后端测试夹具中的私有 LoRA 名称、触发词与个人角色示例。
2. **移除历史私有运维/快照文件**：
   - 删除 `Dockerfile.1.0.27-layer`、`SOURCE.md`、`ops/ROLLBACK-README.md`、`ops/register.py`、`ops/drive_import.py`。
   - 参数化 `ops/backup.sh` 与 `ops/register_h3_ab.py`，去除内部目录与私人 Google Drive 文件 ID 硬编码，新增标准化的 `ops/import_workflows.py`。
3. **凭据隔离**：
   - 源码树完全不含任何生产数据库、`data/`、`config/private/*.json`、`.env`、管理员密码或 API Key。

---

## 7. 运维、备份、升级与回滚指引

### 7.1 数据备份（含 SQLite WAL 铁律）

权威持久化状态位于宿主机的：
- `./data/studio.sqlite3`（以及 WAL/SHM 文件）
- `./data/workflows/`、`./data/admin-password`、`./data/runtime.json`
- `./config/private/colab.json`、`./config/private/drive.json`

**切勿直接热拷贝运行中的 `.sqlite3` 单文件而忽略 `-wal`！** 请始终使用内置在线备份脚本或先停容器后再完整复制：

```bash
STUDIO_DIR=/opt/drawing-studio bash ops/backup.sh
```

### 7.2 升级与回滚检查清单

1. **升级前检查**：
   - 查询 `GET /api/tasks?status=running` 与 `GET /api/tasks?status=queued` 确认无活跃或排队中的推理任务。
   - 查询 `GET /api/runtime` 确认无正在进行的模型同步；升级控制面容器本身不会销毁已启动的 Colab GPU，但尽量避免在模型传输中重启。
2. **执行构建与更新**：
   - 先运行 `ops/backup.sh` 完成在线备份。
   - 执行 `docker compose build && docker compose up -d`。
3. **上线后验收**：
   - 验证 `GET /api/health` 返回 `{"ok": true, "version": "1.0.42"}`。
   - 验证登录态、`/api/runtime` 状态接管、`GET /api/gallery?page=1&limit=12` 列表及视频播放（206 Range）。
4. **回滚方法**：
   - 若需回滚，先在无活跃任务时停止容器，切回旧镜像版本并恢复升级前备份的 `data/` 与 `compose.yaml`，再启动容器。
