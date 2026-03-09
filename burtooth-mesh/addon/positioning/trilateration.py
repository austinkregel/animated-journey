import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class Trilateration:
    """RSSI-to-distance conversion and weighted least-squares 3D position estimation."""

    def __init__(self, default_tx_power: float = -59.0, default_path_loss_n: float = 2.7):
        self._default_tx_power = default_tx_power
        self._default_n = default_path_loss_n
        self.calibration: dict[str, dict[str, float]] = {}

    def set_calibration(self, node_id: str, tx_power: float, n: float) -> None:
        self.calibration[node_id] = {"tx_power": tx_power, "n": n}

    def rssi_to_distance(
        self,
        rssi: float,
        node_id: str | None = None,
        adv_tx_power: float | None = None,
    ) -> float:
        """Convert RSSI to estimated distance in meters using log-distance path loss model.

        d = 10^((TxPower - RSSI) / (10 * n))

        If the BLE advertisement included a TX power level, it is used in
        preference to the per-node calibration / default value.
        """
        cal = self.calibration.get(node_id, {}) if node_id else {}
        n = cal.get("n", self._default_n)

        if adv_tx_power is not None:
            tx_power = adv_tx_power
        else:
            tx_power = cal.get("tx_power", self._default_tx_power)

        if n <= 0:
            n = self._default_n

        exponent = (tx_power - rssi) / (10.0 * n)
        distance = math.pow(10.0, exponent)
        return max(distance, 0.1)

    def estimate_position(
        self,
        observations: list[tuple[str, float]],
        node_positions: dict[str, tuple[float, float, float]],
        adv_tx_power: float | None = None,
    ) -> Optional[tuple[float, float, float, float]]:
        """Estimate device position from RSSI observations in 3D.

        Args:
            observations: List of (node_id, rssi) pairs.
            node_positions: Map of node_id -> (x, y, z) in local meters.
            adv_tx_power: Optional TX power from the BLE advertisement.

        Returns:
            (x, y, z, accuracy_m) or None if insufficient data.
        """
        valid = [
            (nid, rssi) for nid, rssi in observations if nid in node_positions
        ]
        if len(valid) < 2:
            return None

        positions: list[tuple[float, float, float]] = []
        distances: list[float] = []
        weights: list[float] = []

        for node_id, rssi in valid:
            pos = node_positions[node_id]
            dist = self.rssi_to_distance(rssi, node_id, adv_tx_power)
            w = 1.0 / (dist * dist) if dist > 0 else 1.0

            positions.append(pos)
            distances.append(dist)
            weights.append(w)

        if len(valid) <= 3:
            return self._few_sphere_estimate(positions, distances, weights)

        return self._weighted_least_squares(positions, distances, weights)

    def _few_sphere_estimate(
        self,
        positions: list[tuple[float, float, float]],
        distances: list[float],
        weights: list[float],
    ) -> tuple[float, float, float, float]:
        """With 2-3 observations, return weighted midpoint along the line/plane
        between nodes (constrained estimate)."""
        total_inv_d = sum(1.0 / max(d, 0.1) for d in distances)
        if total_inv_d <= 0:
            total_inv_d = 1.0

        x_est = 0.0
        y_est = 0.0
        z_est = 0.0
        for pos, d in zip(positions, distances):
            frac = (1.0 / max(d, 0.1)) / total_inv_d
            x_est += pos[0] * frac
            y_est += pos[1] * frac
            z_est += pos[2] * frac

        accuracy = sum(distances) / len(distances)
        return (x_est, y_est, z_est, accuracy)

    def _weighted_least_squares(
        self,
        positions: list[tuple[float, float, float]],
        distances: list[float],
        weights: list[float],
    ) -> tuple[float, float, float, float]:
        """Weighted least-squares trilateration for 4+ observations in 3D.

        Linearises the sphere equations by subtracting the last equation from
        all others, yielding A @ [x, y, z] = b, then solves with WLS.
        """
        n = len(positions)
        xs = np.array([p[0] for p in positions])
        ys = np.array([p[1] for p in positions])
        zs = np.array([p[2] for p in positions])
        ds = np.array(distances)
        ws = np.array(weights)

        x_ref, y_ref, z_ref, d_ref = xs[-1], ys[-1], zs[-1], ds[-1]

        A = np.zeros((n - 1, 3))
        b = np.zeros(n - 1)
        W = np.zeros((n - 1, n - 1))

        for i in range(n - 1):
            A[i, 0] = 2.0 * (xs[i] - x_ref)
            A[i, 1] = 2.0 * (ys[i] - y_ref)
            A[i, 2] = 2.0 * (zs[i] - z_ref)
            b[i] = (
                ds[i] ** 2 - d_ref ** 2
                - xs[i] ** 2 + x_ref ** 2
                - ys[i] ** 2 + y_ref ** 2
                - zs[i] ** 2 + z_ref ** 2
            )
            W[i, i] = (ws[i] + ws[-1]) / 2.0

        try:
            WA = W @ A
            Wb = W @ b
            result, residuals, _, _ = np.linalg.lstsq(WA, Wb, rcond=None)
            x_est, y_est, z_est = float(result[0]), float(result[1]), float(result[2])
        except np.linalg.LinAlgError:
            logger.warning("Least-squares solve failed, falling back to weighted centroid")
            total_w = sum(weights)
            x_est = sum(p[0] * w for p, w in zip(positions, weights)) / total_w
            y_est = sum(p[1] * w for p, w in zip(positions, weights)) / total_w
            z_est = sum(p[2] * w for p, w in zip(positions, weights)) / total_w

        residual_sum = 0.0
        weight_sum = 0.0
        for i in range(n):
            predicted_dist = math.sqrt(
                (x_est - xs[i]) ** 2 + (y_est - ys[i]) ** 2 + (z_est - zs[i]) ** 2
            )
            residual_sum += ws[i] * (predicted_dist - ds[i]) ** 2
            weight_sum += ws[i]

        accuracy = math.sqrt(residual_sum / weight_sum) if weight_sum > 0 else 10.0

        return (x_est, y_est, z_est, accuracy)
