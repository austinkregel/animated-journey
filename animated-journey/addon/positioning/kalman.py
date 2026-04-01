import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from filterpy.kalman import KalmanFilter

logger = logging.getLogger(__name__)


@dataclass
class TrackerState:
    kf: KalmanFilter
    last_update: float
    last_seen: float
    initialized: bool = False


class KalmanTracker:
    """Manages per-MAC 3D Kalman filters for trajectory smoothing.

    State vector: [x, y, z, vx, vy, vz]
    Measurement vector: [x, y, z]
    """

    def __init__(self, max_speed_mps: float = 2.0, process_noise: float = 0.5):
        self._trackers: dict[str, TrackerState] = {}
        self._max_speed = max_speed_mps
        self._process_noise = process_noise

    def _create_filter(self, x: float, y: float, z: float, accuracy_m: float) -> KalmanFilter:
        kf = KalmanFilter(dim_x=6, dim_z=3)

        kf.F = np.eye(6)

        # Measurement function: we observe [x, y, z]
        kf.H = np.zeros((3, 6))
        kf.H[0, 0] = 1.0
        kf.H[1, 1] = 1.0
        kf.H[2, 2] = 1.0

        kf.x = np.array([x, y, z, 0.0, 0.0, 0.0])

        kf.P = np.diag([
            accuracy_m ** 2, accuracy_m ** 2, accuracy_m ** 2,
            4.0, 4.0, 4.0,
        ])

        r = max(accuracy_m, 0.5) ** 2
        kf.R = np.diag([r, r, r])

        kf.Q = np.eye(6) * self._process_noise

        return kf

    def _update_process_noise(self, kf: KalmanFilter, dt: float) -> None:
        """Set process noise based on elapsed time using piecewise constant white noise."""
        q = self._process_noise
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt3 * dt

        # 3-axis PCWN: block-diagonal for (x,vx), (y,vy), (z,vz)
        Q = np.zeros((6, 6))
        for ax in range(3):
            p = ax          # position index
            v = ax + 3      # velocity index
            Q[p, p] = q * dt4 / 4
            Q[p, v] = q * dt3 / 2
            Q[v, p] = q * dt3 / 2
            Q[v, v] = q * dt2
        kf.Q = Q

    def update(
        self,
        mac: str,
        x: float,
        y: float,
        z: float,
        accuracy_m: float,
        timestamp: float,
    ) -> tuple[float, float, float, float, float, float]:
        """Create or update Kalman filter for a MAC address.

        Returns smoothed (x, y, z, vx, vy, vz).
        Rejects measurements that imply unrealistic speed.
        """
        tracker = self._trackers.get(mac)

        if tracker is None:
            kf = self._create_filter(x, y, z, accuracy_m)
            self._trackers[mac] = TrackerState(
                kf=kf,
                last_update=timestamp,
                last_seen=timestamp,
                initialized=True,
            )
            return (x, y, z, 0.0, 0.0, 0.0)

        kf = tracker.kf
        dt = max(timestamp - tracker.last_update, 0.001)

        # Predict step: update state transition with dt
        kf.F = np.eye(6)
        kf.F[0, 3] = dt
        kf.F[1, 4] = dt
        kf.F[2, 5] = dt
        self._update_process_noise(kf, dt)
        kf.predict()

        predicted_x = float(kf.x[0])
        predicted_y = float(kf.x[1])
        predicted_z = float(kf.x[2])
        dx = x - predicted_x
        dy = y - predicted_y
        dz = z - predicted_z
        implied_speed = math.sqrt(dx * dx + dy * dy + dz * dz) / dt if dt > 0 else 0.0

        if implied_speed > self._max_speed * 3:
            logger.debug(
                "Rejecting measurement for %s: implied speed %.1f m/s exceeds limit",
                mac, implied_speed,
            )
        else:
            r = max(accuracy_m, 0.5) ** 2
            kf.R = np.diag([r, r, r])
            kf.update(np.array([x, y, z]))

        tracker.last_update = timestamp
        tracker.last_seen = timestamp

        state = kf.x
        return (
            float(state[0]), float(state[1]), float(state[2]),
            float(state[3]), float(state[4]), float(state[5]),
        )

    def predict(self, mac: str, timestamp: float) -> Optional[tuple[float, float, float, float, float, float]]:
        """Predict position at a given timestamp without a measurement update."""
        tracker = self._trackers.get(mac)
        if tracker is None:
            return None

        dt = max(timestamp - tracker.last_update, 0.0)
        if dt <= 0:
            state = tracker.kf.x
            return (
                float(state[0]), float(state[1]), float(state[2]),
                float(state[3]), float(state[4]), float(state[5]),
            )

        F = np.eye(6)
        F[0, 3] = dt
        F[1, 4] = dt
        F[2, 5] = dt
        predicted = F @ tracker.kf.x
        return (
            float(predicted[0]), float(predicted[1]), float(predicted[2]),
            float(predicted[3]), float(predicted[4]), float(predicted[5]),
        )

    def get_state(self, mac: str) -> Optional[dict]:
        tracker = self._trackers.get(mac)
        if tracker is None:
            return None
        state = tracker.kf.x
        return {
            "x": float(state[0]),
            "y": float(state[1]),
            "z": float(state[2]),
            "vx": float(state[3]),
            "vy": float(state[4]),
            "vz": float(state[5]),
            "last_seen": tracker.last_seen,
        }

    def get_all_states(self) -> dict[str, dict]:
        return {mac: self.get_state(mac) for mac in self._trackers}

    def prune_stale(self, max_age_s: float = 300.0) -> list[str]:
        """Remove trackers not updated in max_age_s seconds. Returns pruned MACs."""
        now = time.time()
        stale = [
            mac for mac, tracker in self._trackers.items()
            if (now - tracker.last_seen) > max_age_s
        ]
        for mac in stale:
            del self._trackers[mac]
        if stale:
            logger.debug("Pruned %d stale trackers", len(stale))
        return stale
