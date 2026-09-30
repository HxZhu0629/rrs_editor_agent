# 项目介绍网站

本目录是独立的中文项目导览，使用原生 HTML、CSS、JavaScript，没有 npm 依赖或后端请求。只发布本目录，不发布数据库、稿件、环境变量或整个前端应用。

预期网址：<https://HxZhu0629.github.io/rrs_editor_agent/>。首次启用完成并且部署工作流成功后才可访问。

## 本地预览

在项目根目录运行：

```powershell
python -m http.server 8088 --bind 127.0.0.1 --directory site
```

打开 <http://127.0.0.1:8088>。直接打开 `index.html` 也可阅读；复制按钮在剪贴板不可用时会选中文本，供手动复制。

## 首次启用 GitHub Pages

1. 打开仓库 **Settings → Pages → Build and deployment**。
2. 将 **Source** 设为 **GitHub Actions**。
3. 在 **Actions → Publish project guide → Run workflow** 手动执行一次，或推送对 `site/` 的更新。
4. 等待部署任务成功，再访问上述网址。

工作流 `.github/workflows/pages.yml` 会上传 `site/` 为 Pages artifact 并部署。首次开启仓库 Pages 需要仓库管理权限，SSH 推送权限不能替代这一步；默认 `GITHUB_TOKEN` 不能为尚未启用 Pages 的仓库完成首次开通。

## 内容与交互

- 五阶段稿件流程，显示持久化结果、同步/异步边界和源码入口。
- 文档 RAG 与审稿人召回对照，覆盖增强选项、评分和筛选局限。
- 预设 Agent 工具轨迹，逐步解释技能、检索、推荐、草稿和交付。
- 工程架构、数据模型、MCP、评测方法、API、启动步骤和常见问题。
- 手机导航、键盘可操作标签页、减少动画偏好、无 JavaScript 的可读内容和复制降级处理。

所有演示内容为静态说明，不连接模型或生成真实候选名单。页面中的技术描述基于仓库代码；业务实现变化后，需要同步更新 `index.html` 和 `script.js` 中对应说明。
