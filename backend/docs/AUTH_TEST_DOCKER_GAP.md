# Docker 测试缺口（第七节 7.4）

该文件记录了完整发布验证通过后，`backend/docs/AUTH_TEST_PLAN.md`
中唯一**未执行的**测试用例。

## 为什么存在这种差距

发布验证环境 (sg_dev: `10.251.229.92`) **没有
安装 Docker 守护进程**。TC-DOCKER 用例是容器运行时
需要实际 Docker 引擎才能启动的行为测试
`docker/docker-compose.yaml` 服务。

```bash
$ ssh sg_dev "which docker; docker --version"
# (empty)
# bash: docker: command not found
```

所有其他测试计划部分均针对以下任一执行：
- 本地开发盒（Mac，所有服务在本地运行），或
- 已部署的 sg_dev 实例（网关 + 前端 + nginx 通过 SSH 隧道）

## 未执行用例

| 用例 | 标题 | 覆盖内容 | 为什么未运行 |
|---|---|---|---|
| TC-DOCKER-01 | `deerflow.db` 卷持久性 | 验证 `DEER_FLOW_HOME` 绑定挂载在容器重新启动后仍然存在 | 需要`docker compose up` |
| TC-DOCKER-02 | 容器重启后的会话持久性 | `AUTH_JWT_SECRET`env var 在`docker compose down && up` 之后保持 cookie 有效 | 需要 `docker compose down/up` |
| TC-DOCKER-03 | 每个 worker 的速率限制器分歧 | 确认进程内 `_login_attempts` 字典不会在 `gunicorn` worker 之间共享状态（compose 文件中默认为 4）；已知限制，已记录 | 需要多 worker 容器 |
| TC-DOCKER-04 | IM 通道使用内部网关身份验证 | 验证 Feishu/Slack/Telegram 调度程序在调用网关兼容的 LangGraph APIs 时附加进程本地内部身份验证标头以及 CSRF cookie/header | 需要 `docker logs` |
| TC-DOCKER-05 | 重置凭证显示 | `reset_admin`在`DEER_FLOW_HOME` 中写入 0600 凭证文件，而不是记录明文。基于文件的行为通过非 Docker 重置测试进行验证，因此唯一的 Docker 特定差距是验证卷挂载将文件携带到主机 | 需要容器+主机卷 |
| TC-DOCKER-06 | Docker 部署使用 Gateway 嵌入式运行时 | `./scripts/deploy.sh`生成网关 + 前端 + nginx 拓扑（无`langgraph`容器）；与本地`make dev` 相同的身份验证流程 | 需要 `docker compose up` |

## 非 Docker 测试已提供覆盖范围

每个 Docker 案例中的 **auth-relevant** 行为已经由
在 sg_dev 或本地运行的测试用例：

| Docker 用例 | 涵盖的身份验证行为 |
|---|---|
| TC-DOCKER-01（卷持久性） | sg_dev 上的 TC-REENT-01（网关重启后 admin 行仍存在）— 使用相同的 SQLite 文件，只是中间没有容器层 |
| TC-DOCKER-02（会话持久化） | TC-API-02/03/06 (cookie 往返)，加上 TC-REENT-04 (多 cookie) — JWT 验证与进程状态无关，容器重启相当于 `pkill uvicorn && uv run uvicorn` |
| TC-DOCKER-03（每个 worker 的速率限制） | TC-GW-04 + TC-REENT-09（单 worker 速率限制 + 5 分钟过期）。跨 worker 分歧是内存字典的架构属性；没有授权代码路径不同 |
| TC-DOCKER-04（IM 通道使用内部身份验证） | 代码级：`app/channels/manager.py` 使用 `create_internal_auth_headers()` 加上 CSRF cookie/header 创建 `langgraph_sdk` 客户端，因此通道 worker 不依赖浏览器 cookie |
| TC-DOCKER-05（凭据暴露） | `reset_admin`使用模式 0600 写入`.deer-flow/admin_initial_credentials.txt`并仅记录路径 - 唯一的 Docker 独特步骤是绑定挂载是否将此路径投影到主机上，这是`docker compose` 配置检查，而不是运行时行为更改 |
| TC-DOCKER-06（网关嵌入式运行时容器） | 第七节 7.2 已由 TC-GW-01..05 覆盖，第二节（sg_dev 上的 Gateway auth flow）也已覆盖；使用相同 Gateway 代码，容器只是打包方式变化 |

## Docker 可用时的复制步骤

任何安装了 `docker` + `docker compose` 的人都可以通过逐字运行测试计划中的相关部分来重现该缺口。预检查：

```bash
# Required on the host
docker --version           # >=24.x
docker compose version     # plugin >=2.x

# Required env var (otherwise sessions reset on every container restart)
echo "AUTH_JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  >> .env

# Optional: pin DEER_FLOW_HOME to a stable host path
echo "DEER_FLOW_HOME=$HOME/deer-flow-data" >> .env
```

然后从编写的测试计划中运行 TC-DOCKER-01..06 。

## 决策日志

- **不阻止发布。** 每个 Docker 用例中与身份验证相关的行为
  在裸机上都有已经验证过的等效路径。缺口只涉及*容器封装*
  细节（绑定挂载、多 worker、日志收集），不涉及授权代码路径是否有效。
- **TC-DOCKER-05 已在 `AUTH_TEST_PLAN.md` 中就地更新**以反映
  当前重置流程（`reset_admin` → 0600 凭证文件，无日志泄漏）。
  旧的“docker 日志中的 grep '密码：'”期望将会失败
  默默地给予一种虚假的覆盖感。
