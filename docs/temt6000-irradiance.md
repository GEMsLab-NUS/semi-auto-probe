# TEMT6000 current and estimated irradiance mapping

Choose the TEMT6000 Mapping template, or select **Optical detector = TEMT6000** in a **2450 Fixed-bias Current** node. Set **TEMT6000 wavelength (nm)** to the laser wavelength (default 650 nm). Mapping offers a dual view for each TEMT6000 node: measured signed total current and estimated incident optical power density. Other workflow metrics remain selectable. Both plots use the sequential `inferno` colormap, equal physical XY scales, and independent automatic color limits from completed finite measurements. Click either plot to inspect the same spatial point. The saved `mapping.png` contains the selected view; saved results can be reopened without instruments.

## Conversion and provenance

TEMT6000 is a phototransistor. Its package area is not a specified active optical area, and its internal current gain is not unity. The implementation therefore does not infer A/W from quantum efficiency or divide by the package dimensions.

The default is a **typical-device estimate**, normalized to the datasheet's illuminant-A characterization. Let `s(λ)` be the approximate relative sensitivity digitized from Fig. 5, `A(λ)` the relative spectral irradiance of a 2856 K Planck source approximating illuminant A, and `V(λ)` the CIE photopic sensitivity:

```
k_peak = (0.5e-6 A/lx) × 683 lm/W × ∫ A(λ)V(λ)dλ / ∫ A(λ)s(λ)dλ
k(λ)   = k_peak × s(λ)                              [A / (W/m²)]
E_est  = (I_measured − I_dark) / k(λ) × correction   [W/m²]
1 W/m² = 0.1 mW/cm²
```

Integration uses 1 nm sampling over 300–1100 nm, retaining the detector's infrared response to the reference lamp. The unplotted UV tail below 360 nm is tapered to zero; the response above 1100 nm is assumed zero. Relative sensitivity uses linear interpolation; user wavelengths outside 360–1000 nm are rejected. These approximations limit absolute accuracy. The runtime is fully offline.

**Power correction (×)** defaults to 1. At the same wavelength, bias, temperature and beam geometry, set it to `reference irradiance / uncorrected estimated irradiance` using a calibrated reference. A correction measured at one wavelength does not guarantee accuracy at another wavelength. **Measured dark offset (A)** defaults to 0; it is a user-supplied reading, not an automatic dark measurement. Negative net current produces no irradiance estimate, while the signed current remains visible. Failed and interrupted points do not contribute to either color range. No samples or failed points are replaced with zero.

The model assumes linear, unsaturated response, normal incidence, approximately 25 °C and 5 V VCE. Device gain, temperature, bias and spectral variation can change the actual response substantially. With a focused spot smaller than the sensitive area, this represents an equivalent sensor-area average and cannot reconstruct local peak irradiance without beam/area calibration. Ambient light contributes to total current unless separately accounted for. The result is incident detector-plane irradiance, not DP100 electrical power or total laser output power.

New measurement JSON files save the wavelength, response-model version, effective responsivity, correction and dark offset alongside `statistics.estimated_irradiance_w_m2`; the metric is also retained in each point's `mapping.csv` workflow data. Loading old TEMT6000 template results recognizes their original `temt6000_current` node and assumes 650 nm / correction 1 / dark offset 0 if absent. Generic current nodes are not treated as TEMT6000 detectors.

## Sources and data license

- Vishay, [TEMT6000X01 datasheet, document 81579, rev. 1.9](https://www.vishay.com/docs/81579/temt6000.pdf), Basic Characteristics and Fig. 5. Spectral samples in `temt6000.py` are approximate readings of the published curve, not an absolute responsivity specification.
- CIE (2019), [CIE spectral luminous efficiency for photopic vision](https://doi.org/10.25039/CIE.DS.dktna2s3). The unmodified data file `src/semi_auto_probe/assets/CIE_sle_photopic.csv` is distributed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); attribution and that license apply to this data. SHA-256: `ee5d5d17922ae645d4af52cacf6a50bdb9385749f9d2181ca312eb2b08febac2`.
- CIE (2018), [CIE standard illuminant A, 1 nm](https://doi.org/10.25039/CIE.DS.8jsxjrsn), used to check the 2856 K reference approximation. The Planck model extends into the infrared region required by this sensor; truncating the reference at 830 nm would bias the normalization.
