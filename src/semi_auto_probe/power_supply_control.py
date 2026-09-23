"""Manual DC supplies: model-specific limits and confirmed status, no synthetic readings."""
from dataclasses import dataclass
import math
import time
from .dp100 import DP100Setpoint
from .hp6614c import verify_hp6614c_identity

DP100_ADDRESS = 'USB / vendor DLL'


def supply_kind(identity):
    if identity.strip().upper() in {'DP100', 'ATK-DP100', 'ALIENTEK DP100'}:
        return 'dp100'
    try:
        verify_hp6614c_identity(identity)
        return 'hp6614c'
    except RuntimeError:
        return None


@dataclass(frozen=True)
class SupplySettings:
    kind: str
    value: float = 0
    limit: float = .01
    ovp: float = 3.3
    ocp: float = .025
    preset: int = 1
    ocp_enabled: bool = False
    measure_range: float = .5
    mode: str = 'voltage'

    def validate(self):
        if self.kind not in {'dp100', 'hp6614c'}:
            raise ValueError('Unsupported power supply.')
        if not all(math.isfinite(v) for v in (self.value, self.limit, self.ovp, self.ocp, self.measure_range)):
            raise ValueError('Supply settings must be finite.')
        vmax, imax = (30, 5) if self.kind == 'dp100' else (100, .5)
        if not 0 <= self.value <= vmax or not 0 < self.limit <= imax:
            raise ValueError(f'Supply voltage: 0–{vmax} V; current limit: >0–{imax} A.')
        if not self.value <= self.ovp <= (30.5 if self.kind == 'dp100' else 110) or self.ovp <= 0:
            raise ValueError('OVP must be positive, within the instrument limit and at least the set voltage.')
        if self.kind == 'dp100':
            if not self.limit <= self.ocp <= 5.05:
                raise ValueError('DP100 OCP must be between the current limit and 5.05 A.')
            if self.value * self.limit > 100:
                raise ValueError('DP100 requested voltage × current limit exceeds 100 W.')
            if self.preset not in range(1, 10):
                raise ValueError('DP100 writable presets are 1–9.')
            for v in (self.value, self.limit, self.ovp, self.ocp):
                if not math.isclose(v * 1000, round(v * 1000), abs_tol=1e-7):
                    raise ValueError('DP100 DLL settings use whole mV / mA; sub-milli values are unsupported.')
        elif self.measure_range not in {.02, .5}:
            raise ValueError('HP 6614C measurement ranges: 20 mA or 500 mA.')


