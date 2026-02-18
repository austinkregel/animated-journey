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
    """Manages per-MAC 2D Kalman filters for trajectory smoothing.

    State vector: [x, y, vx, vy]
    Measurement vector: [x, y]
    """

    def __init__(self, max_speed_mps: float = 2.0, process_noise: float = 0.5):
        self._trackers: dict[str, TrackerState] = {}
        self._max_speed = max_speed_mps
        self._process_noise = process_noise

    def _create_filter(self, x: float, y: float, accuracy_m: float) -> KalmanFilter:
        kf = KalmanFilter(dim_x=4, dim_z=2)

        # State transition (constant velocity model, dt filled in at predict time)
        kf.F = np.eye(4)

        # Measurement function: we observe [x, y]
        kf.H = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ])

        # Initial state
        kf.x = np.array([x, y, 0.0, 0.0])

        # Initial covariance: position uncertain by accuracy, velocity very uncertain
        kf.P = np.diag([accuracy_m ** 2, accuracy_m ** 2, 4.0, 4.0])

        # Measurement noise from accuracy estimate
        r = max(accuracy_m, 0.5) ** 2
        kf.R = np.diag([r, r])

        # Process noise (updated per-predict based on dt)
        kf.Q = np.eye(4) * self._process_noise

        return kf

    def _update_process_noise(self, kf: KalmanFilter, dt: float) -> None:
        """Set process noise based on elapsed time using piecewise constant white noise."""
        q = self._process_noise
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt3 * dt

        kf.Q = q * np.array([
            [dt4 / 4, 0,       dt3 / 2, 0      ],
            [0,       dt4 / 4, 0,       dt3 / 2],
            [dt3 / 2, 0,       dt2,     0      ],
            [0,       dt3 / 2, 0,       dt2    ],
        ])

    def update(
        self,
        mac: str,
        x: float,
        y: float,
        accuracy_m: float,
        timestamp: float,
    ) -> tuple[float, float, float, float]:
        """Create or update Kalman filter for a MAC address.

        Returns smoothed (x, y, vx, vy).
        Rejects measurements that imply unrealistic speed.
        """
        tracker = self._trackers.get(mac)

        if tracker is None:
            kf = self._create_filter(x, y, accuracy_m)
            self._trackers[mac] = TrackerState(
                kf=kf,
                last_update=timestamp,
                last_seen=timestamp,
                initialized=True,
            )
            return (x, y, 0.0, 0.0)

        kf = tracker.kf
        dt = max(timestamp - tracker.last_update, 0.001)

        # Predict step
        kf.F[0, 2] = dt
        kf.F[1, 3] = dt
        self._update_process_noise(kf, dt)
        kf.predict()

        # Check if implied speed is realistic before accepting the measurement
        predicted_x, predicted_y = float(kf.x[0]), float(kf.x[1])
        dx = x - predicted_x
        dy = y - predicted_y
        implied_speed = math.sqrt(dx * dx + dy * dy) / dt if dt > 0 else 0.0

        if implied_speed > self._max_speed * 3:
            # Likely multipath error — use prediction only
            logger.debug(
                "Rejecting measurement for %s: implied speed %.1f m/s exceeds limit",
                mac, implied_speed,
            )
        else:
            # Update measurement noise from current accuracy
            r = max(accuracy_m, 0.5) ** 2
            kf.R = np.diag([r, r])
            kf.update(np.array([x, y]))

        tracker.last_update = timestamp
        tracker.last_seen = timestamp

        state = kf.x
        return (float(state[0]), float(state[1]), float(state[2]), float(state[3]))

    def predict(self, mac: str, timestamp: float) -> Optional[tuple[float, float, float, float]]:
        """Predict position at a given timestamp without a measurement update."""
        tracker = self._trackers.get(mac)
        if tracker is None:
            return None

        dt = max(timestamp - tracker.last_update, 0.0)
        if dt <= 0:
            state = tracker.kf.x
            return (float(state[0]), float(state[1]), float(state[2]), float(state[3]))

        # Work on a copy so we don't advance the filter without a real measurement
        kf = tracker.kf
        F = kf.F.copy()
        F[0, 2] = dt
        F[1, 3] = dt
        predicted = F @ kf.x
        return (float(predicted[0]), float(predicted[1]), float(predicted[2]), float(predicted[3]))

    def get_state(self, mac: str) -> Optional[dict]:
        tracker = self._trackers.get(mac)
        if tracker is None:
            return None
        state = tracker.kf.x
        return {
            "x": float(state[0]),
            "y": float(state[1]),
            "vx": float(state[2]),
            "vy": float(state[3]),
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
