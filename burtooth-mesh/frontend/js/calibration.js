class CalibrationView {
  constructor(map, options = {}) {
    this.map = map;
    this.apiBase = options.apiBase || '';
    this.active = false;
    this.pollTimer = null;
    this.pollInterval = options.pollInterval || 3000;
    this.waypointMarkers = [];
    this.waypointLayer = L.layerGroup().addTo(this.map);
    this.manualMode = false;
    this.currentWaypointIndex = 0;

    this.onStatusUpdate = options.onStatusUpdate || (() => {});
  }

  start() {
    if (this.active) return;
    this.active = true;
    this._pollStatus();
  }

  stop() {
    this.active = false;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  }

  _pollStatus() {
    if (!this.active) return;

    fetch(this.apiBase + '/api/calibration/status')
      .then((r) => r.json())
      .then((data) => {
        this.onStatusUpdate(data);
      })
      .catch((err) => {
        console.warn('Calibration poll error:', err);
      })
      .finally(() => {
        if (this.active) {
          this.pollTimer = setTimeout(() => this._pollStatus(), this.pollInterval);
        }
      });
  }

  startManualCalibration(waypoints) {
    this.manualMode = true;
    this.currentWaypointIndex = 0;
    this.waypointLayer.clearLayers();
    this.waypointMarkers = [];

    (waypoints || []).forEach((wp, i) => {
      const color = i === 0 ? '#58a6ff' : '#555';
      const icon = L.divIcon({
        className: 'calibration-waypoint',
        html: `<div class="cal-wp" style="border-color:${color}">
          <span>${i + 1}</span>
        </div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });

      // CRS.Simple: latLng(y, x)
      const marker = L.marker([wp.y, wp.x], { icon: icon, interactive: false })
        .addTo(this.waypointLayer);

      this.waypointMarkers.push({ marker, wp, completed: false });
    });
  }

  markCurrentWaypoint() {
    if (!this.manualMode || this.currentWaypointIndex >= this.waypointMarkers.length) return null;

    const entry = this.waypointMarkers[this.currentWaypointIndex];
    entry.completed = true;

    const icon = L.divIcon({
      className: 'calibration-waypoint',
      html: `<div class="cal-wp completed"><span>&#10003;</span></div>`,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
    entry.marker.setIcon(icon);

    const result = {
      waypoint_index: this.currentWaypointIndex,
      x: entry.wp.x,
      y: entry.wp.y,
      z: entry.wp.z || 0,
      node_id: entry.wp.node_id,
      timestamp: new Date().toISOString(),
    };

    this.currentWaypointIndex++;

    if (this.currentWaypointIndex < this.waypointMarkers.length) {
      const next = this.waypointMarkers[this.currentWaypointIndex];
      const nextIcon = L.divIcon({
        className: 'calibration-waypoint',
        html: `<div class="cal-wp active"><span>${this.currentWaypointIndex + 1}</span></div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });
      next.marker.setIcon(nextIcon);
    }

    return this._sendCalibrationPoint(result);
  }

  _sendCalibrationPoint(point) {
    return fetch(this.apiBase + '/api/calibration/point', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(point),
    })
      .then((r) => r.json())
      .catch((err) => {
        console.warn('Calibration point error:', err);
        return null;
      });
  }

  stopManualCalibration() {
    this.manualMode = false;
    this.waypointLayer.clearLayers();
    this.waypointMarkers = [];
    this.currentWaypointIndex = 0;
  }

  isManualMode() {
    return this.manualMode;
  }

  getCurrentWaypointIndex() {
    return this.currentWaypointIndex;
  }

  getTotalWaypoints() {
    return this.waypointMarkers.length;
  }

  destroy() {
    this.stop();
    this.stopManualCalibration();
    this.map.removeLayer(this.waypointLayer);
  }
}
