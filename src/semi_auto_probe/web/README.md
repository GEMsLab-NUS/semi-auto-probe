# Semi Auto Probe Web

这个目录包含 AutoTest session 的只读网页和 API。它复用原来的
FastAPI 网页服务和 `SEMI_AUTO_PROBE_WEB_TOKEN` token 机制，但不再提供
摄像头推流功能。

## 启动

在项目根目录启动本地网页服务：

```powershell
$env:SEMI_AUTO_PROBE_WEB_TOKEN="change-this-token"
$env:SEMI_AUTO_PROBE_AUTOTEST_SESSION_DIR="D:\Project\semi-auto-probe\autotest_session"
uv run semi-auto-probe-web
```

默认监听：

```text
http://127.0.0.1:8000
```

也可以继续使用托盘 GUI：

```text
D:\Project\semi-auto-probe\src\semi_auto_probe\web\web_tray_silent.vbs
```

托盘菜单可以打开网页、重启服务、修改 token、查看访问连接和查看
AutoTest session 概览。

## 外网访问

内网穿透继续指向本机服务即可，例如 Cloudflare Tunnel：

```yaml
ingress:
  - hostname: probe.example.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

浏览器访问方式：

```text
https://probe.example.com
```

页面右上角输入 `SEMI_AUTO_PROBE_WEB_TOKEN` 的值并保存。也可以临时通过
URL 传入 token：

```text
https://probe.example.com/?token=change-this-token
```

页面会把 token 保存到当前浏览器的 localStorage，并从地址栏移除 token。

## 外部代码访问

推荐使用 `X-Access-Token` 请求头：

```powershell
$headers = @{ "X-Access-Token" = "change-this-token" }
Invoke-RestMethod -Uri "https://probe.example.com/api/autotest/sessions" -Headers $headers
```

Python 示例：

```python
import requests

base_url = "https://probe.example.com"
token = "change-this-token"
headers = {"X-Access-Token": token}

sessions = requests.get(f"{base_url}/api/autotest/sessions", headers=headers, timeout=10).json()
latest_id = sessions["sessions"][0]["id"]
detail = requests.get(f"{base_url}/api/autotest/sessions/{latest_id}", headers=headers, timeout=10).json()
print(detail["summary"])
```

也支持查询参数 `?token=change-this-token`，用于 `<img>`、下载链接或不方便设置
header 的场景。

## API

- `GET /api/status`：服务状态、session 根目录、最近 session、请求计数。
- `GET /api/connections`：来源 IP、user-agent、请求量和文件下载量。
- `GET /api/autotest/sessions?limit=100`：AutoTest session 历史列表和聚合统计。
- `GET /api/autotest/sessions/latest`：最近一次 AutoTest session 摘要。
- `GET /api/autotest/sessions/{session_id}`：指定 session 的文件树、分类统计、JSON 元数据摘要。
- `GET /api/autotest/sessions/{session_id}/json/{file_path}`：读取某个 JSON 文件并返回解析后的内容。
- `GET /api/autotest/sessions/{session_id}/text/{file_path}`：预览 CSV/TXT/LOG 等文本文件。
- `GET /api/autotest/sessions/{session_id}/files/{file_path}`：下载或内联打开文件；加 `download=true` 会作为附件下载。

所有 API 都是只读接口，且文件路径会限制在配置的 AutoTest session 根目录内。

## CORS

服务端代码或脚本访问不需要 CORS。若要让另一个网页前端直接调用这些 API，
可在启动前设置允许来源：

```powershell
$env:SEMI_AUTO_PROBE_WEB_CORS_ORIGINS="https://example.com"
```

多个来源用逗号分隔。
