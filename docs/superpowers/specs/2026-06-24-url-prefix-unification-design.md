# URL 路径统一设计

> 日期: 2026-06-24
> 状态: 已批准

## 背景

hindsight-manager、docupipe-manager、xinyi-platform 三个服务的 URL path 缺少统一规划：
- HM 的页面路由全在根路径（`/dashboard`、`/api-keys`），与 API 混在一起
- docupipe 已有 `/docupipe/` 页面前缀，但 API 和 auth 仍在根路径
- xinyi-platform 全部在根路径
- 未来如果三个服务跑在同一端口的不同 contextPath 上，反代转发规则会很麻烦

## 目标

所有服务的所有路由（页面、API、auth、静态资源）统一加服务名前缀：

```
/xinyi/*        → xinyi-platform (8000)
/hindsight/*    → hindsight-manager (8001)
/docupipe/*     → docupipe-manager (8002)
```

## 方案

使用 `app.include_router(router, prefix="/{service}")` 方式，不改路由文件本身。

### 路由布局

#### xinyi-platform
```
/xinyi/health
/xinyi/_ui/static              ← install_ui 静态文件
/xinyi/static                  ← 自身静态
/xinyi/login
/xinyi/logout
/xinyi/account
/xinyi/oauth/authorize
/xinyi/oauth/token
/xinyi/oauth/revoke
/xinyi/internal/users/batch-get
/xinyi/internal/users/search
/xinyi/internal/auth/revoke
/xinyi/internal/audit
/xinyi/admin/clients
/xinyi/admin/users
/xinyi/admin/audit-logs
/xinyi/admin/login-history
```

#### hindsight-manager
```
/hindsight/health
/hindsight/_ui/static
/hindsight/static
/hindsight/                     → redirect → /hindsight/dashboard
/hindsight/login
/hindsight/dashboard
/hindsight/api-keys
/hindsight/admin/tenants
/hindsight/admin/api-keys
/hindsight/admin/task-monitor
/hindsight/auth/login-redirect
/hindsight/auth/callback
/hindsight/auth/logout
/hindsight/auth/refresh
/hindsight/auth/access-token
/hindsight/auth/otp
/hindsight/auth/otp/redirect
/hindsight/auth/exchange-otp
/hindsight/tenants/*
/hindsight/admin/api/*
```

#### docupipe-manager
```
/docupipe/health
/docupipe/_ui/static
/docupipe/static
/docupipe/                      → redirect → /docupipe/projects
/docupipe/projects
/docupipe/projects/new
/docupipe/projects/{id}
/docupipe/projects/{id}/tasks/new
/docupipe/projects/{id}/tasks/{tid}/edit
/docupipe/runs
/docupipe/runs/{id}
/docupipe/credentials
/docupipe/auth/login-redirect
/docupipe/auth/callback
/docupipe/auth/logout
/docupipe/auth/refresh
/docupipe/api/projects/*
/docupipe/api/projects/{id}/members
/docupipe/api/projects/{id}/credentials/*
/docupipe/api/projects/{id}/tasks/*
/docupipe/api/projects/{id}/env-vars/*
/docupipe/api/users/search
/docupipe/api/runs/*
/docupipe/api/stats
/docupipe/admin/api/projects
```

### 公共组件改动

#### install_ui()
- 新增 `ui_static_prefix` 参数
- 静态文件挂载路径从 `/_ui/static` 改为 `{ui_static_prefix}/_ui/static`

#### topbar.html
- 退出按钮 `action` 从硬编码改为使用 `service_prefix` 变量
- 平台: `{service_prefix}/logout`
- 业务应用: `{service_prefix}/auth/logout`
- `service_prefix` 由 `install_ui` 注入到 `app.state.ui`

#### registry.py
- 产品链接 `url_template` 调整：
  - 平台: `{platform_url}/account` （platform_url 已含 /xinyi）
  - HM: `{manager_url}/dashboard` （manager_url 已含 /hindsight）
  - docupipe: `{docupipe_url}/projects` （docupipe_url 已含 /docupipe）

### 配置改动

#### docupipe-manager `.env`
```
DOCUPIPE_MANAGER_PLATFORM_URL=http://localhost:8000/xinyi
DOCUPIPE_MANAGER_OAUTH_REDIRECT_URI=http://localhost:8002/docupipe/auth/callback
```

#### hindsight-manager `.env`
```
HINDSIGHT_MANAGER_PLATFORM_URL=http://localhost:8000/xinyi
HINDSIGHT_MANAGER_OAUTH_REDIRECT_URI=http://localhost:8001/hindsight/auth/callback
```

### 异常处理器改动

#### docupipe-manager main.py
```python
async def page_auth_redirect(request, exc):
    if exc.status_code == 401 and request.url.path.startswith("/docupipe/"):
        # 页面路由才跳转，API 返回 JSON
        if not request.url.path.startswith("/docupipe/api/"):
            return RedirectResponse(url=f"/docupipe/auth/login-redirect?return_to=...")
    return JSONResponse(...)
```

#### hindsight-manager main.py
```python
async def _page_auth_redirect(request, exc):
    if exc.status_code == 401 and request.url.path.startswith("/hindsight/"):
        if not request.url.path.startswith("/hindsight/api/"):
            return RedirectResponse(url=f"/hindsight/auth/login-redirect?return_to=...")
    return JSONResponse(...)
```

### xinyi-platform logout 端点

logout.py 中 `_render_slo_page` 的 SLO iframe logout_url 已经是完整 URL（含端口号），不需要改动。但 `install_ui` 中 `app.state.ui` 的 `service_prefix` 需要设置。

### OAuth redirect_uri 更新

xinyi-platform 的 BusinessClient 表中注册的 `redirect_uris` 需要更新：
- docupipe-prod: `http://localhost:8002/docupipe/auth/callback`
- hm-prod: `http://localhost:8001/hindsight/auth/callback`
- logout_url:
  - docupipe-prod: `http://localhost:8002/docupipe/auth/logout`
  - hm-prod: `http://localhost:8001/hindsight/auth/logout`

## 不改动

- 每个路由文件的 `APIRouter(prefix=...)` 定义 — 保持不变
- 数据库 schema — 无影响
- JWT 签名/验签逻辑 — 无影响
