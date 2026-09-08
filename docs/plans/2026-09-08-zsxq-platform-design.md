# 知识星球第三平台接入 — 设计文档

- **日期**：2026-09-08
- **状态**：in-progress（P0–P3 代码已落地，待真机 OAuth/发帖验收）
- **Tech Lead**：用户

## 背景与目标

将知识星球（zsxq）做成 stock-media-ai-bot 的第三发布平台，与雪球/聚宽同级：设置页 OAuth 绑定、发帖管理一键勾选、我的帖子可看可评。

成功标准：用户完成设备码授权并选择星球后，发帖管理可把诊断润色内容（含海报图）发到知识星球。

## 用户故事

- 作为运营，我希望在设置里绑定知识星球账号并选默认星球，以便后续一键发帖
- 作为运营，我希望发帖时勾选知识星球，以便和雪球/聚宽同时分发
- 作为运营，我希望在我的帖子里看到知识星球主题并评论

## 技术方案

### 认证与调用

官方 `zsxq-cli`（Go 二进制）提供 OAuth 2.0 设备码流程与主题 API。本期采用 **CLI 子进程封装**：

1. `auth login --no-browser --no-wait --json` → `device_code` / `user_code` / `verification_uri`
2. 前端展示授权链接与确认码；后端用 `auth login --device-code <code> --json` 完成轮询落凭据
3. 发帖：`topic +create --group-id … --text … [--files …] --markdown --ai --json`
4. 列表：`user +footprints --json`（或按 group 拉主题）
5. 评论：`topic +reply --topic-id … --text … --json`

每用户隔离 `HOME`/`USERPROFILE` 目录：`data/zsxq_homes/{user_id}`，避免凭据串号（Linux/Docker 下更可靠；Windows 本机 Credential Manager 可能仍按 OS 用户共享，单运营账号可接受）。

Token **不依赖宿主机全局 Keychain 作为产品契约**；CLI 凭据落在隔离 home，业务字段（`group_id` 等）写入 `platform_accounts.credentials_json`。

### 范围

**做**：登录、选星球、发 talk、列足迹、评论、心跳校验。

**不做**：巡场/周报/精华/专栏/视频场景；提问帖、投票；多星球同发（一期一默认星球）。

## API 变更

| 方法 | 路径 | 请求 | 响应 | 鉴权 |
|------|------|------|------|------|
| POST | `/api/platform/zsxq/login/start` | `{}` | `waiting_for_auth` + uri/code | JWT |
| GET | `/api/platform/zsxq/login/status` | — | success / waiting / error | JWT |
| POST | `/api/platform/zsxq/login/cancel` | — | cancelled | JWT |
| POST | `/api/platform/zsxq/group` | `{group_id, group_name?}` | ok | JWT |
| GET | `/api/platform/zsxq/groups` | — | groups[] | JWT |
| POST | `/api/platform/{platform}/post` | content, image_* | success / post_id | JWT（platform=zsxq） |
| GET | `/api/platform/{platform}/posts` | refresh? | posts[] | JWT |
| POST | `/api/platform/{platform}/comment` | post_id, content | success | JWT |

复用现有 `/api/platform/{platform}/*` 路由，`platform=zsxq` 分支。

## 数据库

- 无新表；复用 `platform_accounts`（`platform='zsxq'`）
- `credentials_json`：`{ group_id, group_name, account_name, device_code? }`
- `cookies_json`：占位 `{ "cli": "1" }`（满足现有 cookie_count/心跳结构）
- `is_valid`：授权成功为 true；心跳失败置 false

## 跨端改动清单

### 后端 (stock-media-ai-bot/backend)

- [ ] `services/zsxq_cli.py` — CLI runner
- [ ] `services/zsxq_login_service.py`
- [ ] `services/zsxq_post_service.py`
- [ ] `services/zsxq_comment_service.py`
- [ ] `routers/platform.py` — zsxq 分支 + groups API
- [ ] `services/heartbeat.py` — zsxq 校验
- [ ] `Dockerfile` — Node + zsxq-cli
- [ ] `.env.example` — `ZSXQ_CLI_PATH` / `ZSXQ_DEFAULT_GROUP_ID`

### Web (frontend)

- [ ] Settings：oauth_device 登录 + 选星球
- [ ] PostManagement：平台列表加 zsxq
- [ ] MyPosts：平台切换含 zsxq
- [ ] `api/client.ts`：类型与 groups API

## Task 拆分

| # | Task | 验证 |
|---|------|------|
| 1 | CLI runner + login | start 返回 uri/code |
| 2 | post / comment / posts | CLI --json 成功解析 |
| 3 | 前端三页 | 设置绑定 + 发帖勾选 |
| 4 | heartbeat + Docker | doctor/auth status 失败标红 |

## 联调验证 Checklist

- [ ] 设置页设备码授权成功
- [ ] 选择默认星球并保存
- [ ] 发帖管理勾选知识星球发出主题（含图可选）
- [ ] 我的帖子可见足迹并可评论
- [ ] token 失效后 is_valid=false 且 UI 提示重授权

## 未决事项

- 纯 Python 直调 `mcp.zsxq.com` OAuth（需稳定 `client_id`）可作为后续优化，去掉 Node/CLI 依赖
- Windows 多 SMAB 用户共用同一 OS 账户时 CLI 钥匙串隔离能力有限

## 风险

- CLI 升级可能改 JSON 字段；封装层集中解析
- 写入限流 429：发帖间隔与错误提示
- Docker 需安装 Node≥16 + `@zsxq/cli-linux-x64`
