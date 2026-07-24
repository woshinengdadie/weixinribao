const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

// Wrangler 配置文件：优先标准路径，Windows 兼容
const possiblePaths = [
  path.join(process.env.USERPROFILE || "~", ".wrangler", "config", "default.toml"),
  path.join(process.env.APPDATA || "", "xdg.config", ".wrangler", "config", "default.toml"),
];
let configPath = null;
for (const p of possiblePaths) {
  if (fs.existsSync(p)) { configPath = p; break; }
}
if (!configPath) {
  console.error("ERROR: Wrangler config not found. Tried:", possiblePaths);
  process.exit(1);
}
const config = fs.readFileSync(configPath, "utf8");
const match = config.match(/oauth_token\s*=\s*"([^"]+)"/);
if (!match) {
  console.error("ERROR: oauth_token not found in Wrangler config:", configPath);
  process.exit(1);
}
const token = match[1];

const https = require("https");

// First delete the old secret
function delSecret() {
  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: "api.cloudflare.com",
      path: "/client/v4/accounts/57e70efad14aae4be63ab7b547bcec37/workers/scripts/wechat-license/secrets/ADMIN_SECRET",
      method: "DELETE",
      headers: { "Authorization": "Bearer " + token }
    }, res => { let d=""; res.on("data",c=>d+=c); res.on("end",()=>resolve(JSON.parse(d))); });
    req.on("error", reject);
    req.end();
  });
}

function setSecret(val) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ name: "ADMIN_SECRET", value: val, type: "secret_text" });
    const req = https.request({
      hostname: "api.cloudflare.com",
      path: "/client/v4/accounts/57e70efad14aae4be63ab7b547bcec37/workers/scripts/wechat-license/secrets",
      method: "PUT",
      headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) }
    }, res => { let d=""; res.on("data",c=>d+=c); res.on("end",()=>resolve(JSON.parse(d))); });
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

async function main() {
  // 从环境变量 ADMIN_SECRET 读取，不再硬编码
  const val = process.env.ADMIN_SECRET;
  if (!val) {
    console.error("ERROR: ADMIN_SECRET env var not set.");
    console.error("Usage: $env:ADMIN_SECRET='your-secret'; node set_secret.js");
    process.exit(1);
  }
  if (val.length < 12) {
    console.error("ERROR: ADMIN_SECRET too short (min 12 chars)");
    process.exit(1);
  }
  console.log("Setting ADMIN_SECRET, length:", val.length);
  await delSecret().catch(() => {});
  const r = await setSecret(val);
  console.log("Result:", JSON.stringify(r));
}

main();
