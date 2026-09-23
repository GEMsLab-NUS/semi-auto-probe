# ntfy notifications

Semi Auto Probe can publish background notifications through ntfy. Configure it
on **Settings > NTFY NOTIFICATIONS**.

The current local configuration uses:

- Server: https://ntfy.sh
- Topic: SAP_alerts
- ERROR / CRITICAL alerts: enabled
- Major completion alerts: enabled

ERROR and CRITICAL notifications are privacy-safe summaries. They contain the
event time and a prompt to inspect the local application log. Exception text,
device addresses, file paths, connection details, and measurement values are not
published.

A successful AutoTest sends its completion status and completed point count.
AutoTest failures already produce ERROR log records and therefore trigger the
generic error alert.

Notification HTTP requests run on a daemon worker queue and never block stage
motion, instrument acquisition, or the Tk UI. Delivery failures are written as
local WARNING records and do not recursively create new alerts.

Use **Send Test** after changing the server or topic. **Save ntfy** stores the
settings in probe_config.local.json.
