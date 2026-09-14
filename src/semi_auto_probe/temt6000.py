"""Approximate monochromatic irradiance from a TEMT6000 collector current.

See docs/temt6000-irradiance.md for the normalization, sources and limitations.
No package area or unity phototransistor gain is assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import math

import numpy as np


MODEL_VERSION = "temt6000-illuminant-a-v1"
# Approximate readings of Vishay document 81579, rev. 1.9, Fig. 5.
# The unplotted UV tail is tapered to zero; do not extrapolate user wavelengths.
SPECTRAL_RESPONSE = (
    (300, 0), (360, .10), (400, .28), (440, .54), (450, .59),
    (500, .80), (525, .89), (550, .975), (570, 1), (600, .96),
    (650, .89), (700, .79), (750, .66), (800, .50), (850, .35),
    (900, .23), (950, .13), (1000, .06), (1050, .018), (1100, 0),
)


def is_temt6000(params) -> bool:
    # Recognize saved templates predating the explicit detector selector.
    model = params.get("optical_model")
    return model == "TEMT6000" or (model is None and params.get("_node_id") == "temt6000_current")


def relative_response(wavelength_nm: float) -> float:
    if not math.isfinite(wavelength_nm) or not 360 <= wavelength_nm <= 1000:
        raise ValueError("TEMT6000 wavelength must be between 360 and 1000 nm; no spectral extrapolation.")
    wavelengths, sensitivity = np.array(SPECTRAL_RESPONSE).T
    return float(np.interp(wavelength_nm, wavelengths, sensitivity))


@lru_cache(maxsize=1)
def peak_responsivity() -> float:
    """Collector A per W/m² at the spectral peak, typical 5 V / 25 °C.

    k = (50 µA / 100 lx) * 683 * integral(A * V) / integral(A * S).
    A is a 2856 K Planck spectrum approximating CIE illuminant A, including
    its infrared tail (the sensor still responds beyond the photopic range).
    """
    wl = np.arange(300., 1101.)
    spectrum = (560 / wl) ** 5 * np.expm1(1.438776877e7 / (560 * 2856)) / np.expm1(1.438776877e7 / (wl * 2856))
    photopic = np.loadtxt(Path(__file__).with_name("assets") / "CIE_sle_photopic.csv", delimiter=",")
    v = np.interp(wl, photopic[:, 0], photopic[:, 1], left=0, right=0)
    curve_wl, curve_s = np.array(SPECTRAL_RESPONSE).T
    sensitivity = np.interp(wl, curve_wl, curve_s)
    integrate = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
    return float(0.5e-6 * 683 * integrate(spectrum * v, wl) / integrate(spectrum * sensitivity, wl))


@dataclass(frozen=True)
class TEMT6000Response:
    wavelength_nm: float = 650
    power_correction: float = 1
    dark_current_a: float = 0

    @classmethod
    def from_params(cls, params):
        result = cls(float(params.get("wavelength_nm", "650")),
                     float(params.get("power_correction", "1")),
                     float(params.get("dark_current_a", "0")))
        relative_response(result.wavelength_nm)
        if not math.isfinite(result.power_correction) or result.power_correction <= 0:
            raise ValueError("TEMT6000 power correction must be finite and positive.")
        if not math.isfinite(result.dark_current_a):
            raise ValueError("TEMT6000 dark-current offset must be finite (A).")
        return result

    @property
    def responsivity_a_per_w_m2(self):
        return peak_responsivity() * relative_response(self.wavelength_nm)

    def irradiance(self, current_a):
        """W/m²; negative net current remains unavailable, never abs/clipped."""
        if current_a is None or not math.isfinite(current_a) or current_a < self.dark_current_a:
            return None
        return (current_a - self.dark_current_a) / self.responsivity_a_per_w_m2 * self.power_correction

    def metadata(self):
        return dict(model=MODEL_VERSION, wavelength_nm=self.wavelength_nm,
                    power_correction=self.power_correction, dark_current_a=self.dark_current_a,
                    responsivity_a_per_w_m2=self.responsivity_a_per_w_m2,
                    quantity="estimated incident irradiance", unit="W/m²",
                    assumptions="Monochromatic light; linear response; typical 5 V, 25 C; normal incidence. Sensor-area average, not resolved local irradiance for a focused spot.")
