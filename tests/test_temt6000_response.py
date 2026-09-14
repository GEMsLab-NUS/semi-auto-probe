import hashlib
import math
from pathlib import Path
import unittest

import numpy as np
from matplotlib.figure import Figure

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.mapping import generate_mapping_plan
from semi_auto_probe.mapping_plot import draw_temt6000_figure
from semi_auto_probe.temt6000 import TEMT6000Response, is_temt6000, peak_responsivity, relative_response
from semi_auto_probe.workflow_library import cards_from_workflow_payload
from test_mapping import settings


class TEMT6000ResponseTests(unittest.TestCase):
    def test_spectral_correction_and_units(self):
        response = TEMT6000Response()
        self.assertAlmostEqual(relative_response(650), .89)
        self.assertAlmostEqual(peak_responsivity(), 6.794151527e-5, delta=1e-12)
        self.assertAlmostEqual(response.irradiance(50e-6), .82688434395, places=9)
        self.assertAlmostEqual(response.irradiance(50e-6) / TEMT6000Response(570).irradiance(50e-6), 1 / .89)
        adjusted = TEMT6000Response(650, 2, 5e-6)
        self.assertAlmostEqual(adjusted.irradiance(55e-6), 2 * response.irradiance(50e-6))
        for value in (None, math.nan, math.inf, -1e-9):
            self.assertIsNone(response.irradiance(value))
        self.assertIsNone(adjusted.irradiance(4e-6))
        self.assertEqual(adjusted.irradiance(5e-6), 0)

    def test_invalid_optics_rejected_in_preflight_and_generic_nodes_unaffected(self):
        for key, value in (("wavelength_nm", "nan"), ("wavelength_nm", "1100"),
                           ("wavelength_nm", "350"), ("power_correction", "0"),
                           ("power_correction", "inf"), ("dark_current_a", "nan")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                ProbeApp._constant_current_node_config({"optical_model": "TEMT6000", key: value})
        ProbeApp._constant_current_node_config({"optical_model": "none", "wavelength_nm": "invalid"})
        self.assertFalse(is_temt6000({"_node_id": "unrelated_current"}))
        self.assertTrue(is_temt6000({"_node_id": "temt6000_current"}))
        self.assertFalse(is_temt6000({"_node_id": "temt6000_current", "optical_model": "none"}))
        cards = cards_from_workflow_payload({"version": 2, "format": "semi_auto_probe.measurement_workflow", "nodes": [{"type_id": "constant_current", "params": {"_node_id": "temt6000_current"}}]})
        self.assertEqual(cards[0].params["optical_model"], "none")
        self.assertEqual(cards[0].params["wavelength_nm"], "650")

    def test_dual_plot_preserves_geometry_auto_limits_and_missing_states(self):
        plan = generate_mapping_plan(settings(), (1, 1, 1))
        current = np.array([[1, 2, 3], [4, 5, 6], [7, 9000, -9000]], dtype=float) * 1e-6
        power = current / TEMT6000Response().responsivity_a_per_w_m2
        states = np.ones((3, 3), dtype=int)
        states[2, 1:] = [2, 3]
        axes = draw_temt6000_figure(Figure(figsize=(10, 6)), plan, current, power, states)
        self.assertEqual(len(axes), 2)
        for ax in axes:
            self.assertEqual(ax.get_aspect(), 1)
            self.assertEqual(ax.images[0].get_cmap().name, "inferno")
            self.assertTrue(ax.images[0].get_array().mask[2, 1:].all())
        np.testing.assert_allclose(axes[0].images[0].get_clim(), [1, 7])
        np.testing.assert_allclose(axes[1].images[0].get_clim(), np.array([1, 7]) * 1e-7 / TEMT6000Response().responsivity_a_per_w_m2)
        self.assertEqual(axes[0].get_xlim(), axes[1].get_xlim())
        self.assertEqual(axes[0].get_ylim(), axes[1].get_ylim())

    def test_reference_data_checksum(self):
        import semi_auto_probe.temt6000 as module
        data = Path(module.__file__).with_name("assets").joinpath("CIE_sle_photopic.csv").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "ee5d5d17922ae645d4af52cacf6a50bdb9385749f9d2181ca312eb2b08febac2")