class HP6614CManual:
    def __init__(self, instrument):
        self.instrument = instrument
        self.settings = None
        self.readback = {}

    def identify(self):
        identity = self.instrument.query('*IDN?').strip()
        verify_hp6614c_identity(identity)
        return identity

    def _number(self, command):
        raw = self.instrument.query(command).strip()
        try:
            value = float(raw)
        except ValueError as exc:
            raise RuntimeError(f'Invalid HP readback for {command}: {raw}') from exc
        if not math.isfinite(value) or abs(value) >= 9e36:
            raise RuntimeError(f'Invalid/overrange reading for {command}')
        return value

    def _error(self):
        error = self.instrument.query('SYST:ERR?').strip()
        if int(error.split(',')[0]) != 0:
            raise RuntimeError(f'HP 6614C error: {error}')

    def _write(self, command):
        self.instrument.write(command)
        self._error()

    def output_state(self):
        raw = self.instrument.query('OUTP?').strip().upper()
        if raw not in {'0', '1', 'OFF', 'ON'}:
            raise RuntimeError(f'Unknown HP output state: {raw}')
        enabled = raw in {'1', 'ON'}
        operation = int(self._number('STAT:OPER:COND?'))
        questionable = int(self._number('STAT:QUES:COND?'))
        self.readback = dict(output=enabled, voltage=self._number('VOLT?'),
            current=self._number('CURR?'), ovp=self._number('VOLT:PROT?'),
            ocp_enabled=bool(self._number('CURR:PROT:STAT?')),
            measure_range=self._number('SENS:CURR:RANG?'),
            mode='CC-' if operation & 2048 else 'CC' if operation & 1024 else 'CV' if operation & 256 else 'Idle',
            protection=', '.join(name for bit, name in ((1,'OVP'),(2,'OCP'),(4,'Fuse'),(16,'OTP'),(512,'Inhibit'),(1024,'Unregulated'),(16384,'Overrange')) if questionable & bit) or 'Normal')
        return enabled

    def apply(self, settings):
        settings.validate()
        if settings.kind != 'hp6614c':
            raise ValueError('Incorrect supply settings.')
        if self.output_state():
            raise ValueError('Turn output OFF before applying supply parameters.')
        self._error()
        self.settings = None
        for cmd in ('VOLT 0', f'VOLT:PROT {settings.ovp:.12g}', f'CURR {settings.limit:.12g}',
                    f'CURR:PROT:STAT {int(settings.ocp_enabled)}',
                    f'SENS:CURR:RANG {settings.measure_range:.12g}', f'VOLT {settings.value:.12g}'):
            self._write(cmd)
        self.output_state()
        for key, expected, tolerance in (('voltage',settings.value,.03),('current',settings.limit,.0002),
                ('ovp',settings.ovp,.03)):
            if not math.isclose(self.readback[key], expected, rel_tol=.002, abs_tol=tolerance):
                raise RuntimeError(f'HP {key} readback mismatch: {self.readback[key]} vs {expected}')
        actual_range = self.readback['measure_range']
        # RANG? returns the selected range maximum, not the supplied selection
        # value: the nominal 500 mA high range can report 0.5118 A.
        range_matches = (.019 <= actual_range <= .021 if settings.measure_range == .02
                         else .49 <= actual_range <= .52)
        if not range_matches:
            raise RuntimeError(f'HP current range readback mismatch: {actual_range}')
        if self.readback['ocp_enabled'] != settings.ocp_enabled:
            raise RuntimeError('HP OCP enable readback mismatch.')
        self.settings = settings

    def output(self, enabled):
        if enabled and self.settings is None:
            raise ValueError('Apply supply settings first.')
        self._write('OUTP ON' if enabled else 'OUTP OFF')
        actual = self.output_state()
        if actual != enabled:
            raise RuntimeError('HP output state did not match command.')
        return actual

    def clear_protection(self):
        self.output(False)
        self._write('OUTP:PROT:CLE')
        self.output_state()

    def measure(self):
        readings = (self._number('MEAS:VOLT?'), self._number('MEAS:CURR?'))
        self._error()
        return readings

    def close(self):
        self.instrument.close()


class DP100Manual:
    def __init__(self, driver):
        self.driver = driver
        self.settings = None
        self.readback = {}

    def identify(self):
        if not self.driver.connected:
            raise RuntimeError('DP100 USB connection is not open.')
        self.output_state()
        return 'ALIENTEK DP100'

    def output_state(self):
        self.readback = self.driver.read_settings()
        return self.readback['output']

    def _confirm(self, enabled, settings=None):
        deadline = time.monotonic() + 1.5
        while True:
            actual = self.output_state()
            matching = actual == enabled
            if settings is not None:
                matching = matching and self.readback['preset'] == settings.preset and all(
                    abs(self.readback[key] - value) <= .00051 for key, value in
                    (('voltage',settings.value),('current',settings.limit),('ovp',settings.ovp),('ocp',settings.ocp)))
            if matching:
                return actual
            if time.monotonic() >= deadline:
                raise RuntimeError('DP100 output/settings readback did not match the command.')
            time.sleep(.05)

    def apply(self, settings):
        settings.validate()
        if settings.kind != 'dp100':
            raise ValueError('Incorrect supply settings.')
        if self.output_state():
            raise ValueError('Turn output OFF before applying supply parameters.')
        self.settings = None
        point = DP100Setpoint(settings.value, settings.limit, settings.preset)
        self.driver.configure_limits(point, ovp_v=settings.ovp, ocp_a=settings.ocp)
        self.driver.use_preset(point, ovp_v=settings.ovp, ocp_a=settings.ocp)
        self.driver.set_output(point, False)
        self._confirm(False, settings)
        self.settings = settings

    def output(self, enabled):
        if enabled:
            if self.settings is None:
                raise ValueError('Apply supply settings first.')
            s = self.settings
            point = DP100Setpoint(s.value, s.limit, s.preset)
        else:
            # Attempt OFF even if a fresh status query cannot complete. Confirmation
            # afterwards must still succeed before ownership can be released.
            point = DP100Setpoint(self.readback.get('voltage', 0), self.readback.get('current', 0), self.readback.get('preset', 1))
        self.driver.set_output(point, enabled)
        return self._confirm(enabled)

    def measure(self):
        reading = self.driver.read_output()
        values = (reading.voltage_v, reading.current_a)
        if not all(math.isfinite(v) for v in values):
            raise RuntimeError('Invalid DP100 measurement.')
        return values

    def close(self):
        # Shared application USB connection remains connected after manual release.
        pass
