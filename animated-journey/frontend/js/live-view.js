class LiveView {
  constructor(map, options = {}) {
    this.map = map;
    this.apiBase = options.apiBase || '';
    this.pollInterval = options.pollInterval || 2000;
    this.maxTrailLength = options.maxTrailLength || 30;
    this.active = false;
    this.pollTimer = null;

    this.devices = {};
    this.deviceLayer = L.layerGroup().addTo(this.map);
    this.rssiLayer = L.layerGroup().addTo(this.map);
    this.trailLayer = L.layerGroup().addTo(this.map);
    this.trailMode = true;

    this.filters = {
      types: new Set(['phone', 'wearable', 'car', 'unknown']),
      minRssi: -100,
      timeRange: 300,
    };

    this.typeColors = {
      phone: '#3b82f6',
      wearable: '#22c55e',
      car: '#ef4444',
      unknown: '#9ca3af',
    };

    this.onDeviceUpdate = options.onDeviceUpdate || (() => {});
  }

  start() {
    if (this.active) return;
    this.active = true;
    this._poll();
  }

  stop() {
    this.active = false;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  }

  _poll() {
    if (!this.active) return;

    fetch(this.apiBase + '/api/positions')
      .then((r) => r.json())
      .then((data) => {
        this._updateDevices(data.devices || data || []);
        this.onDeviceUpdate(this.getDeviceCount());
      })
      .catch((err) => {
        console.warn('LiveView poll error:', err);
      })
      .finally(() => {
        if (this.active) {
          this.pollTimer = setTimeout(() => this._poll(), this.pollInterval);
        }
      });
  }

  _updateDevices(deviceList) {
    var now = Date.now();
    var cutoff = now - this.filters.timeRange * 1000;
    var seen = new Set();

    this.rssiLayer.clearLayers();

    deviceList.forEach((dev) => {
      if (!this.filters.types.has(dev.type || 'unknown')) return;
      if ((dev.rssi || -100) < this.filters.minRssi) return;

      var ts = dev.last_seen ? dev.last_seen * 1000 : (dev.timestamp ? new Date(dev.timestamp).getTime() : now);
      if (ts < cutoff) return;

      var id = dev.mac_hash;
      seen.add(id);
      var type = dev.type || 'unknown';
      var color = this.typeColors[type] || this.typeColors.unknown;

      // Derive z-based opacity: higher z = more opaque ring
      var zOpacity = dev.z != null ? Math.min(1, 0.5 + Math.abs(dev.z) / 20) : 0.85;

      if (!this.devices[id]) {
        this.devices[id] = {
          marker: null,
          trail: [],
          trailLine: null,
          data: dev,
        };
      }

      var entry = this.devices[id];
      entry.data = dev;

      // CRS.Simple: latLng(y, x)
      var latlng = L.latLng(dev.y, dev.x);

      if (entry.marker) {
        entry.marker.setLatLng(latlng);
      } else {
        entry.marker = L.circleMarker(latlng, {
          radius: 6,
          fillColor: color,
          fillOpacity: zOpacity,
          color: '#fff',
          weight: 1.5,
        }).addTo(this.deviceLayer);

        entry.marker.bindPopup('', { className: 'dark-popup', maxWidth: 280 });
        entry.marker.on('click', () => {
          entry.marker.setPopupContent(this._buildDevicePopup(entry.data));
        });
      }

      entry.marker.setStyle({ fillColor: color, fillOpacity: zOpacity });

      if (this.trailMode) {
        entry.trail.push(latlng);
        if (entry.trail.length > this.maxTrailLength) {
          entry.trail.shift();
        }
        this._drawTrail(entry, color);
      }

      this._drawRssiRings(dev, color);
    });

    Object.keys(this.devices).forEach((id) => {
      if (!seen.has(id)) {
        var entry = this.devices[id];
        var lastSeen = entry.data.last_seen ? entry.data.last_seen * 1000 : 0;
        var age = now - lastSeen;
        if (age > this.filters.timeRange * 1000) {
          this._removeDevice(id);
        }
      }
    });
  }

  _drawTrail(entry, color) {
    if (entry.trailLine) {
      this.trailLayer.removeLayer(entry.trailLine);
    }
    if (entry.trail.length < 2) return;

    entry.trailLine = L.polyline(entry.trail, {
      color: color,
      weight: 2,
      opacity: 0.5,
      dashArray: '4 6',
    }).addTo(this.trailLayer);
  }

  _drawRssiRings(dev, color) {
    if (!dev.detecting_nodes) return;
    dev.detecting_nodes.forEach((dn) => {
      if (dn.x == null || dn.y == null) return;
      var radius = this._rssiToRadius(dn.rssi);
      L.circle([dn.y, dn.x], {
        radius: radius,
        color: color,
        fillColor: color,
        fillOpacity: 0.06,
        weight: 1,
        opacity: 0.3,
      }).addTo(this.rssiLayer);
    });
  }

  _rssiToRadius(rssi) {
    var clamped = Math.max(-100, Math.min(-30, rssi));
    return ((clamped + 100) / 70) * 2 + 3;
  }

  _floorLabel(z) {
    if (z == null) return '';
    if (z < 1.5) return 'Ground';
    var floor = Math.round(z / 3);
    return 'Floor ' + floor;
  }

  _buildDevicePopup(dev) {
    var type = dev.type || 'unknown';
    var color = this.typeColors[type] || this.typeColors.unknown;
    var lastSeen = dev.last_seen
      ? new Date(dev.last_seen * 1000).toLocaleTimeString()
      : (dev.timestamp ? new Date(dev.timestamp).toLocaleTimeString() : '\u2014');

    var nodesHtml = '';
    if (dev.rssi_per_node) {
      nodesHtml = Object.entries(dev.rssi_per_node)
        .map(([nid, rssi]) => `<div class="node-popup-row"><span class="label">${nid}:</span> ${rssi} dBm</div>`)
        .join('');
    }

    var speed3d = '\u2014';
    if (dev.vx != null && dev.vy != null) {
      var vz = dev.vz || 0;
      var s = Math.sqrt(dev.vx * dev.vx + dev.vy * dev.vy + vz * vz);
      speed3d = s.toFixed(1) + ' m/s';
    } else if (dev.speed != null) {
      speed3d = dev.speed.toFixed(1) + ' m/s';
    }

    var zInfo = '';
    if (dev.z != null) {
      zInfo = `<div class="node-popup-row"><span class="label">Height:</span> ${dev.z.toFixed(1)} m (${this._floorLabel(dev.z)})</div>`;
    }

    return `
      <div class="device-popup">
        <div class="device-popup-header">
          <span class="device-type-badge" style="background:${color}">${type}</span>
          <strong>${dev.mac_hash || '???'}</strong>
        </div>
        <div class="node-popup-body">
          <div class="node-popup-row"><span class="label">Pos:</span> (${(dev.x || 0).toFixed(1)}, ${(dev.y || 0).toFixed(1)}) m</div>
          ${zInfo}
          <div class="node-popup-row"><span class="label">Vendor:</span> ${dev.vendor || 'Unknown'}</div>
          <div class="node-popup-row"><span class="label">Signals:</span> ${(dev.signal_types || []).join(', ') || '\u2014'}</div>
          <div class="node-popup-row"><span class="label">Speed:</span> ${speed3d}</div>
          <div class="node-popup-row"><span class="label">Last seen:</span> ${lastSeen}</div>
          ${nodesHtml ? '<hr class="popup-divider">' + nodesHtml : ''}
        </div>
      </div>
    `;
  }

  _removeDevice(id) {
    var entry = this.devices[id];
    if (!entry) return;
    if (entry.marker) this.deviceLayer.removeLayer(entry.marker);
    if (entry.trailLine) this.trailLayer.removeLayer(entry.trailLine);
    delete this.devices[id];
  }

  setFilter(key, value) {
    if (key === 'types') {
      this.filters.types = new Set(value);
    } else {
      this.filters[key] = value;
    }
  }

  setTrailMode(enabled) {
    this.trailMode = enabled;
    if (!enabled) {
      this.trailLayer.clearLayers();
      Object.values(this.devices).forEach((e) => {
        e.trail = [];
        e.trailLine = null;
      });
    }
  }

  clearDevices() {
    this.deviceLayer.clearLayers();
    this.trailLayer.clearLayers();
    this.rssiLayer.clearLayers();
    this.devices = {};
  }

  getDeviceCount() {
    return Object.keys(this.devices).length;
  }

  destroy() {
    this.stop();
    this.clearDevices();
    this.map.removeLayer(this.deviceLayer);
    this.map.removeLayer(this.trailLayer);
    this.map.removeLayer(this.rssiLayer);
  }
}
