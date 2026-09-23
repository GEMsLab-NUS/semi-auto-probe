from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


logger = logging.getLogger("semi_auto_probe")


# Default supply profile for the user's 3–5 V, <30 mA, <5 mW laser module.
# Electrical current is a limit, not a calibrated optical-power setpoint.
DEFAULT_LASER_PARAMS = {
    "peak_voltage_v": "3.0",
    "peak_current_a": "0.010",
    "ovp_v": "3.3",
    "ocp_a": "0.025",
}


@dataclass(frozen=True)
class DP100Setpoint:
    voltage_v: float
    current_a: float
    preset_index: int = 1

    def normalized(self) -> "DP100Setpoint":
        voltage_v = float(self.voltage_v)
        current_a = float(self.current_a)
        preset_index = int(self.preset_index)
        if not 0 <= voltage_v <= 65.535:
            raise ValueError("DP100 voltage must be between 0 and 65.535 V.")
        if not 0 <= current_a <= 65.535:
            raise ValueError("DP100 current must be between 0 and 65.535 A.")
        if not 0 <= preset_index <= 9:
            raise ValueError("DP100 preset index must be between 0 and 9.")
        return DP100Setpoint(voltage_v, current_a, preset_index)

    @property
    def voltage_mv(self) -> int:
        return round(self.normalized().voltage_v * 1000)

    @property
    def current_ma(self) -> int:
        return round(self.normalized().current_a * 1000)


@dataclass(frozen=True)
class DP100Reading:
    voltage_v: float
    current_a: float


class DP100Driver:
    """Thin pythonnet adapter for the vendor's managed ATK-DP100 DLL."""

    def __init__(self, dll_path: str | Path | None = None) -> None:
        path = Path(dll_path or Path.cwd() / "refs" / "ATK-DP100DLL(x64).dll").resolve()
        if not path.is_file():
            raise FileNotFoundError(f"ATK-DP100 DLL not found: {path}")
        try:
            import clr  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("DP100 control requires pythonnet on Windows (pip install pythonnet).") from exc
        clr.AddReference(str(path))
        _silence_dotnet_console()
        from ATK_DP100DLL import ATKDP100API  # type: ignore[import-not-found]

        self._api = ATKDP100API()
        self._reading_callback_type = ATKDP100API.ReceBasicInfo

    @property
    def connected(self) -> bool:
        return bool(self._api.ConnState)

    def connect(self) -> bool:
        connected = self.connected or bool(self._api.DevOpenOrClose())
        logger.info("DP100 %s; vendor hexadecimal traffic is hidden.", "connected" if connected else "connection failed")
        return connected

    def disconnect(self) -> None:
        if self.connected:
            self._api.DevOpenOrClose()
            logger.info("DP100 disconnected.")

    def set_output(self, setpoint: DP100Setpoint, enabled: bool) -> None:
        value = setpoint.normalized()
        if enabled and (value.voltage_mv == 0 or value.current_ma == 0):
            raise ValueError("DP100 output requires non-zero voltage and current settings.")
        method = self._api.OpenOut if enabled else self._api.CloseOut
        if not bool(method(value.preset_index, value.current_ma, value.voltage_mv)):
            raise RuntimeError("ATK-DP100 rejected the output command.")
        logger.debug(
            "DP100 %s%s.",
            "output ON, " if enabled else "output OFF",
            f"voltage {value.voltage_mv / 1000:g} V, current {value.current_ma:g} mA" if enabled else "",
        )

    def configure_limits(self, setpoint: DP100Setpoint, *, ovp_v: float, ocp_a: float) -> None:
        value = setpoint.normalized()
        ovp_mv = _uint16_si(ovp_v, 1000, "OVP voltage")
        ocp_ma = _uint16_si(ocp_a, 1000, "OCP current")
        if value.preset_index == 0:
            raise ValueError("DP100 preset 0 is read-only; use preset 1-9 for protection settings.")
        if not bool(self._api.SetGroupInfo(value.preset_index, value.current_ma, value.voltage_mv, ocp_ma, ovp_mv)):
            raise RuntimeError("ATK-DP100 rejected the protection settings.")
        logger.debug("DP100 protection limits: OVP %g V, OCP %g mA.", ovp_mv / 1000, ocp_ma)

    def read_output(self, timeout: float = 1.0) -> DP100Reading:
        """Request one measured voltage/current callback from the vendor DLL."""
        received = threading.Event()
        values: list[tuple[int, int]] = []

        def receive(voltage_mv, current_ma):
            values.append((int(voltage_mv), int(current_ma)))
            received.set()

        callback = self._reading_callback_type(receive)
        self._api.ReceBasicInfoEvent = callback
        try:
            if not bool(self._api.GetBasicInfo()):
                raise RuntimeError("ATK-DP100 rejected the voltage/current read request.")
            if not received.wait(max(0.01, float(timeout))):
                raise TimeoutError("Timed out waiting for ATK-DP100 voltage/current readback.")
        finally:
            # Keep the managed delegate alive until the next assignment; the DLL
            # event property does not reliably accept -= through pythonnet.
            self._reading_callback = callback
        voltage_mv, current_ma = values[-1]
        logger.debug("DP100 readback: %g V, %g mA.", voltage_mv / 1000, current_ma)
        return DP100Reading(voltage_mv / 1000.0, current_ma / 1000.0)

    def read_settings(self) -> dict:
        """GetCurrentBasic: index, output state, V/I setpoints, OVP/OCP (mV/mA)."""
        if not self.connected:
            raise RuntimeError('DP100 disconnected.')
        result = self._api.GetCurrentBasic(0, 0, 0, 0, 0, 0)
        if not result[0]:
            raise RuntimeError('DP100 status readback failed.')
        _, preset, state, voltage, current, ovp, ocp = result
        return dict(preset=int(preset), output=int(state) > 0, voltage=float(voltage) / 1000,
                    current=float(current) / 1000, ovp=float(ovp) / 1000, ocp=float(ocp) / 1000)

    def use_preset(self, setpoint: DP100Setpoint, *, ovp_v: float, ocp_a: float) -> None:
        value = setpoint.normalized()
        if not self._api.UseGroup(value.preset_index, value.current_ma, value.voltage_mv,
                                  _uint16_si(ocp_a, 1000, 'OCP'), _uint16_si(ovp_v, 1000, 'OVP')):
            raise RuntimeError('DP100 rejected preset activation.')

    def shutdown(self) -> None:
        try:
            self.set_output(DP100Setpoint(0, 0, 1), False)
        finally:
            self.disconnect()

    def pulse(
        self,
        peak: DP100Setpoint,
        baseline: DP100Setpoint,
        *,
        delay_ms: float,
        width_ms: float,
        period_ms: float,
        count: int,
        final_enabled: bool = False,
        stop_event: threading.Event | None = None,
    ) -> None:
        width_s = max(0.0, float(width_ms) / 1000.0)
        period_s = max(width_s, float(period_ms) / 1000.0)
        wait = stop_event.wait if stop_event is not None else _sleep_wait
        if wait(max(0.0, float(delay_ms) / 1000.0)):
            return
        for _ in range(max(0, int(count))):
            self.set_output(peak, True)
            if wait(width_s):
                break
            self.set_output(baseline, baseline.voltage_v > 0 or baseline.current_a > 0)
            if wait(max(0.0, period_s - width_s)):
                break
        self.set_output(baseline, final_enabled)


