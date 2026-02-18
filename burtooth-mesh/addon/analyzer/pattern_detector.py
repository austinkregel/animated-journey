import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class PatternDetector:
    """Detects temporal patterns in device appearances."""

    def __init__(self):
        self._device_history: Dict[str, List[dict]] = defaultdict(list)

    def record_visit(
        self,
        mac_hash: str,
        timestamp: datetime,
        duration_s: float,
        entry_bearing: Optional[float] = None,
        exit_bearing: Optional[float] = None,
        avg_speed: Optional[float] = None,
    ) -> None:
        """Record a device visit for pattern analysis."""
        self._device_history[mac_hash].append({
            "timestamp": timestamp,
            "mac_hash": mac_hash,
            "duration_s": duration_s,
            "entry_bearing": entry_bearing,
            "exit_bearing": exit_bearing,
            "avg_speed": avg_speed,
        })

    def detect_commuters(
        self,
        min_appearances: int = 5,
        time_tolerance_min: int = 30,
    ) -> List[dict]:
        """Find devices that appear regularly at similar times.

        Returns a list of dicts with keys:
            mac_hash, typical_times, days_of_week, frequency, confidence
        """
        results: List[dict] = []
        tolerance = timedelta(minutes=time_tolerance_min)

        for mac_hash, visits in self._device_history.items():
            if len(visits) < min_appearances:
                continue

            time_clusters = self._cluster_times_of_day(visits, tolerance)
            day_counts = self._count_days_of_week(visits)

            if not time_clusters:
                continue

            active_days = [day for day, count in day_counts.items() if count > 0]
            total_visits = len(visits)

            # Regularity: ratio of visits that fall into a time cluster
            clustered_count = sum(len(c) for c in time_clusters)
            regularity = clustered_count / total_visits if total_visits else 0.0

            # Confidence combines regularity with appearance count (capped contribution)
            count_factor = min(total_visits / 20.0, 1.0)
            confidence = round(0.6 * regularity + 0.4 * count_factor, 3)

            typical_times = []
            for cluster in time_clusters:
                minutes = [
                    v["timestamp"].hour * 60 + v["timestamp"].minute for v in cluster
                ]
                mean_min = int(np.mean(minutes))
                typical_times.append(f"{mean_min // 60:02d}:{mean_min % 60:02d}")

            results.append({
                "mac_hash": mac_hash,
                "typical_times": typical_times,
                "days_of_week": active_days,
                "frequency": total_visits,
                "confidence": confidence,
            })

        results.sort(key=lambda r: r["confidence"], reverse=True)
        return results

    def detect_time_patterns(self, hours: int = 168) -> dict:
        """Analyze overall traffic by time-of-day and day-of-week.

        Returns dict with keys:
            hourly_counts (24 ints), daily_counts (7 ints),
            peak_hours (list), quiet_hours (list)
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        hourly = np.zeros(24, dtype=int)
        daily = np.zeros(7, dtype=int)

        for visits in self._device_history.values():
            for v in visits:
                ts: datetime = v["timestamp"]
                if ts < cutoff:
                    continue
                hourly[ts.hour] += 1
                daily[ts.weekday()] += 1

        hourly_list = hourly.tolist()
        daily_list = daily.tolist()

        if hourly.sum() == 0:
            return {
                "hourly_counts": hourly_list,
                "daily_counts": daily_list,
                "peak_hours": [],
                "quiet_hours": [],
            }

        mean_h = float(np.mean(hourly))
        std_h = float(np.std(hourly)) or 1.0
        peak_hours = [int(h) for h in range(24) if hourly[h] > mean_h + std_h]
        quiet_hours = [int(h) for h in range(24) if hourly[h] < max(mean_h - std_h, 1)]

        return {
            "hourly_counts": hourly_list,
            "daily_counts": daily_list,
            "peak_hours": peak_hours,
            "quiet_hours": quiet_hours,
        }

    def get_recent_activity(self, hours: int = 24) -> dict:
        """Get activity summary for the last *hours* hours.

        Returns dict with keys:
            total_devices, unique_devices, avg_duration, busiest_hour
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        hourly: Dict[int, int] = defaultdict(int)
        durations: List[float] = []
        seen_macs: set = set()
        total = 0

        for mac_hash, visits in self._device_history.items():
            for v in visits:
                if v["timestamp"] < cutoff:
                    continue
                total += 1
                seen_macs.add(mac_hash)
                durations.append(v["duration_s"])
                hourly[v["timestamp"].hour] += 1

        busiest_hour: Optional[int] = None
        if hourly:
            busiest_hour = max(hourly, key=hourly.get)  # type: ignore[arg-type]

        return {
            "total_devices": total,
            "unique_devices": len(seen_macs),
            "avg_duration": round(float(np.mean(durations)), 1) if durations else 0.0,
            "busiest_hour": busiest_hour,
        }

    def get_device_history(self, mac_hash: str, days: int = 30) -> List[dict]:
        """Get visit history for a specific device within the last *days* days."""
        cutoff = datetime.now() - timedelta(days=days)
        return [
            v for v in self._device_history.get(mac_hash, []) if v["timestamp"] >= cutoff
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cluster_times_of_day(
        visits: List[dict],
        tolerance: timedelta,
    ) -> List[List[dict]]:
        """Group visits into clusters based on time-of-day proximity."""
        tol_minutes = tolerance.total_seconds() / 60.0
        sorted_visits = sorted(
            visits, key=lambda v: v["timestamp"].hour * 60 + v["timestamp"].minute
        )

        clusters: List[List[dict]] = []
        current_cluster: List[dict] = []

        for v in sorted_visits:
            v_min = v["timestamp"].hour * 60 + v["timestamp"].minute
            if not current_cluster:
                current_cluster.append(v)
                continue

            first_min = (
                current_cluster[0]["timestamp"].hour * 60
                + current_cluster[0]["timestamp"].minute
            )
            if abs(v_min - first_min) <= tol_minutes:
                current_cluster.append(v)
            else:
                if len(current_cluster) >= 2:
                    clusters.append(current_cluster)
                current_cluster = [v]

        if len(current_cluster) >= 2:
            clusters.append(current_cluster)

        return clusters

    @staticmethod
    def _count_days_of_week(visits: List[dict]) -> Dict[int, int]:
        """Count visits per day of week (0=Monday, 6=Sunday)."""
        counts: Dict[int, int] = {d: 0 for d in range(7)}
        for v in visits:
            counts[v["timestamp"].weekday()] += 1
        return counts
