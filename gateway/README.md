# MailBridge OAuth gateway

OAuth 2.1 authentication gateway for the existing MailBridge Streamable HTTP service.

- Public MCP resource: `https://mcp.example.com/mailbridge/mcp`
- Issuer: `https://mcp.example.com/mailbridge`
- Scope: `mail:full`
- Local gateway: `127.0.0.1:4204`
- Private MailBridge upstream: `127.0.0.1:18760/mcp`

The gateway keeps the previous static bearer token working for existing clients. OAuth access tokens are stored as SHA-256 hashes in `/var/lib/mailbridge/oauth.sqlite`. The approval password and upstream bearer token stay in separate mode-0600 files.
