"""Read-only VISA discovery, run in a separate process from measurement sessions."""
from __future__ import annotations

import json
import sys


def scan_visa(addresses=(), *, manager_factory=None):
    rows = []
    manager = None
    try:
        if manager_factory is None:
            import pyvisa
            manager_factory = pyvisa.ResourceManager
        manager = manager_factory()
        resources = set(addresses)
        try:
            resources.update(manager.list_resources())
        except Exception as exc:
            rows.append(dict(device="VISA discovery", address="", status="Error", detail=str(exc)))
        for address in sorted(resources):
            # Do not send SCPI to serial controllers or arbitrary sockets.
            if not address.upper().startswith(("GPIB", "USB", "TCPIP")) or not address.upper().endswith("::INSTR"):
                continue
            row = dict(device="VISA instrument", address=address, status="Connection failed", detail="")
            session = None
            try:
                session, _ = manager.open_bare_resource(address, open_timeout=1500)
                row.update(status="Session open; identity unverified")
                try:
                    from pyvisa import constants
                    manager.visalib.set_attribute(session, constants.ResourceAttribute.timeout_value, 1500)
                    manager.visalib.write(session, b"*IDN?\n")
                    payload, _ = manager.visalib.read(session, 4096)
                    identity = payload.decode("ascii", errors="replace").strip()
                    if identity:
                        row.update(device=identity, status="Connected", detail="Identity query succeeded")
                    else:
                        row["detail"] = "Empty identity response"
                except Exception as exc:
                    row["detail"] = str(exc)
            except Exception as exc:
                row["detail"] = str(exc)
            finally:
                if session is not None:
                    try:
                        manager.visalib.close(session)
                    except Exception as exc:
                        row.update(status="Close failed", detail=f"{row['detail']}; {exc}")
            rows.append(row)
    except Exception as exc:
        rows.append(dict(device="VISA backend", address="", status="Unavailable", detail=str(exc)))
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception as exc:
                rows.append(dict(device="VISA backend", address="", status="Close failed", detail=str(exc)))
    return rows


if __name__ == "__main__":
    print(json.dumps(scan_visa(sys.argv[1:]), ensure_ascii=True))
