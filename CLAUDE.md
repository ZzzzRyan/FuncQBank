# CLAUDE.md

泛函题库 **FuncQBank** —— 泛函分析考前刷题 / 记忆原题的 Web 题库（考试只考原题）。
本文件给接手开发的 AI 会话（Claude / Codex 等，`AGENTS.md` 也指向这里）提供**必须遵守的原则与项目约定**。请先读完再动手。

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
4. **对 LLM 不稳定的容错**：用户的 LLM 网关偶发「空响应 / 把非流式当 SSE 字符串返回 / JSON 里 LaTeX 反斜杠未转义」三种故障，已在 `scripts/extract.py` 的 `call_model` / `parse_sse_content` / `_fix_latex_escapes` / `parse_json_loose` 兜底——复用它们，别新写一套。
5. **易人工纠错且非破坏**：后台「重新识别」只把结果**填进表单供检查、不写库**；失败（如空响应）**不改动任何数据**。改这块时保持「未点保存即不落库」的语义。
6. **自包含易部署**：无 Node、KaTeX 已 vendored、SQLite 单文件、CSS 手写；保持 `uv` + Docker 可一键起。新增前端能力优先原生实现，别引 CDN/构建链。
7. **安全基线**（"一定程度即可"，别退化）：argon2 哈希、写操作校验 CSRF（表单隐藏字段 `csrf_token`；fetch 用 `X-CSRF-Token` 头）、签名会话 Cookie、登录/注册限流、统一安全头(含 CSP)。

## 代码风格与结构纪律（保持整洁，别堆屎山）
**职责归位——先找现成的家，别另起炉灶。** 改动前先确认这段逻辑该落在哪一层，沿用既有模块，不要复制粘贴出第二套：
- 数据模型与所有常量 → `app/models.py`；DB 引擎/会话 → `app/db.py`；配置 → `app/config.py`。
- 鉴权/角色/会话 → `app/auth.py`+`app/security.py`；题面安全渲染 → `app/render.py`；模板公共上下文注入 → `app/templating.py:page()`。
- HTTP 路由 → `app/routers/{auth_routes,practice,admin}.py`。**路由保持「薄」**：只做请求解析、鉴权、查询、渲染/重定向；可复用或较复杂的逻辑下沉为模块级函数（如 `practice.py` 的 `VISIBLE`、查询辅助）。当前规模别过度分层——不需要 service/repository 框架，一个文件一职责即可。
- 数据管线脚本 → `scripts/`，与 web 应用 `app/` 严格分离，每个脚本可独立运行、可断点续跑。
- 模板都继承 `base.html`；可复用片段用 `_` 前缀（如 `_practice_body.html`）。

**风格惯例（贴合既有代码）：**
- 每个模块顶部一句话 docstring 说明职责；UI 文案用中文；注释密度/命名风格随周围。
- 除 `app/models.py` 外的模块普遍带 `from __future__ import annotations`；`models.py` **绝不能加**——会破坏 SQLModel 的 `Relationship` 解析。
- 题型 `single/multiple/judge`、状态 `pending/verified/flagged`、角色 `user/admin` 常量都在 `app/models.py`，引用常量名而非裸字符串。**flagged 题目从刷题页隐藏**（`practice.py` 的 `VISIBLE`）。
- 改 `app/static/app.css` 或 `app.js` 后，**bump `app/templating.py` 里的 `asset_v`** 刷新浏览器缓存。
- 设计系统集中在 `app.css` 的 CSS 变量（教材/考卷风格：暖纸张、衬线、钢笔蓝、红笔批改色）；保持统一、移动端优先。
- 收尾前跑语法自检（见上）；新增依赖一律 `uv add`，不手改 `pyproject.toml`/`uv.lock`。

**架构优先，别打补丁。** 遇到反复出现或牵连多处的问题，从数据流与职责边界层面修正一次，**不要在调用点堆 `if`/特例/重复兜底**。容错与渲染已有统一入口（`extract.py` 的解析兜底、`render_rich`/`richToHtml`），复用它们而不是再写一套。动手大改前若涉及跨层重构或行为变化，先说明思路。

## 数据库与环境（重要——项目已上线）
- **本机是开发/测试环境**，这里的 `data/app.db` 可丢弃：需要就 `rm data/app.db && uv run scripts/seed.py` 重建。本机不要为了保数据库而妥协代码质量。
- **但项目已上线，线上库存有不可重建的真实数据**：用户账号、`verified` 人工校对、刷题进度（`UserQuestionState`）。代码要照顾这条线上现实。
- **无迁移框架——这是硬约束**：`app/db.py:init_db()` 只 `SQLModel.metadata.create_all`（**仅建缺失的表，绝不会 `ALTER` 既有表/加列/改约束**）。所以任何 schema 改动在线上 `app.db` 上是静默失配，不会自动生效。
- **任何数据库结构改动必须先和用户说明并给出迁移方案**——包括：增删改模型字段、改 `UniqueConstraint`/索引、改 JSON 列形状（`options`/`answer`/`auto_flags`）、改状态/类型常量语义。说明时附上线上迁移路径（手写 `ALTER TABLE` / 一次性迁移脚本 / 导出→重建→重导），别让用户在线上撞见数据丢失或启动报错。
- **不算 schema 改动、无需特别报备**：纯内容修正（改 `data/extracted/**.json` 后 `seed.py` 重入库，`verified` 行受保护不被覆盖）。
- 真正部署见 `README.md`（Docker + 反代 HTTPS）。
