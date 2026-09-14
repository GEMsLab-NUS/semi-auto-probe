# ImgStitch registration and click mapping

The layout solves all horizontal and vertical grid neighbors jointly, weighted by registration confidence. Max correction (um) now limits the measured correction of each neighbor constraint, rather than the accumulated displacement of each tile from the nominal stage grid. This permits consistent camera skew to accumulate across a scan. Reg weight scales these corrections; the existing enabled green-edge fallback continues to use full weight for replaced edges.

Shift-click maps a mosaic point through the nearest covering tile, subtracts its optical center, and applies the session pixel scale and camera rotation before adding that tile's recorded stage coordinates. Empty gaps are rejected. This accounts for each tile's registration displacement; overlap regions can still have local ambiguity when tiles disagree.

Offline replay of the 2026-09-09 17:44 session (25 tiles, original settings, Reg weight 1.0 and Max correction 20 um): neighbor displacement residual median/max improved from 8.77/41.35 px to 0.43/1.04 px. These residuals compare layout displacement against phase-correlation measurements, not independent physical ground truth. First-tile center mapping previously had an offset of -283.38/-212.54 um; the corrected mapping returns its capture coordinates.

Regression coverage includes accumulated cross-axis displacement, capture-order independence, registered tile centers, rotated off-center points, empty gaps, and canvas pan/zoom to stage target conversion. Hardware motion was not exercised.
