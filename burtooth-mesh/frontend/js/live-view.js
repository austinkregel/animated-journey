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
        this._updateDevices(data.devices || []);
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
    const now = Date.now();
    const cutoff = now - this.filters.timeRange * 1000;
    const seen = new Set();

    deviceList.forEach((dev) => {
      if (!this.filters.types.has(dev.type || 'unknown')) return;
      if ((dev.rssi || -100) < this.filters.minRssi) return;

      const ts = dev.timestamp ? new Date(dev.timestamp).getTime() : now;
      if (ts < cutoff) return;

      const id = dev.mac_hash;
      seen.add(id);
      const type = dev.type || 'unknown';
      const color = this.typeColors[type] || this.typeColors.unknown;

      if (!this.devices[id]) {
        this.devices[id] = {
          marker: null,
          trail: [],
          trailLine: null,
          data: dev,
        };
      }

      const entry = this.devices[id];
      entry.data = dev;

      const latlng = L.latLng(dev.lat, dev.lng);

      if (entry.marker) {
        entry.marker.setLatLng(latlng);
      } else {
        entry.marker = L.circleMarker(latlng, {
          radius: 6,
          fillColor: color,
          fillOpacity: 0.85,
          color: '#fff',
          weight: 1.5,
        }).addTo(this.deviceLayer);

        entry.marker.bindPopup('', { className: 'dark-popup', maxWidth: 280 });
        entry.marker.on('click', () => {
          entry.marker.setPopupContent(this._buildDevicePopup(entry.data));
        });
      }

      entry.marker.setStyle({ fillColor: color });

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
        const entry = this.devices[id];
        const age = now - new Date(entry.data.timestamp || 0).getTime();
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
      if (!dn.lat || !dn.lng) return;
      const radius = this._rssiToRadius(dn.rssi);
      L.circle([dn.lat, dn.lng], {
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
    const clamped = Math.max(-100, Math.min(-30, rssi));
    return ((clamped + 100) / 70) * 2 + 3;
  }

  _buildDevicePopup(dev) {
    const type = dev.type || 'unknown';
    const color = this.typeColors[type] || this.typeColors.unknown;
    const lastSeen = dev.timestamp
      ? new Date(dev.timestamp).toLocaleTimeString()
      : '—';

    let nodesHtml = '';
    if (dev.rssi_per_node) {
      nodesHtml = Object.entries(dev.rssi_per_node)
        .map(([nid, rssi]) => `<div class="node-popup-row"><span class="label">${nid}:</span> ${rssi} dBm</div>`)
        .join('');
    }

    const speed = dev.speed != null
      ? `${dev.speed.toFixed(1)} m/s`
      : '—';

    return `
      <div class="device-popup">
        <div class="device-popup-header">
          <span class="device-type-badge" style="background:${color}">${type}</span>
          <strong>${dev.mac_hash || '???'}</strong>
        </div>
        <div class="node-popup-body">
          <div class="node-popup-row"><span class="label">Vendor:</span> ${dev.vendor || 'Unknown'}</div>
          <div class="node-popup-row"><span class="label">Signals:</span> ${(dev.signal_types || []).join(', ') || '—'}</div>
          <div class="node-popup-row"><span class="label">Speed:</span> ${speed}</div>
          <div class="node-popup-row"><span class="label">Last seen:</span> ${lastSeen}</div>
          ${nodesHtml ? '<hr class="popup-divider">' + nodesHtml : ''}
        </div>
      </div>
    `;
  }

  _removeDevice(id) {
    const entry = this.devices[id];
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