class SimulatedDP100Driver:
    def __init__(self, on_change: Callable[[DP100Setpoint, bool], None] | None = None) -> None:
        self.connected = False
        self.on_change = on_change
        self.commands: list[tuple[DP100Setpoint, bool]] = []
        self.last_setpoint = DP100Setpoint(0, 0, 1)
        self.output_enabled = False

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def set_output(self, setpoint: DP100Setpoint, enabled: bool) -> None:
        value = setpoint.normalized()
        if enabled and (value.voltage_mv == 0 or value.current_ma == 0):
            raise ValueError("DP100 output requires non-zero voltage and current settings.")
        self.commands.append((value, bool(enabled)))
        self.last_setpoint, self.output_enabled = value, bool(enabled)
        if self.on_change is not None:
            self.on_change(value, bool(enabled))

    def configure_limits(self, setpoint: DP100Setpoint, *, ovp_v: float, ocp_a: float) -> None:
        setpoint.normalized()
        _uint16_si(ovp_v, 1000, "OVP voltage")
        _uint16_si(ocp_a, 1000, "OCP current")

    def read_output(self, timeout: float = 1.0) -> DP100Reading:
        del timeout
        if not self.output_enabled:
            return DP100Reading(0, 0)
        # Deterministic LED-like curve for workflow tests: 2.02 V at 1 mA,
        # increasing to 2.50 V at 25 mA.
        current = self.last_setpoint.current_a
        return DP100Reading(min(self.last_setpoint.voltage_v, 2.0 + 20.0 * current), current)

    def shutdown(self) -> None:
        self.set_output(DP100Setpoint(0, 0, 1), False)
        self.disconnect()


def _sleep_wait(seconds: float) -> bool:
    time.sleep(seconds)
    return False


def _silence_dotnet_console(console=None, text_writer=None) -> None:
    """Discard the vendor DLL's raw hexadecimal Console.WriteLine traffic."""
    try:
        if console is None or text_writer is None:
            from System import Console as console  # type: ignore[import-not-found,no-redef]
            from System.IO import TextWriter as text_writer  # type: ignore[import-not-found,no-redef]
        console.SetOut(text_writer.Null)
        console.SetError(text_writer.Null)
    except Exception:
        # Console redirection is cosmetic and must never prevent hardware use.
        logger.debug("Could not suppress the DP100 vendor DLL console output.", exc_info=True)


def _uint16_si(value: float, scale: int, label: str) -> int:
    converted = round(float(value) * scale)
    if not 0 <= converted <= 65535:
        raise ValueError(f"DP100 {label} is outside the UInt16 device range.")
    return converted
