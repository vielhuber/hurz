from __future__ import annotations

import json
import os

import numpy as np


INDICATOR_COUNT = 8
TIME_FEATURE_COUNT = 4
ATR_INDICATOR_INDEX = 5


def atr_column_index(feature_count: int) -> int:
    return feature_count - TIME_FEATURE_COUNT - INDICATOR_COUNT + ATR_INDICATOR_INDEX


def compute_quartile_edges(values: np.ndarray) -> list[float]:
    finite_values = np.asarray(values, dtype=float)
    finite_values = finite_values[np.isfinite(finite_values)]
    if finite_values.size == 0:
        raise ValueError("ATR quartiles require at least one finite value.")
    return [float(value) for value in np.quantile(finite_values, [0.25, 0.5, 0.75])]


def quartile_one_hot(values: np.ndarray, edges: list[float]) -> np.ndarray:
    quartiles = np.searchsorted(np.asarray(edges, dtype=float), values, side="right")
    return np.eye(4, dtype=float)[quartiles]


def augment_numpy_matrix(values: np.ndarray, edges: list[float]) -> np.ndarray:
    matrix = np.asarray(values)
    atr_values = matrix[:, atr_column_index(matrix.shape[1])]
    return np.concatenate(
        [matrix, quartile_one_hot(atr_values, edges).astype(matrix.dtype)],
        axis=1,
    )


def sidecar_path(filename_model: str) -> str:
    base, _ = os.path.splitext(filename_model)
    return base + ".atrreg_quartiles.json"


def save_sidecar(filename_model: str, edges: list[float]) -> None:
    with open(sidecar_path(filename_model), "w", encoding="utf-8") as file:
        json.dump(edges, file)


def load_sidecar(filename_model: str) -> list[float] | None:
    path = sidecar_path(filename_model)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as file:
        return [float(value) for value in json.load(file)]
