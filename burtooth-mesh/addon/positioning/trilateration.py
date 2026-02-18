import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class Trilateration:
    """RSSI-to-distance conversion and weighted least-squares position estimation."""

    def __init__(self, default_tx_power: float = -59.0, default_path_loss_n: float = 2.7):
        self._default_tx_power = default_tx_power
        self._default_n = default_path_loss_n
        self.calibration: dict[str, dict[str, float]] = {}

    def set_calibration(self, node_id: str, tx_power: float, n: float) -> None:
        self.calibration[node_id] = {"tx_power": tx_power, "n": n}

    def rssi_to_distance(self, rssi: float, node_id: str | None = None) -> float:
        """Convert RSSI to estimated distance in meters using log-distance path loss model.

        d = 10^((TxPower - RSSI) / (10 * n))
        """
        cal = self.calibration.get(node_id, {}) if node_id else {}
        tx_power = cal.get("tx_power", self._default_tx_power)
        n = cal.get("n", self._default_n)

        if n <= 0:
            n = self._default_n

        exponent = (tx_power - rssi) / (10.0 * n)
        distance = math.pow(10.0, exponent)
        return max(distance, 0.1)

    def estimate_position(
        self,
        observations: list[tuple[str, float]],
        node_positions: dict[str, tuple[float, float]],
    ) -> Optional[tuple[float, float, float]]:
        """Estimate device position from RSSI observations.

        Args:
            observations: List of (node_id, rssi) pairs.
            node_positions: Map of node_id -> (x, y) in local meters.

        Returns:
            (x, y, accuracy_m) or None if insufficient data.
        """
        valid = [
            (nid, rssi) for nid, rssi in observations if nid in node_positions
        ]
        if len(valid) < 2:
            return None

        positions = []
        distances = []
        weights = []

        for node_id, rssi in valid:
            pos = node_positions[node_id]
            dist = self.rssi_to_distance(rssi, node_id)
            w = 1.0 / (dist * dist) if dist > 0 else 1.0

            positions.append(pos)
            distances.append(dist)
            weights.append(w)

        if len(valid) == 2:
            return self._two_circle_estimate(positions, distances, weights)

        return self._weighted_least_squares(positions, distances, weights)

    def _two_circle_estimate(
        self,
        positions: list[tuple[float, float]],
        distances: list[float],
        weights: list[float],
    ) -> tuple[float, float, float]:
        """With only 2 observations, return weighted midpoint along the line between nodes."""
        (x1, y1), (x2, y2) = positions
        d1, d2 = distances
        w1, w2 = weights

        inter_dist = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        if inter_dist < 0.01:
            return (x1, y1, max(d1, d2))

        # Position along the line proportional to distances
        t = d1 / (d1 + d2) if (d1 + d2) > 0 else 0.5
        x = x1 + t * (x2 - x1)
        y = y1 + t * (y2 - y1)

        accuracy = (d1 + d2) / 2.0
        return (x, y, accuracy)

    def _weighted_least_squares(
        self,
        positions: list[tuple[float, float]],
        distances: list[float],
        weights: list[float],
    ) -> tuple[float, float, float]:
        """Weighted least-squares trilateration for 3+ observations.

        Linearises the system by subtracting the last equation from all others,
        yielding A @ [x, y] = b, then solves with weighted least squares.
        """
        n = len(positions)
        xs = np.array([p[0] for p in positions])
        ys = np.array([p[1] for p in positions])
        ds = np.array(distances)
        ws = np.array(weights)

        # Use the last circle as reference to linearize
        x_ref, y_ref, d_ref = xs[-1], ys[-1], ds[-1]

        A = np.zeros((n - 1, 2))
        b = np.zeros(n - 1)
        W = np.zeros((n - 1, n - 1))

        for i in range(n - 1):
            A[i, 0] = 2.0 * (xs[i] - x_ref)
            A[i, 1] = 2.0 * (ys[i] - y_ref)
            b[i] = (
                ds[i] ** 2 - d_ref ** 2
                - xs[i] ** 2 + x_ref ** 2
                - ys[i] ** 2 + y_ref ** 2
            )
            # Combine weights of both the i-th and reference circles
            W[i, i] = (ws[i] + ws[-1]) / 2.0

        try:
            WA = W @ A
            Wb = W @ b
            result, residuals, _, _ = np.linalg.lstsq(WA, Wb, rcond=None)
            x_est, y_est = float(result[0]), float(result[1])
        except np.linalg.LinAlgError:
            logger.warning("Least-squares solve failed, falling back to weighted centroid")
            total_w = sum(weights)
            x_est = sum(p[0] * w for p, w in zip(positions, weights)) / total_w
            y_est = sum(p[1] * w for p, w in zip(positions, weights)) / total_w

        # Accuracy: weighted RMS of distance residuals
        residual_sum = 0.0
        weight_sum = 0.0
        for i in range(n):
            predicted_dist = math.sqrt((x_est - xs[i]) ** 2 + (y_est - ys[i]) ** 2)
            residual_sum += ws[i] * (predicted_dist - ds[i]) ** 2
            weight_sum += ws[i]

        accuracy = math.sqrt(residual_sum / weight_sum) if weight_sum > 0 else 10.0

        return (x_est, y_est, accuracy)
