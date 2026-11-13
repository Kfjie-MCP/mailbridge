# 部署 MailBridge

需要 Python、Node.js 24+ 和 HTTPS 反向代理。部署模板使用 `/opt/mcp/mailbridge`，共享授权页面安装到 `/opt/mcp/auth-ui`，参见 [Auth UI 部署](https://github.com/Kfjie-MCP/auth-ui/blob/main/docs/deployment.md)。

## 配置

复制根目录 `.env.example` 为 `.env`，设置自己的 `PUBLIC_URL`。systemd 模板通过 `EnvironmentFile` 加载该文件。

`.env` 设为 `0600` 并归服务用户所有：`sudo chown mailbridge-mcp:mailbridge-mcp .env && chmod 600 .env`。Python SDK 也会读取工作目录中的 `.env`，仅允许 root 读取会导致启动失败。

| 配置 | 用途 |
| --- | --- |
| `PUBLIC_URL` | 公网 HTTPS origin，例如 `https://mcp.example.com`，不含服务路径 |
| `MAILBRIDGE_HOME` | 邮箱配置目录，服务模板使用 `/var/lib/mailbridge/.mailbridge` |
| `MAILBRIDGE_TOKEN_FILE` | HTTP 后端访问令牌文件 |
| `OAUTH_PASSWORD_FILE` | 授权页面的连接密码文件 |
| `UPSTREAM_TOKEN_FILE` | 网关访问邮件后端的令牌文件 |

模板中的令牌、连接密码和 OAuth 数据库均位于 `/var/lib/mailbridge`。创建专用用户 `mailbridge-mcp`，状态目录设为 `0700`，凭据文件设为 `0600`。远程访问令牌至少 32 字符。

## 安装

在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r deploy/requirements.txt
cd gateway
npm ci --omit=dev
npm run check
cd ..
```

交互式配置邮箱：

```bash
sudo -u mailbridge-mcp env HOME=/var/lib/mailbridge \
  MAILBRIDGE_HOME=/var/lib/mailbridge/.mailbridge \
  .venv/bin/python upstream/server/setup.py
```

安装服务与路由：

```bash
sudo install -m 0644 deploy/mailbridge-mcp.service /etc/systemd/system/
sudo install -m 0644 gateway/deploy/mailbridge-oauth.service /etc/systemd/system/
sudo install -m 0644 gateway/deploy/mailbridge-locations.conf /etc/nginx/snippets/
sudo systemctl daemon-reload
sudo systemctl enable --now mailbridge-mcp mailbridge-oauth
```

在自己的 Nginx HTTPS server 中引用上述路由片段和 Auth UI 静态资源片段，检查配置后 reload。后端监听 `127.0.0.1:18760`，网关监听 `127.0.0.1:4204`。

## 日期排序

搜索先对全部匹配邮件按实际时间排序，再应用数量限制；无有效 Date 头时使用 INTERNALDATE。支持 SORT 的邮箱优先使用服务器排序，其余邮箱只读获取日期元数据。

`date-index.sqlite3` 位于邮箱配置目录，仅缓存邮箱标识、UIDVALIDITY、UID 和时间戳，权限为 `0600`。重启后可复用，邮箱 UIDVALIDITY 变化时失效。邮件正文不进入此索引。

## 运行检查

```bash
systemctl status mailbridge-mcp mailbridge-oauth
curl -fsS http://127.0.0.1:4204/healthz
```

修改邮箱配置后重启邮件后端。连接密码与邮箱应用授权码用途不同，客户端只需要前者。
