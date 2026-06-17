# 泛函题库 FuncQBank

泛函分析课程题库网页应用，用于**考前刷题与记忆原题**。题目原本是手机截图（题干+答案混在一张图里），
本项目用大模型把每张图结构化提取为「题干 / 选项 / 答案 / 解析」，公式转为 LaTeX，做成**题目与答案分离**、
界面美观、移动端友好的网页题库；并提供管理员后台校对纠错、个人进度、错题本、搜索筛选与随机练习。

- 后端：FastAPI + SQLite（SQLModel），单进程，`uv` 管理
- 前端：服务端渲染 + 原生 JS + KaTeX（已本地内置，离线可用），无需 Node 构建
- 题型：单选 / 多选 / 判断，共 **213 题**（第二章 145 题、第三章 68 题）
- 用户系统：开放注册；普通用户只能刷题，管理员才能进入后台与查看原图

---

## 1. 环境准备

需要 [`uv`](https://docs.astral.sh/uv/)（Python 包管理）。克隆代码后：

```bash
uv sync          # 创建虚拟环境并安装依赖
cp .env.example .env
```

编辑 `.env`：

```ini
OPENAI_ENDPOINT="https://你的网关/v1"   # OpenAI 兼容、支持视觉的接口
OPENAI_MODEL="你的视觉模型"
OPENAI_APIKEY="sk-..."

SESSION_SECRET="改成一长串随机字符串"      # 务必修改！可用 python -c "import secrets;print(secrets.token_hex(32))" 生成
REGISTRATION_OPEN=true                    # 是否开放自助注册
COOKIE_SECURE=false                       # 走 HTTPS（含 Cloudflare Tunnel）时设为 true
TRUST_PROXY_HEADERS=true                  # 在反代/隧道后面：从代理头取真实客户端 IP 用于限流；直连裸跑才设 false
# ADMIN_USERNAME=                          # 可选：启动时自动把该用户提升为管理员
```

> `.env` 已被 git 忽略，不会泄露密钥。

## 2. 提取题目 → 入库

```bash
# 1) 用大模型识别 docs/ 下全部图片 → data/extracted/*.json（可断点续跑）
uv run scripts/extract.py                 # 全量；已提取的会自动跳过
uv run scripts/extract.py --limit 5       # 先小批量试跑看看效果
uv run scripts/extract.py --retry-flagged # 把被标记的（多为网关偶发空响应）重试一遍

# 2) 为缺少解析的题目批量生成简短解析（默认不覆盖已有解析、不处理 flagged）
uv run scripts/generate_explanations.py --limit 5  # 先小批量试跑
uv run scripts/generate_explanations.py

# 3) 导入数据库（幂等；已「校对」的题目不会被覆盖）
uv run scripts/seed.py
```

提取结果 `data/extracted/` 是题目内容的事实源，建议纳入 git 版本管理。
脚本对识别失败/异常会标记为 `flagged`，方便后台优先复核。

## 3. 创建管理员

```bash
uv run scripts/create_admin.py <用户名> --name "显示名"   # 会提示输入密码
```

> 也可不创建：**第一个注册的账号会自动成为管理员**。建议你先注册/建号，再把网址发给同学。

## 4. 本地运行

```bash
uv run uvicorn app.main:app --reload --port 8000  # 自动重载，开发用；生产部署见下
```

打开 http://127.0.0.1:8000 ：

- **刷题**：首页按章节进入；先看题，点「提交答案 / 直接看答案」揭示正确答案；答错自动进**错题本**；可标记「★ 掌握」。
  - 快捷键：`←/→` 切题、数字键选项、回车揭示/下一题、`m` 标记掌握。
- **错题本 / 搜索**：顶部导航进入；搜索支持题干关键词 + 题型筛选。
- **后台校对**（仅管理员，导航「后台」）：详见下方《后台校对操作要点》。

> 普通用户看不到后台，也无法访问原图（`/admin/image/*` 仅管理员，返回 403）——因为原图里含答案。

## 5. Docker 部署（推荐，便于迁移）

```bash
# 在服务器上：填好 .env 后
docker compose up -d --build
```

- 题目内容（`docs/` 图片 + `data/extracted` JSON）打包进镜像；运行期数据库存于命名卷 `funcqbank-data`（容器内 `/app/var/app.db`），**重启/升级不丢数据**。
- 首次启动会自动 `seed` 入库。
- 默认监听 `8000`。迁移到新服务器：拷贝整个仓库 + `.env`，`docker compose up -d --build` 即可；要保留用户/进度，连同卷一起迁移（`docker run --rm -v funcqbank-data:/v -v $PWD:/b alpine tar czf /b/funcqbank-data.tgz -C /v .` 备份）。

### HTTPS / 反向代理

公网部署务必走 HTTPS，并在 `.env` 设 `COOKIE_SECURE=true`（让会话 Cookie 带 `Secure`）。
应用本身只需监听本地端口（如 `127.0.0.1:8011`），由前面的反代/隧道终止 TLS。下面三种任选其一。

**A. Caddy（自动签发证书）** —— `Caddyfile`：

```
你的域名 {
    reverse_proxy 127.0.0.1:8011
}
```

`caddy run` 即自动申请并续期证书。Caddy 默认会带上 `X-Forwarded-For`/`X-Forwarded-Proto`。

**B. Nginx** —— 关键是把真实客户端 IP 和协议转发给应用（否则限流会把所有人当成同一个 IP）：

```nginx
server {
    server_name 你的域名;
    location / {
        proxy_pass         http://127.0.0.1:8011;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;        # ← 真实 IP，限流靠它
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
    # listen 443 ssl; ssl_certificate ...;  证书可用 certbot 签发
}
```

**C. Cloudflare Tunnel（cloudflared，无需公网开端口、无需自己管证书）**

Cloudflare 在边缘终止 TLS，浏览器看到的就是 HTTPS；cloudflared 通过**出站**连接把流量送到本机，
所以服务器**不必对公网开放任何入站端口**。要点：

1. `.env` 设 `COOKIE_SECURE=true`（浏览器侧是 HTTPS，Secure Cookie 会正常发送）。
   建议在 Cloudflare 控制台开 **Always Use HTTPS**，避免偶发 http 导致带 Secure 的会话 Cookie 不发出、表现为“登录后立刻掉登录”。
2. 真实客户端 IP 由 Cloudflare 放在 `CF-Connecting-IP` 头里并经隧道传入，应用已优先读取它做限流（`TRUST_PROXY_HEADERS=true`，默认开）。
3. 隧道把域名指向本机端口即可（`config.yml` 片段）：

```yaml
tunnel: <你的隧道ID>
credentials-file: /root/.cloudflared/<隧道ID>.json
ingress:
  - hostname: 你的域名
    service: http://localhost:8011
  - service: http_status:404
```

`cloudflared tunnel run` 启动（建议做成 systemd 服务）。应用照常 `uvicorn ... --host 127.0.0.1 --port 8011` 跑即可。

> 真实 IP 说明：限流从 `CF-Connecting-IP` → `X-Real-IP` → `X-Forwarded-For` 依次取值，兼容 Cloudflare Tunnel 与 Nginx/Caddy。
> 这些头**仅在应用只经反代/隧道访问时**可信；若你把应用直接裸跑暴露公网（不推荐），请设 `TRUST_PROXY_HEADERS=false`，否则它们可被伪造绕过限流。

## 6. 不用 Docker 的裸机部署

```bash
# 1) 环境准备（同 1~3 步）
uv sync
uv run scripts/seed.py
uv run scripts/create_admin.py <用户名>

# 2) 裸机部署
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # 建议用 systemd 守护 + Caddy/Nginx 反代
```

## 7. 备份 / 导出

```bash
uv run scripts/export.py        # DB → data/export/*.json（含人工校对结果，便于备份/版本管理）
```
SQLite 数据库就是单个文件，直接拷贝 `data/app.db`（或卷内 `app.db`）即可备份。

---

## 目录结构

```
app/
  main.py            FastAPI 装配（中间件、安全头、路由、异常处理）
  config.py          读取 .env
  db.py  models.py   SQLite + SQLModel 模型
  auth.py security.py  会话/角色、密码哈希(argon2)、CSRF、限流
  render.py          题干/选项的 LaTeX+加粗 安全渲染
  templating.py      Jinja2 与公共上下文
  routers/           auth_routes / practice / admin
  templates/  static/  (app.css, app.js, vendor/katex 内置)
scripts/
  extract.py  generate_explanations.py  import_explanations.py  seed.py  export.py  create_admin.py
data/extracted/      提取结果（内容事实源，入 git）
docs/                题目原图（仅管理员可见）
Dockerfile  docker-compose.yml
```

## 安全说明（"一定程度即可"）

密码 argon2 哈希；会话为签名 Cookie（`httponly`，`samesite=lax`，HTTPS 下 `secure`）；表单与写接口校验 CSRF；
登录/注册有基础限流（反代/隧道后按真实客户端 IP，见上）；统一安全响应头（含 CSP）；原图仅管理员可访问。开放注册意味着拿到网址即可注册——
如需收紧，把 `.env` 的 `REGISTRATION_OPEN` 设为 `false`（之后由管理员用 `create_admin.py` 建号）。

## 后台校对操作要点

编辑页三栏：**左＝原图，中＝整道题实时预览（与刷题页一致、答案高亮、公式实时渲染），右＝编辑表单**。改右侧任意字段，中间预览即时更新。

题目三种状态：

| 状态 | 含义 | 是否出现在刷题页 |
|---|---|---|
| **待校对 pending** | 已识别、尚未人工确认 | 是 |
| **已校对 verified** | 你已人工确认 | 是；`seed.py` 重新导入**不会覆盖** |
| **需复核 flagged** | 自动检查有疑点（答案字母不在选项内、字段缺失、批量提取时网关失败等） | **否，隐藏直到处理** |

**校对流程**：列表用「需复核」筛选优先处理 → 打开对照原图修正 → 点 **「保存并标记已校对」**（或「保存并下一道」连续校对）。保存后状态变 verified 并重新出现在刷题页。

**「用大模型重新识别此图」的行为（重要）**：它只调用模型，把新结果**填入右侧表单供你检查**，**不会自动保存、也不会改 `data/extracted/`**——你确认无误再点「保存」才会写库。若遇网关空响应/超时（该网关偶发，见下），会提示「识别失败，未改动任何数据」，**原题保持原样**，稍等重试即可。所以不存在“点一下就把识别好的数据改坏”的风险。

> 批量找回偶发失败的题：命令行 `uv run scripts/extract.py --retry-flagged` 重试，再 `uv run scripts/seed.py`（已校对的不受影响）。当前 213 题已全部识别成功、无 flagged。

## 生产库字段补丁（导入解析等）

当 `data/extracted/*.json` 里补了某些字段，但生产库里已有用户、进度、人工校对结果时，不要重新 `seed` 覆盖整题。
用 `scripts/import_explanations.py` 这种“字段级补丁”脚本，只更新白名单字段（当前支持 `explanation,note`），按 `rel_path` 匹配已有题目，不新建题目，也不改题干/选项/答案/状态/用户数据。

Docker 部署时一定在容器内跑脚本：宿主机直接 `uv run ...` 会指向仓库里的 `data/app.db`，不是生产卷里的 `/app/var/app.db`。

```bash
# 1) 拉代码并重建镜像，让容器里拿到最新 JSON 和脚本
git pull
docker compose up -d --build

# 2) 先 dry-run。确认输出里的数据库是 /app/var/app.db，且 DB 题目数正常
docker compose exec app uv run --no-sync scripts/import_explanations.py

# 3) 写入。更稳妥的做法是短暂停服务，避免备份 SQLite 时有并发写入
docker compose stop app
docker compose run --rm app uv run --no-sync scripts/import_explanations.py --apply
docker compose up -d app
```

常用变体（默认仍是 dry-run；确认无误后再按第 3 步加 `--apply` 写入）：

```bash
# 只处理某个章节/路径片段
docker compose exec app uv run --no-sync scripts/import_explanations.py --only "3.2"

# 同步多个已允许字段
docker compose exec app uv run --no-sync scripts/import_explanations.py --fields explanation,note

# 覆盖生产库里已有的非空字段；谨慎使用，先 dry-run 看清楚旧值/新值
docker compose exec app uv run --no-sync scripts/import_explanations.py --overwrite
```

`--apply` 默认会在同一个 Docker 卷里生成 `app.db.bak-时间戳` 备份；不要随手加 `--no-backup`。`uv run --no-sync` 的意思是“运行脚本前不重新同步依赖”，因为镜像构建时已经安装好了依赖，生产容器里这样更快也更少扰动。以后要补别的简单文本字段，先把字段加入脚本的白名单，再按同样流程 dry-run → 确认 DB 路径/变更计划 → apply。
