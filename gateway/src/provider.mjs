import { randomBytes, createHash, timingSafeEqual } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { mkdirSync, chmodSync, existsSync } from "node:fs";
import { dirname } from "node:path";
import { pathToFileURL } from "node:url";
import { InvalidGrantError, InvalidRequestError, InvalidScopeError, InvalidTokenError } from "@modelcontextprotocol/sdk/server/auth/errors.js";

const productionRenderer = "/opt/mcp/auth-ui/server/render.mjs";
const rendererUrl = process.env.AUTH_UI_RENDERER
  ? pathToFileURL(process.env.AUTH_UI_RENDERER)
  : existsSync(productionRenderer)
    ? pathToFileURL(productionRenderer)
    : new URL("../../mcp-auth-ui/server/render.mjs", import.meta.url);
const { sendAuthorizationPage } = await import(rendererUrl.href);

export const SCOPE = "mail:full";
const now = () => Math.floor(Date.now() / 1000);
const random = () => randomBytes(32).toString("base64url");
const hash = value => createHash("sha256").update(value).digest("hex");
const equal = (a, b) => {
  const left = Buffer.from(a || ""), right = Buffer.from(b || "");
  return left.length === right.length && timingSafeEqual(left, right);
};

export class OwnerOAuthProvider {
  constructor({ database, password, directToken, resource, issuer }) {
    this.password = password;
    this.directToken = directToken;
    this.resource = resource;
    this.issuer = issuer;
    mkdirSync(dirname(database), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(database);
    chmodSync(database, 0o600);
    this.db.exec("PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS oauth (kind TEXT, key TEXT, value TEXT, expires INTEGER, PRIMARY KEY(kind,key))");
    this.clientsStore = {
      getClient: async id => this.get("client", id),
      registerClient: async client => { this.put("client", client.client_id, client); return client; },
    };
  }

  get(kind, key) {
    const row = this.db.prepare("SELECT value, expires FROM oauth WHERE kind=? AND key=?").get(kind, key);
    return row && (!row.expires || row.expires > now()) ? JSON.parse(row.value) : undefined;
  }
  put(kind, key, value, expires = 0) {
    this.db.prepare("DELETE FROM oauth WHERE expires>0 AND expires<=?").run(now());
    this.db.prepare("INSERT OR REPLACE INTO oauth VALUES (?,?,?,?)").run(kind, key, JSON.stringify(value), expires);
  }
  remove(kind, key) { this.db.prepare("DELETE FROM oauth WHERE kind=? AND key=?").run(kind, key); }
  close() { this.db.close(); }
  checkResource(resource) {
    if (resource && resource.toString() !== this.resource) throw new InvalidRequestError("Unexpected MCP resource");
  }
  checkScopes(scopes = [SCOPE]) {
    if (scopes.some(scope => scope !== SCOPE)) throw new InvalidScopeError("Only mail:full is supported");
    return scopes.length ? scopes : [SCOPE];
  }

  async authorize(client, params, res) {
    this.checkResource(params.resource);
    const scopes = this.checkScopes(params.scopes);
    const request = random();
    const requestExpiresAt = now() + 600;
    this.put("request", request, { ...params, resource: this.resource, scopes, clientId: client.client_id, clientName: client.client_name || "MCP 客户端", requestExpiresAt }, requestExpiresAt);
    return this.renderAuthorization(request, res);
  }

  renderAuthorization(request, res, error = null) {
    const grant = this.get("request", request);
    return sendAuthorizationPage(res, {
      service: "mailbridge", request: grant ? request : "",
      clientName: grant?.clientName || "MCP 客户端",
      redirectUri: grant?.redirectUri,
      expiresAt: grant?.requestExpiresAt || 0,
      error: grant ? error : "request_expired",
    });
  }

  approve(request, password) {
    const grant = this.get("request", request);
    if (!grant) return { error: "request_expired" };
    if (!equal(password.trim(), this.password)) return { error: "password_incorrect" };
    if (grant.approvedRedirect && this.get("code", hash(new URL(grant.approvedRedirect).searchParams.get("code")))) {
      return { redirect: grant.approvedRedirect, repeated: true };
    }
    const code = random();
    this.put("code", hash(code), { ...grant, request }, now() + 300);
    const redirect = new URL(grant.redirectUri);
    redirect.searchParams.set("code", code);
    if (grant.state !== undefined) redirect.searchParams.set("state", grant.state);
    redirect.searchParams.set("iss", this.issuer);
    this.put("request", request, { ...grant, approvedRedirect: redirect.href }, grant.requestExpiresAt || now() + 600);
    return { redirect: redirect.href };
  }

  grantForCode(client, code) {
    const grant = this.get("code", hash(code));
    if (!grant || grant.clientId !== client.client_id) throw new InvalidGrantError("Invalid or expired authorization code");
    return grant;
  }
  async challengeForAuthorizationCode(client, code) { return this.grantForCode(client, code).codeChallenge; }
  async exchangeAuthorizationCode(client, code, _verifier, redirectUri, resource) {
    const grant = this.grantForCode(client, code);
    this.checkResource(resource);
    if (redirectUri !== grant.redirectUri) throw new InvalidGrantError("redirect_uri does not match authorization");
    this.remove("code", hash(code));
    if (grant.request) this.remove("request", grant.request);
    console.info(JSON.stringify({ event: "mailbridge_oauth_code_exchange", outcome: "success" }));
    return this.issueTokens(grant);
  }
  issueTokens(grant) {
    const access = random(), refresh = random();
    const info = { clientId: grant.clientId, scopes: grant.scopes, resource: this.resource };
    this.put("access", hash(access), { ...info, expiresAt: now() + 3600 }, now() + 3600);
    this.put("refresh", hash(refresh), info, now() + 30 * 86400);
    return { access_token: access, refresh_token: refresh, token_type: "Bearer", expires_in: 3600, scope: info.scopes.join(" ") };
  }
  async exchangeRefreshToken(client, token, scopes, resource) {
    const grant = this.get("refresh", hash(token));
    if (!grant || grant.clientId !== client.client_id) throw new InvalidGrantError("Invalid or expired refresh token");
    this.checkResource(resource);
    if (scopes && scopes.some(scope => !grant.scopes.includes(scope))) throw new InvalidScopeError("Refresh cannot add permissions");
    this.remove("refresh", hash(token));
    return this.issueTokens({ ...grant, scopes: scopes ?? grant.scopes });
  }
  async verifyAccessToken(token) {
    if (equal(token, this.directToken)) return { token, clientId: "owner", scopes: [SCOPE], resource: this.resource, expiresAt: now() + 3600 };
    const info = this.get("access", hash(token));
    if (!info || info.resource !== this.resource) throw new InvalidTokenError("Invalid or expired access token");
    return { ...info, token };
  }
  async revokeToken(client, { token }) {
    for (const kind of ["access", "refresh"]) {
      const grant = this.get(kind, hash(token));
      if (grant?.clientId === client.client_id) this.remove(kind, hash(token));
    }
  }
}
