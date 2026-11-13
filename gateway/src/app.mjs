import express from "express";
import { rateLimit } from "express-rate-limit";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createOAuthMetadata, mcpAuthMetadataRouter, getOAuthProtectedResourceMetadataUrl } from "@modelcontextprotocol/sdk/server/auth/router.js";
import { authorizationHandler } from "@modelcontextprotocol/sdk/server/auth/handlers/authorize.js";
import { tokenHandler } from "@modelcontextprotocol/sdk/server/auth/handlers/token.js";
import { clientRegistrationHandler } from "@modelcontextprotocol/sdk/server/auth/handlers/register.js";
import { revocationHandler } from "@modelcontextprotocol/sdk/server/auth/handlers/revoke.js";
import { requireBearerAuth } from "@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js";
import { OwnerOAuthProvider, SCOPE } from "./provider.mjs";

export function createGateway({ publicUrl, upstreamUrl, database, password, upstreamToken, directToken = upstreamToken }) {
  const resource = new URL("/mailbridge/mcp", publicUrl);
  const issuer = new URL("/mailbridge", publicUrl);
  const provider = new OwnerOAuthProvider({ database, password, directToken, resource: resource.href, issuer: issuer.href });
  const app = express();
  app.disable("x-powered-by");
  app.set("trust proxy", 1);
  const oauth = "/mailbridge/oauth";
  const metadata = createOAuthMetadata({ provider, issuerUrl: issuer, scopesSupported: [SCOPE] });
  for (const [key, path] of Object.entries({ authorization_endpoint: "authorize", token_endpoint: "token", registration_endpoint: "register", revocation_endpoint: "revoke" })) {
    metadata[key] = new URL(`${oauth}/${path}`, publicUrl).href;
  }
  metadata.authorization_response_iss_parameter_supported = true;
  metadata.revocation_endpoint_auth_methods_supported = ["client_secret_post", "none"];
  app.use(`${oauth}/authorize`, (req, res, next) => {
    const redirect = res.redirect.bind(res);
    res.redirect = (status, target) => {
      const url = new URL(typeof status === "string" ? status : target);
      url.searchParams.set("iss", issuer.href);
      return redirect(typeof status === "number" ? status : 302, url.href);
    };
    next();
  }, authorizationHandler({ provider }));
  app.post(`${oauth}/approve`, rateLimit({ windowMs: 15 * 60 * 1000, limit: 30 }), express.urlencoded({ extended: false, limit: "32kb" }), (req, res) => {
    res.set({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" });
    const result = provider.approve(String(req.body.request || ""), String(req.body.password || ""));
    console.info(JSON.stringify({ event: "mailbridge_oauth_approval", outcome: result.error || (result.repeated ? "reused" : "success") }));
    if (result.error === "password_incorrect") return provider.renderAuthorization(String(req.body.request || ""), res.status(401), "password_incorrect");
    if (result.error === "request_expired") return provider.renderAuthorization(String(req.body.request || ""), res.status(400), "request_expired");
    res.redirect(302, result.redirect);
  });
  app.use(`${oauth}/token`, tokenHandler({ provider }));
  app.use(`${oauth}/register`, rateLimit({ windowMs: 15 * 60 * 1000, limit: 100 }), clientRegistrationHandler({ clientsStore: provider.clientsStore, clientSecretExpirySeconds: 0 }));
  app.use(`${oauth}/revoke`, revocationHandler({ provider }));
  app.use(mcpAuthMetadataRouter({ oauthMetadata: metadata, resourceServerUrl: resource, scopesSupported: [SCOPE], resourceName: "MailBridge Email MCP" }));
  app.get("/.well-known/oauth-authorization-server/mailbridge", (_req, res) => res.json(metadata));
  app.get("/healthz", (_req, res) => res.json({ ok: true, service: "mailbridge-oauth" }));
  const authenticate = requireBearerAuth({ verifier: provider, requiredScopes: [SCOPE], resourceMetadataUrl: getOAuthProtectedResourceMetadataUrl(resource) });

  const proxy = async (req, res) => {
    const headers = {
      Accept: req.get("accept") || "application/json, text/event-stream",
      Authorization: `Bearer ${upstreamToken}`,
    };
    for (const name of ["content-type", "mcp-protocol-version", "mcp-session-id", "last-event-id"]) {
      if (req.get(name)) headers[name] = req.get(name);
    }
    try {
      const response = await fetch(new URL("/mcp", upstreamUrl), {
        method: req.method,
        headers,
        body: ["GET", "HEAD"].includes(req.method) ? undefined : (req.body === undefined ? undefined : JSON.stringify(req.body)),
        signal: AbortSignal.timeout(130000),
      });
      res.status(response.status);
      for (const name of ["content-type", "mcp-session-id", "mcp-protocol-version", "cache-control", "allow"]) {
        if (response.headers.has(name)) res.set(name, response.headers.get(name));
      }
      if (req.body?.method === "tools/list" && response.ok && response.headers.get("content-type")?.includes("application/json")) {
        const body = await response.json();
        for (const tool of body.result?.tools || []) {
          const securitySchemes = [{ type: "oauth2", scopes: [SCOPE] }];
          tool.securitySchemes = securitySchemes;
          tool._meta = { ...tool._meta, securitySchemes };
        }
        return res.json(body);
      }
      if (response.body) await pipeline(Readable.fromWeb(response.body), res);
      else res.end();
    } catch {
      if (!res.headersSent) res.status(502).json({ error: "mcp_upstream_unavailable" });
      else res.end();
    }
  };
  app.all(resource.pathname, authenticate, express.json({ limit: "256kb" }), proxy);
  return { app, provider };
}
