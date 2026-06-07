---
name: vercel-deploy
description: Deploy applications and websites to Vercel. Use this skill when the user requests deployment actions such as "Deploy my app", "Deploy this to production", "Create a preview deployment", "Deploy and give me the link", or "Push this live". No authentication required - returns preview URL and claimable deployment link.
metadata:
  author: vercel
  version: "1.0.0"
---

# Vercel 部署

将任意项目即时部署到 Vercel。**无需身份验证**。

## 工作原理

1. 将项目打包为 tar 归档（排除 `node_modules` 和 `.git`）
2. 从 `package.json` 自动识别框架
3. 上传到部署服务
4. 返回**预览 URL（Preview URL，即线上站点）**与**认领 URL（Claim URL，可转给用户的 Vercel 账户）**

## 使用方法

```bash
bash /mnt/skills/user/vercel-deploy/scripts/deploy.sh [path]
```

**参数：**
- `path` —— 要部署的目录，或一个 `.tgz` 文件（默认为当前目录）

**示例：**

```bash
# 部署当前目录
bash /mnt/skills/user/vercel-deploy/scripts/deploy.sh

# 部署指定项目
bash /mnt/skills/user/vercel-deploy/scripts/deploy.sh /path/to/project

# 部署已存在的 tar 归档
bash /mnt/skills/user/vercel-deploy/scripts/deploy.sh /path/to/project.tgz
```

## 输出

```
Preparing deployment...
Detected framework: nextjs
Creating deployment package...
Deploying...
✓ Deployment successful!

Preview URL: https://skill-deploy-abc123.vercel.app
Claim URL:   https://vercel.com/claim-deployment?code=...
```

脚本同时向标准输出（stdout）输出 JSON，便于程序化使用：

```json
{
  "previewUrl": "https://skill-deploy-abc123.vercel.app",
  "claimUrl": "https://vercel.com/claim-deployment?code=...",
  "deploymentId": "dpl_...",
  "projectId": "prj_..."
}
```

## 框架识别

脚本会根据 `package.json` 自动识别框架。支持的框架包括：

- **React**：Next.js、Gatsby、Create React App、Remix、React Router
- **Vue**：Nuxt、Vitepress、Vuepress、Gridsome
- **Svelte**：SvelteKit、Svelte、Sapper
- **其他前端框架**：Astro、Solid Start、Angular、Ember、Preact、Docusaurus
- **后端框架**：Express、Hono、Fastify、NestJS、Elysia、h3、Nitro
- **构建工具**：Vite、Parcel
- **以及更多**：Blitz、Hydrogen、RedwoodJS、Storybook、Sanity 等

对于纯静态 HTML 项目（没有 `package.json`），框架字段被设为 `null`。

## 静态 HTML 项目

对于没有 `package.json` 的项目：

- 如果只有一个 `.html` 文件且文件名不是 `index.html`，会自动重命名
- 这样可以保证页面通过根 URL（`/`）访问

## 向用户呈现结果

务必同时展示两个 URL：

```
✓ Deployment successful!

- [Preview URL](https://skill-deploy-abc123.vercel.app)
- [Claim URL](https://vercel.com/claim-deployment?code=...)

View your site at the Preview URL.
To transfer this deployment to your Vercel account, visit the Claim URL.
```

## 故障排查

### 网络出口错误

如果因网络限制导致部署失败（在 claude.ai 上很常见），请告知用户：

```
Deployment failed due to network restrictions. To fix this:

1. Go to https://claude.ai/settings/capabilities
2. Add *.vercel.com to the allowed domains
3. Try deploying again
```
