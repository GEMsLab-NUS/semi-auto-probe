# Raspberry Pi exclusive serial bridge implementation prompt

Use the following prompt with the agent that maintains the Raspberry Pi program.

---

You are modifying the Raspberry Pi motor-controller service at:

- source: `/home/icalculate/serial-web/app.py`
- systemd unit: `serial-web.service`
- physical serial controller: `115200 8N1`, fixed 12-byte frames
- SSH-only raw TCP bridge: `127.0.0.1:9500`

Inspect the current implementation before editing it. Preserve the existing web UI, keyboard controls, serial auto-detection, physical emergency-stop override, and raw unframed TCP byte stream. Do not introduce a second process that opens the serial device; the existing service must remain its sole owner.

Implement strict remote-exclusive ownership with these semantics:

1. A successfully accepted bridge connection has higher priority than every Raspberry Pi local control path.
2. Before publishing the remote session as active:
   - atomically reserve the single remote-session slot;
   - stop all axes once as a safe ownership boundary;
   - send the controller command that disables realtime position upload;
   - allow/drain any already queued realtime-position frames so none are delivered as the first remote response;
   - only then attach the TCP connection to serial RX forwarding.
3. While the remote session is active:
   - reject web UI, keyboard, local automation, polling, and other locally originated serial writes;
   - do not enable realtime position upload or issue local status/read commands;
   - forward remote TCP bytes to serial and serial RX bytes to TCP without adding headers, status bytes, framing, acknowledgements, or authentication;
   - the only local-control exception is the physical emergency stop, which must always override remote control immediately;
   - monitoring may observe data but must never inject serial commands.
4. On remote disconnect, SSH loss, socket reset, or keepalive expiry:
   - stop all axes once;
   - tear down and clear the remote owner atomically;
   - restore the local controller mode, including realtime position upload if the local UI requires it;
   - resume keyboard/web/local automation only after remote ownership is fully released.
5. Enforce exactly one remote session:
   - protect ownership with a lock;
   - keep the accept loop able to accept new sockets while one handler owns the active session;
   - immediately close a second connection when the slot is occupied;
   - do not leave the second connection queued in the TCP listen backlog;
   - do not write an error payload because the bridge protocol is raw controller bytes.
6. Preserve loopback-only binding on `127.0.0.1:9500`; SSH remains the only authentication layer.

Add a read-only status endpoint used through SSH by the Windows upper-computer:

`GET /api/bridge/status`

Return JSON with this stable schema:

```json
{
  "service_ready": true,
  "serial_connected": true,
  "serial_port": "/dev/ttyUSB0",
  "bridge_active": false,
  "available": true,
  "control_owner": "local",
  "realtime_upload_enabled": true
}
```

Contract:

- `available` is true only when the service is ready, the physical motor serial port is connected, and no remote session is active.
- `control_owner` is exactly `none`, `local`, or `remote`.
- During a remote session, `bridge_active=true`, `available=false`, `control_owner="remote"`, and `realtime_upload_enabled=false`.
- The endpoint must only inspect state. It must not open TCP port 9500, take ownership, write to serial, or change controller state.
- Keep the existing `/api/status` endpoint compatible.

Fix the current accept-loop structure if it handles the active client synchronously. The `bridge_conn is not None` rejection branch must be reachable while a session is active. Use a dedicated session thread plus an ownership lock, or an equivalent race-free design.

Add focused tests or a deterministic harness covering:

- local mode enables realtime position upload;
- remote attach disables realtime upload before RX forwarding starts;
- no stale `A3 CB` realtime-position frame is forwarded ahead of the remote communication-test response;
- local web/keyboard writes are rejected during remote ownership;
- physical emergency stop remains allowed;
- a second remote socket is closed immediately;
- disconnect restores local mode and realtime upload;
- `/api/bridge/status` reports every ownership transition correctly;
- abrupt SSH/socket loss releases ownership within the configured keepalive bound.

After implementation:

1. Run the tests.
2. Restart `serial-web.service` once.
3. Confirm `/dev/serial/by-id/usb-1a86_USB2.0-Ser_-if00-port0` resolves to the selected serial device; prefer this stable by-id path over a volatile `/dev/ttyUSBN` name where practical.
4. Verify the status endpoint locally with `curl http://127.0.0.1:5000/api/bridge/status`.
5. Verify one remote communication-test frame returns `A3 AA ...` without an earlier unsolicited `A3 CB` frame.
6. Report changed files, test output, and the exact ownership transition sequence.

Do not change the controller wire protocol, baud rate, SSH exposure, or emergency-stop priority.

---
