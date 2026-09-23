"""No hardware: fixed transport/ADC latency benchmark for the real manual worker.
Run: .venv/Scripts/python.exe scripts/benchmark_instrument_timing.py
"""
import sys
import time
import statistics
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_instrument_control import FakeVisa
from semi_auto_probe.instrument_control import ManualSession, Keithley2450Manual, ManualSettings, ManualSweep

class TimedVisa(FakeVisa):
    def __init__(self):
        super().__init__()
        self.starts = []
        self.nested = False
        self.round_trips = 0
    def query(self, command):
        if self.nested:
            return super().query(command)
        self.round_trips += 1
        time.sleep(.002)
        if command.startswith(':READ?'):
            self.starts.append(time.monotonic())
            time.sleep(.020)
        self.nested = True
        try:
            return super().query(command)
        finally:
            self.nested = False
    def write(self, command):
        time.sleep(.002)
        return super().write(command)

def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() > deadline: raise RuntimeError('Worker timeout')
        time.sleep(.001)

for mode in ('continuous', 'iv'):
    visa = TimedVisa()
    session = ManualSession(lambda _: Keithley2450Manual(visa))
    try:
        session.claim('simulated'); wait(lambda: session.connected)
        session.submit('apply', ManualSettings()); wait(lambda: session.configured)
        if mode == 'continuous':
            session.submit('on'); wait(lambda: session.output is True)
            session.submit('start', .05)
        else:
            session.submit('sweep', ManualSweep(0, .5, .05, .02))
        wait(lambda: len(visa.starts) >= 10)
        gaps = [(b-a)*1000 for a,b in zip(visa.starts, visa.starts[1:])]
        print(f'{mode}: mean {statistics.mean(gaps):.1f} ms, min {min(gaps):.1f}, max {max(gaps):.1f}; '
              f'20 ms simulated ADC, 2 ms transport latency')
    finally:
        session.submit('release'); wait(lambda: not session.claimed)
        session.shutdown(); session.thread.join(2)
