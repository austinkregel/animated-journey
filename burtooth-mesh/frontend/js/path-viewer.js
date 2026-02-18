class PathViewer {
  constructor(map, options = {}) {
    this.map = map;
    this.apiBase = options.apiBase || '';
    this.paths = [];
    this.selectedPath = null;
    this.pathLine = null;
    this.animMarker = null;
    this.timestampMarkers = [];
    this.pathLayer = L.layerGroup().addTo(this.map);

    this.playing = false;
    this.playSpeed = 1;
    this.playIndex = 0;
    this.playTimer = null;

    this.onPathsLoaded = options.onPathsLoaded || (() => {});
    this.onPlaybackUpdate = options.onPlaybackUpdate || (() => {});
  }

  fetchPaths() {
    return fetch(this.apiBase + '/api/paths')
      .then((r) => r.json())
      .then((data) => {
        this.paths = (data.paths || []).map((p) => ({
          id: p.id,
          mac_hash: p.mac_hash,
          start_time: p.start_time,
          end_time: p.end_time,
          duration: p.duration || this._calcDuration(p.start_time, p.end_time),
          distance: p.distance || 0,
          points: p.points || [],
        }));
        this.onPathsLoaded(this.paths);
        return this.paths;
      })
      .catch((err) => {
        console.warn('PathViewer fetch error:', err);
        return [];
      });
  }

  _calcDuration(start, end) {
    if (!start || !end) return 0;
    return (new Date(end) - new Date(start)) / 1000;
  }

  selectPath(pathId) {
    this.stop();
    this.clear();

    const path = this.paths.find((p) => p.id === pathId);
    if (!path || !path.points.length) return;

    this.selectedPath = path;
    this.playIndex = 0;

    const coords = path.points.map((p) => [p.lat, p.lng]);
    const segments = this._buildColoredSegments(path.points);

    segments.forEach((seg) => {
      L.polyline(seg.coords, {
        color: seg.color,
        weight: 3,
        opacity: 0.8,
      }).addTo(this.pathLayer);
    });

    this._addTimestampLabels(path.points);

    this.animMarker = L.circleMarker(coords[0], {
      radius: 8,
      fillColor: '#fff',
      fillOpacity: 1,
      color: '#58a6ff',
      weight: 3,
    }).addTo(this.pathLayer);

    this.animMarker.bindPopup('', { className: 'dark-popup' });

    this.map.fitBounds(L.latLngBounds(coords).pad(0.1));
    this.onPlaybackUpdate({ playing: false, index: 0, total: coords.length });
  }

  _buildColoredSegments(points) {
    const segments = [];
    for (let i = 0; i < points.length - 1; i++) {
      const speed = points[i].speed || 0;
      const color = this._speedColor(speed);
      segments.push({
        coords: [[points[i].lat, points[i].lng], [points[i + 1].lat, points[i + 1].lng]],
        color: color,
      });
    }
    return segments;
  }

  _speedColor(speed) {
    if (speed < 1.5) return '#22c55e';
    if (speed < 5) return '#eab308';
    return '#ef4444';
  }

  _addTimestampLabels(points) {
    const step = Math.max(1, Math.floor(points.length / 8));
    for (let i = 0; i < points.length; i += step) {
      const p = points[i];
      if (!p.timestamp) continue;
      const time = new Date(p.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const icon = L.divIcon({
        className: 'path-timestamp-label',
        html: `<span>${time}</span>`,
        iconSize: [60, 20],
        iconAnchor: [30, -8],
      });
      const m = L.marker([p.lat, p.lng], { icon: icon, interactive: false });
      m.addTo(this.pathLayer);
      this.timestampMarkers.push(m);
    }
  }

  play() {
    if (!this.selectedPath) return;
    this.playing = true;
    this._animate();
  }

  pause() {
    this.playing = false;
    if (this.playTimer) {
      cancelAnimationFrame(this.playTimer);
      this.playTimer = null;
    }
  }

  stop() {
    this.pause();
    this.playIndex = 0;
    if (this.selectedPath && this.animMarker) {
      const pts = this.selectedPath.points;
      if (pts.length) {
        this.animMarker.setLatLng([pts[0].lat, pts[0].lng]);
      }
    }
    this.onPlaybackUpdate({ playing: false, index: 0, total: this.selectedPath?.points?.length || 0 });
  }

  setSpeed(speed) {
    this.playSpeed = speed;
  }

  seekTo(index) {
    if (!this.selectedPath) return;
    const pts = this.selectedPath.points;
    this.playIndex = Math.max(0, Math.min(index, pts.length - 1));
    if (this.animMarker) {
      const p = pts[this.playIndex];
      this.animMarker.setLatLng([p.lat, p.lng]);
    }
    this.onPlaybackUpdate({
      playing: this.playing,
      index: this.playIndex,
      total: pts.length,
    });
  }

  _animate() {
    if (!this.playing || !this.selectedPath) return;
    const pts = this.selectedPath.points;

    if (this.playIndex >= pts.length - 1) {
      this.playing = false;
      this.onPlaybackUpdate({ playing: false, index: this.playIndex, total: pts.length });
      return;
    }

    this.playIndex++;
    const p = pts[this.playIndex];
    this.animMarker.setLatLng([p.lat, p.lng]);

    const speed = p.speed || 0;
    const time = p.timestamp ? new Date(p.timestamp).toLocaleTimeString() : '';
    this.animMarker.setPopupContent(`
      <div class="device-popup">
        <div class="node-popup-body">
          <div class="node-popup-row"><span class="label">Time:</span> ${time}</div>
          <div class="node-popup-row"><span class="label">Speed:</span> ${speed.toFixed(1)} m/s</div>
          <div class="node-popup-row"><span class="label">Point:</span> ${this.playIndex + 1}/${pts.length}</div>
        </div>
      </div>
    `);

    this.onPlaybackUpdate({
      playing: true,
      index: this.playIndex,
      total: pts.length,
      time: time,
      speed: speed,
    });

    const baseDelay = 100;
    const delay = baseDelay / this.playSpeed;

    this.playTimer = setTimeout(() => {
      requestAnimationFrame(() => this._animate());
    }, delay);
  }

  clear() {
    this.pathLayer.clearLayers();
    this.animMarker = null;
    this.timestampMarkers = [];
    this.selectedPath = null;
    this.playIndex = 0;
    this.playing = false;
  }

  getPathList() {
    return this.paths.map((p) => ({
      id: p.id,
      mac_hash: p.mac_hash,
      start_time: p.start_time,
      end_time: p.end_time,
      duration: p.duration,
      distance: p.distance,
    }));
  }

  destroy() {
    this.stop();
    this.clear();
    this.map.removeLayer(this.pathLayer);
  }
}
