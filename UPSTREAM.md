# 来源与本地维护

- 上游：https://github.com/henry200803/mailbridge
- 基线提交：`870bdd5a533c67064323fa45c8adedcaae1604c4`（1.2.0）
- 许可证：MIT，原文保留在 `upstream/LICENSE`。
- `upstream/` 收录基线源码和本地邮件日期排序修复；这是随仓库维护的源码目录。
- 相对基线的排序改动记录在 `deploy/newest-first.patch`；升级上游时先检查差异并验证回归测试。
- `deploy/` 是 HTTP 适配器和部署模板，`gateway/` 是 OAuth 网关。共享授权界面由独立的 [auth-ui 仓库](https://github.com/Kfjie-MCP/auth-ui) 提供，部署到 `/opt/mcp/auth-ui/`。

邮箱配置、邮件正文、日期索引、OAuth 数据库、密码、依赖和回滚归档均由运行环境管理。
