// 重新设置 ADMIN_SECRET 的简单方式
const postData = JSON.stringify({ name: "ADMIN_SECRET", value: "MySecret2026", type: "secret_text" });

const options = {
  hostname: "api.cloudflare.com",
  path: "/client/v4/accounts/57e70efad14aae4be63ab7b547bcec37/workers/scripts/wechat-license/secrets",
  method: "PUT",
  headers: {
    "Authorization": "Bearer " + require("fs").readFileSync(require("path").join(process.env.APPDATA, "xdg.config", ".wrangler", "config", "default.toml"), "utf8").match(/oauth_token\s*=\s*"([^"]+)"/)[1],
    "Content-Type": "application/json",
    'Content-Length': Buffer.byteLength(postData)
  }
};

const req = require("https").request(options, (r) => { let d = ""; r.on("data", c => d += c); r.on("end", () => console.log(d)); });
req.write(postData);
req.end();
