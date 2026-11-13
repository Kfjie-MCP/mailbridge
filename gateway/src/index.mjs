import { readFileSync } from "node:fs";
import { createGateway } from "./app.mjs";

const readSecret = (environment, fallback, label) => {
  const value = readFileSync(process.env[environment] || fallback, "utf8").trim();
  if (value.length < 24) throw new Error(`${label} is missing or too short`);
  return value;
};
const publicUrl = process.env.PUBLIC_URL;
if (!publicUrl) throw new Error("Set PUBLIC_URL in the server environment");
const password = readSecret("OAUTH_PASSWORD_FILE", "/var/lib/mailbridge/oauth-approval-password", "OAuth approval password");
const upstreamToken = readSecret("UPSTREAM_TOKEN_FILE", "/var/lib/mailbridge/mcp-bearer-token", "MailBridge upstream token");
const { app, provider } = createGateway({
  publicUrl,
  upstreamUrl: process.env.UPSTREAM_URL || "http://127.0.0.1:18760",
  database: process.env.OAUTH_DATABASE || "/var/lib/mailbridge/oauth.sqlite",
  password,
  upstreamToken,
});
const server = app.listen(Number(process.env.PORT || 4204), "127.0.0.1", () => console.log("MailBridge MCP OAuth gateway ready"));
const shutdown = () => server.close(() => { provider.close(); process.exit(0); });
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
