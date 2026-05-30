# Web Deployment

The web surface is now a token-protected AutoTest session file browser. It
keeps the previous FastAPI/Uvicorn service and tray helpers, but camera
publishing and MJPEG streaming are disabled.

## Run Locally

Install dependencies:

```powershell
uv sync
```

Start the web server:

```powershell
$env:SEMI_AUTO_PROBE_WEB_TOKEN="change-this-token"
$env:SEMI_AUTO_PROBE_AUTOTEST_SESSION_DIR="D:\Project\semi-auto-probe\autotest_session"
.\.venv\Scripts\python.exe -m semi_auto_probe.web_app
```

The web service writes its current process ID to:

```text
D:\Project\semi-auto-probe\.runtime\semi-auto-probe-web.pid
```

Open:

```text
http://127.0.0.1:8000
```

The page accepts the token in the UI, in `?token=...`, or through the
`X-Access-Token` HTTP header for API callers.

## Tray GUI

For a fully hidden restart with no PowerShell window, double-click:

```text
D:\Project\semi-auto-probe\src\semi_auto_probe\web\restart_web_silent.vbs
```

For a persistent tray icon with right-click controls, double-click:

```text
D:\Project\semi-auto-probe\src\semi_auto_probe\web\web_tray_silent.vbs
```

The tray menu supports:

- Open Dashboard
- AutoTest Sessions
- Restart Web Service
- Stop Running
- Web Settings > Update Token
- Web Settings > Check Connections

Restart logs are written to:

```text
D:\Project\semi-auto-probe\.runtime\restart_web.log
```

Tray logs are written to:

```text
D:\Project\semi-auto-probe\.runtime\web_tray.log
```

## LAN Binding

The server listens on `127.0.0.1` by default. To listen on all LAN interfaces:

```powershell
$env:SEMI_AUTO_PROBE_WEB_HOST="0.0.0.0"
.\.venv\Scripts\python.exe -m semi_auto_probe.web_app
```

## Fixed Public Link With Cloudflare Tunnel

Cloudflare Tunnel remains the recommended option for a stable external URL
because the local machine does not need a public IP or router port forwarding.

1. Install `cloudflared` on the acquisition PC.
2. Log in:

   ```powershell
   cloudflared tunnel login
   ```

3. Create a tunnel:

   ```powershell
   cloudflared tunnel create semi-auto-probe
   ```

4. Route a fixed hostname to the tunnel:

   ```powershell
   cloudflared tunnel route dns semi-auto-probe probe.example.com
   ```

5. Create a Cloudflare tunnel config, usually at
   `%USERPROFILE%\.cloudflared\config.yml`:

   ```yaml
   tunnel: semi-auto-probe
   credentials-file: C:\Users\YOUR_USER\.cloudflared\TUNNEL_ID.json

   ingress:
     - hostname: probe.example.com
       service: http://127.0.0.1:8000
     - service: http_status:404
   ```

6. Start the local web app and tunnel:

   ```powershell
   $env:SEMI_AUTO_PROBE_WEB_TOKEN="change-this-token"
   $env:SEMI_AUTO_PROBE_AUTOTEST_SESSION_DIR="D:\Project\semi-auto-probe\autotest_session"
   .\.venv\Scripts\python.exe -m semi_auto_probe.web_app
   cloudflared tunnel run semi-auto-probe
   ```

External users can then open:

```text
https://probe.example.com
```

See [`../src/semi_auto_probe/web/README.md`](../src/semi_auto_probe/web/README.md)
for browser and external-code access examples.

## Safety Notes

- Always set `SEMI_AUTO_PROBE_WEB_TOKEN` before exposing the app externally.
- The web page is read-only and does not include motion-control or emergency-stop controls.
- File access is constrained to `SEMI_AUTO_PROBE_AUTOTEST_SESSION_DIR`.
- Camera frames are not published through the web service.
- Keep emergency stop access physically available near the machine while the probe station is operating.
