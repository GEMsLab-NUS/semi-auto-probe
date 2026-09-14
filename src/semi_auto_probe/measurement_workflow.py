"""Measurement configuration shared by probe and bonded-chip scans."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AutoTestFlowStep:
    type_id: str
    params: dict[str, str]
