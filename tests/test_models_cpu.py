from __future__ import annotations

import builtins
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.utils.atr_quartile import (
    atr_column_index,
    augment_numpy_matrix,
    compute_quartile_edges,
    load_sidecar,
    save_sidecar,
)


class CPUModelTest(unittest.TestCase):
    def test_xgboost_imports_and_predicts_without_cupy(self) -> None:
        module_path = Path(__file__).resolve().parent.parent / "external" / "xgboost.py"
        specification = importlib.util.spec_from_file_location(
            "hurz_cpu_xgboost_test",
            module_path,
        )
        module = importlib.util.module_from_spec(specification)
        original_import = builtins.__import__

        def import_without_cupy(name, *args, **kwargs):
            if name == "cupy":
                raise ImportError("CuPy intentionally unavailable in CPU test.")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_cupy):
            specification.loader.exec_module(module)

        self.assertFalse(module.CUDA_AVAILABLE)
        self.assertEqual("cpu", module.XGBOOST_DEVICE)
        model = module.xgb.XGBClassifier(
            n_estimators=2,
            max_depth=1,
            tree_method="hist",
            device=module.XGBOOST_DEVICE,
        )
        values = np.asarray([[0.0], [1.0], [0.1], [0.9]], dtype=np.float32)
        model.fit(values, np.asarray([0, 1, 0, 1]))
        probabilities = model.predict_proba(values)
        self.assertEqual((4, 2), probabilities.shape)

    def test_atr_quartile_features_and_sidecar_are_reproducible(self) -> None:
        matrix = np.arange(80, dtype=float).reshape(5, 16)
        atr_index = atr_column_index(matrix.shape[1])
        edges = compute_quartile_edges(matrix[:, atr_index])
        augmented = augment_numpy_matrix(matrix, edges)

        self.assertEqual((5, 20), augmented.shape)
        np.testing.assert_array_equal(
            np.ones(5),
            augmented[:, -4:].sum(axis=1),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            model_path = str(Path(temporary_directory) / "model.json")
            save_sidecar(model_path, edges)
            self.assertEqual(edges, load_sidecar(model_path))


if __name__ == "__main__":
    unittest.main()
