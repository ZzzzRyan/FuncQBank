# CLAUDE.md

泛函题库 **FuncQBank** —— 泛函分析考前刷题 / 记忆原题的 Web 题库（考试只考原题）。
本文件给未来的 Claude 会话提供**必须遵守的原则与项目约定**。请先读完再动手。

## 一句话定位
把「题干+答案混在一张图里」的截图，用大模型结构化提取成「题干/选项/答案/解析」，做成**题答分离**、界面美观、移动端友好的刷题网站；管理员后台可逐题校对纠错；自包含、易部署。

## 开发环境与命令（一律用 uv，禁止 pip/poetry/conda）
- 装/同步依赖：`uv sync`；增删依赖：`uv add <pkg>` / `uv remove <pkg>`（不要手改 pyproject）
- 跑服务（本机 **8000 端口已被另一应用占用，改用 8011**）：`uv run uvicorn app.main:app --reload --port 8011`
- 提取题目：`uv run scripts/extract.py [--limit N | --only 子串 | --retry-flagged | --force | --workers N]`
- 生成简短解析：`uv run scripts/generate_explanations.py [--limit N | --only 子串 | --force | --include-flagged | --workers N]`（默认不覆盖已有解析、不处理 flagged）
- 入库（幂等）：`uv run scripts/seed.py`　·　建管理员：`uv run scripts/create_admin.py <用户名>`　·　导出备份：`uv run scripts/export.py`
- 语法自检：`uv run python -m py_compile app/*.py app/routers/*.py scripts/*.py`

## 架构与数据流
`docs/<章>/<节>/imageN.jpg`（原图，含答案）→ `scripts/extract.py`（视觉 LLM）→ `data/extracted/**.json`（内容**事实源**，入 git）→ `scripts/generate_explanations.py`（为缺少解析的题目补短解析，仍写回 JSON）→ `scripts/seed.py` → SQLite(`data/app.db`) → FastAPI 提供网页。
- `app/main.py` 装配（中间件、安全头、异常处理、挂载 `/static`、startup 建表）
- `app/config.py` 读 `.env`（pydantic-settings）；`app/db.py` 引擎/会话；`app/models.py` 模型
- `app/auth.py`+`app/security.py` 会话/角色、argon2、CSRF、限流；`app/render.py` 题干安全渲染；`app/templating.py` 的 `page()` 注入公共上下文
- `app/routers/{auth_routes,practice,admin}.py` 路由；`app/templates/` + `app/static/`（`app.css` 手写设计系统、`app.js` 原生 JS、`vendor/katex` 内置）

## 必须保持的原则（重构时不要破坏）
1. **题答分离**：刷题页只渲染提取出的文本；**含答案的原图绝不暴露给普通用户**——只有 `/admin/image/{id}`（`require_admin`）能取图。
2. **公式妥善处理**：内容存纯文本，数学用 `$...$`、强调用 `**粗体**`。服务端一律经 `app/render.py:render_rich`（整体 HTML 转义、仅在非数学段落转粗体、保留 `$` 给 KaTeX），客户端 KaTeX 自动渲染。新增任何会显示题面/选项/解析的地方都要走 `render_rich`（JS 侧有等价 `richToHtml`）。
3. **后台与刷题隔离 + 用户系统**：普通用户只能刷题；后台 `/admin/*` 全部 `require_admin`。开放注册、**首个注册者自动成管理员**。
4. **对 LLM 不稳定的容错**：网关 `newapi.zryan.xyz` 偶发「空响应 / 把非流式当 SSE 字符串返回 / JSON 里 LaTeX 反斜杠未转义」三种故障，已在 `scripts/extract.py` 的 `call_model` / `parse_sse_content` / `_fix_latex_escapes` / `parse_json_loose` 兜底——复用它们，别新写一套。
5. **易人工纠错且非破坏**：后台「重新识别」只把结果**填进表单供检查、不写库**；失败（如空响应）**不改动任何数据**。改这块时保持「未点保存即不落库」的语义。
6. **自包含易部署**：无 Node、KaTeX 已 vendored、SQLite 单文件、CSS 手写；保持 `uv` + Docker 可一键起。新增前端能力优先原生实现，别引 CDN/构建链。
7. **安全基线**（"一定程度即可"，别退化）：argon2 哈希、写操作校验 CSRF（表单隐藏字段 `csrf_token`；fetch 用 `X-CSRF-Token` 头）、签名会话 Cookie、登录/注册限流、统一安全头(含 CSP)。

## 代码约定
- UI 文案用中文；注释密度/命名风格贴合周围既有代码。
- `app/models.py` **不要加** `from __future__ import annotations`——会破坏 SQLModel 的 `Relationship` 解析。
- 题型常量 `single/multiple/judge`、状态常量 `pending/verified/flagged` 都在 `app/models.py`。**flagged 题目从刷题页隐藏**（`practice.py` 的 `VISIBLE`）。
- `seed.py` 幂等且**保护 `verified` 行**不被覆盖；`extract.py` 可断点续跑。
- 改了 `app/static/app.css` 或 `app.js` 后，**bump `app/templating.py` 里的 `asset_v`** 以刷新浏览器缓存。
- 设计系统集中在 `app.css` 的 CSS 变量（教材/考卷风格：暖纸张、衬线、钢笔蓝、红笔批改色）；保持统一、移动端优先。

## 当前环境（重要）
- **这台机器是测试环境，不是部署目标。`data/app.db` 完全可丢弃**：需要就 `rm data/app.db && uv run scripts/seed.py` 重建，**不要为了保数据库/历史数据而妥协代码质量或回避重构——开发与正确性第一**。（模型/字段要改就大胆改，重新 seed 即可。）
- 真正部署见 `README.md`（Docker + 反代 HTTPS）；部署环境才需要持久化与 `verified` 保护。
