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
        this.paths = (data.paths || data || []).map((p) => ({
          id: p.id || p.path_id,
          mac_hash: p.mac_hash,
          start_time: p.start_time,
          end_time: p.end_time,
          duration: p.duration || p.duration_s || this._calcDuration(p.start_time, p.end_time),
          distance: p.distance || p.distance_m || 0,
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

  _toLatLng(point) {
    // CRS.Simple: latLng(y, x)
    return [point.y, point.x];
  }

  selectPath(pathId) {
    this.stop();
    this.clear();

    var path = this.paths.find((p) => p.id === pathId);
    if (!path || !path.points.length) return;

    this.selectedPath = path;
    this.playIndex = 0;

    var coords = path.points.map((p) => this._toLatLng(p));
    var segments = this._buildColoredSegments(path.points);

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
    var segments = [];
    for (var i = 0; i < points.length - 1; i++) {
      var speed = points[i].speed || 0;
      if (!speed && points[i].vx != null && points[i].vy != null) {
        var vz = points[i].vz || 0;
        speed = Math.sqrt(points[i].vx * points[i].vx + points[i].vy * points[i].vy + vz * vz);
      }
      var color = this._speedColor(speed);
      segments.push({
        coords: [this._toLatLng(points[i]), this._toLatLng(points[i + 1])],
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
    var step = Math.max(1, Math.floor(points.length / 8));
    for (var i = 0; i < points.length; i += step) {
      var p = points[i];
      if (!p.timestamp) continue;
      var time = new Date(p.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      var icon = L.divIcon({
        className: 'path-timestamp-label',
        html: `<span>${time}</span>`,
        iconSize: [60, 20],
        iconAnchor: [30, -8],
      });
      var m = L.marker(this._toLatLng(p), { icon: icon, interactive: false });
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
      var pts = this.selectedPath.points;
      if (pts.length) {
        this.animMarker.setLatLng(this._toLatLng(pts[0]));
      }
    }
    this.onPlaybackUpdate({ playing: false, index: 0, total: this.selectedPath?.points?.length || 0 });
  }

  setSpeed(speed) {
    this.playSpeed = speed;
  }

  seekTo(index) {
    if (!this.selectedPath) return;
    var pts = this.selectedPath.points;
    this.playIndex = Math.max(0, Math.min(index, pts.length - 1));
    if (this.animMarker) {
      this.animMarker.setLatLng(this._toLatLng(pts[this.playIndex]));
    }
    this.onPlaybackUpdate({
      playing: this.playing,
      index: this.playIndex,
      total: pts.length,
    });
  }

  _animate() {
    if (!this.playing || !this.selectedPath) return;
    var pts = this.selectedPath.points;

    if (this.playIndex >= pts.length - 1) {
      this.playing = false;
      this.onPlaybackUpdate({ playing: false, index: this.playIndex, total: pts.length });
      return;
    }

    this.playIndex++;
    var p = pts[this.playIndex];
    this.animMarker.setLatLng(this._toLatLng(p));

    var speed = 0;
    if (p.vx != null && p.vy != null) {
      var vz = p.vz || 0;
      speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy + vz * vz);
    } else if (p.speed != null) {
      speed = p.speed;
    }

    var time = p.timestamp ? new Date(p.timestamp * 1000).toLocaleTimeString() : '';
    var zInfo = p.z != null ? `<div class="node-popup-row"><span class="label">Height:</span> ${p.z.toFixed(1)} m</div>` : '';

    this.animMarker.setPopupContent(`
      <div class="device-popup">
        <div class="node-popup-body">
          <div class="node-popup-row"><span class="label">Time:</span> ${time}</div>
          <div class="node-popup-row"><span class="label">Pos:</span> (${(p.x || 0).toFixed(1)}, ${(p.y || 0).toFixed(1)}) m</div>
          ${zInfo}
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

    var baseDelay = 100;
    var delay = baseDelay / this.playSpeed;

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
