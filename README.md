# MailBridge

通过 MCP 搜索、阅读和管理多个邮箱，支持 QQ、Gmail、iCloud 等 IMAP/SMTP 邮箱及 Microsoft Graph。

## 功能

- 搜索邮件、读取正文与会话、获取附件。
- 默认按邮件实际时间从新到旧返回结果，支持跨时区排序。
- 起草、发送、回复和转发邮件，管理文件夹与邮件状态。
- 使用 OAuth 接入远程客户端，也支持 Bearer 认证。

发送邮件和永久删除需提供 `confirm: true`。首次查询大邮箱可能需要建立日期索引，后续只补取新邮件的日期。

## 接入

部署后在客户端添加 `https://mcp.example.com/mailbridge/mcp`，身份验证选择 OAuth。授权页填写服务的连接密码。

```bash
git clone https://github.com/Kfjie-MCP/mailbridge.git
cd mailbridge
cp .env.example .env
```

在私有 `.env` 中将 `PUBLIC_URL` 改为自己的 HTTPS 域名；邮箱账号和应用授权码单独保存在服务器配置中。具体步骤见[部署说明](docs/deployment.md)。

## 文档

[部署与邮箱配置](docs/deployment.md) · [上游使用说明](upstream/README.zh-CN.md) · [来源与维护](UPSTREAM.md)

```bash
python upstream/tests/test_mailbridge.py
python tests/test_newest_first.py
```

基于 [henry200803/mailbridge](https://github.com/henry200803/mailbridge)，保留 [MIT 许可证](upstream/LICENSE)。OAuth 页面使用 [MCP Auth UI](https://github.com/Kfjie-MCP/auth-ui)。
