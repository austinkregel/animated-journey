(function () {
  var basePath = window.location.pathname.replace(/\/$/, '');

  function apiUrl(path) {
    return basePath + path;
  }

  // ---- Map ----
  var map = initMap('map');
  var selectedNodeType = 'perimeter';

  // ---- Node Placer ----
  var nodePlacer = new NodePlacer(map, {
    onNodeChange: function (nodes) {
      renderNodeList(nodes);
      updateActiveNodesCount(nodes.length);
    },
  });

  // ---- Live View ----
  var liveView = new LiveView(map, {
    apiBase: basePath,
    onDeviceUpdate: function (count) {
      document.getElementById('live-device-count').textContent = count;
      document.getElementById('tracked-devices-count').textContent = count + ' devices';
    },
  });

  // ---- Path Viewer ----
  var pathViewer = new PathViewer(map, {
    apiBase: basePath,
    onPathsLoaded: function (paths) {
      renderPathList(paths);
    },
    onPlaybackUpdate: function (state) {
      updatePlaybackUI(state);
    },
  });

  // ---- Calibration ----
  var calibrationView = new CalibrationView(map, {
    apiBase: basePath,
    onStatusUpdate: function (data) {
      renderCalibrationStatus(data);
    },
  });

  // ---- Tab Switching ----
  var currentTab = 'map';

  document.querySelectorAll('.tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.dataset.tab;
      if (target === currentTab) return;

      deactivateTab(currentTab);
      currentTab = target;
      activateTab(target);

      document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
      document.querySelectorAll('.tab-content').forEach(function (c) { c.classList.remove('active'); });
      tab.classList.add('active');
      var el = document.getElementById('tab-' + target);
      if (el) el.classList.add('active');
    });
  });

  function activateTab(name) {
    switch (name) {
      case 'live':
        liveView.start();
        break;
      case 'paths':
        pathViewer.fetchPaths();
        break;
      case 'calibration':
        calibrationView.start();
        break;
      case 'firmware':
        loadFirmwareNodes();
        break;
      case 'settings':
        loadSettings(false);
        break;
    }
    if (name !== 'paths') {
      pathViewer.clear();
    }
  }

  function deactivateTab(name) {
    switch (name) {
      case 'live':
        liveView.stop();
        break;
      case 'calibration':
        calibrationView.stop();
        break;
    }
  }

  // ---- Node Type Selector ----
  document.querySelectorAll('.node-type-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.node-type-btn').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      selectedNodeType = btn.dataset.type;
    });
  });

  // ---- Map Click -> Place Node ----
  map.on('click', function (e) {
    if (currentTab !== 'map') return;
    var nodeId = prompt('Enter node ID:');
    if (!nodeId) return;
    nodePlacer.addNode(e.latlng, nodeId, selectedNodeType);
  });

  // ---- Node List Rendering ----
  function renderNodeList(nodes) {
    var list = document.getElementById('node-list');
    var empty = document.getElementById('node-list-empty');
    list.innerHTML = '';

    if (!nodes.length) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';

    var colors = nodePlacer.getTypeColors();
    nodes.forEach(function (node) {
      var li = document.createElement('li');
      var color = colors[node.type] || '#6b7280';

      li.innerHTML =
        '<div class="node-list-item">' +
          '<span class="node-color-dot" style="background:' + color + '"></span>' +
          '<span class="node-list-name">' + node.node_id + '</span>' +
          '<span class="node-list-type">' + node.type + '</span>' +
        '</div>';

      var removeBtn = document.createElement('button');
      removeBtn.className = 'node-remove-btn';
      removeBtn.textContent = '\u00D7';
      removeBtn.addEventListener('click', function () {
        nodePlacer.removeNode(node.node_id);
      });
      li.appendChild(removeBtn);
      list.appendChild(li);
    });
  }

  // ---- Save Config ----
  document.getElementById('save-config').addEventListener('click', function () {
    var config = {
      nodes: nodePlacer.getNodes(),
      origin: {
        lat: map.getCenter().lat,
        lng: map.getCenter().lng,
      },
    };

    fetch(apiUrl('/api/config'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        showToast('Configuration saved');
      })
      .catch(function (err) {
        showToast('Save failed: ' + err.message, 'error');
      });
  });

  // ---- Load Config ----
  function loadConfig() {
    fetch(apiUrl('/api/config'))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.origin) {
          map.setView([data.origin.lat, data.origin.lng], 19);
        }
        if (data.nodes) {
          nodePlacer.loadNodes(data.nodes);
        }
      })
      .catch(function () {
        console.warn('Could not load config');
      });
  }

  // ---- Live View Filters ----
  document.getElementById('trail-toggle').addEventListener('change', function () {
    liveView.setTrailMode(this.checked);
  });

  document.querySelectorAll('#device-type-filters input[type="checkbox"]').forEach(function (cb) {
    cb.addEventListener('change', function () {
      var types = [];
      document.querySelectorAll('#device-type-filters input:checked').forEach(function (c) {
        types.push(c.dataset.dtype);
      });
      liveView.setFilter('types', types);
    });
  });

  document.getElementById('filter-rssi').addEventListener('input', function () {
    document.getElementById('filter-rssi-val').textContent = this.value;
    liveView.setFilter('minRssi', parseInt(this.value));
  });

  document.getElementById('filter-time').addEventListener('input', function () {
    document.getElementById('filter-time-val').textContent = this.value;
    liveView.setFilter('timeRange', parseInt(this.value));
  });

  // ---- Path List ----
  function renderPathList(paths) {
    var list = document.getElementById('path-list');
    var empty = document.getElementById('path-list-empty');
    list.innerHTML = '';

    if (!paths.length) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';

    paths.forEach(function (p) {
      var li = document.createElement('li');
      li.className = 'path-list-item';
      li.dataset.pathId = p.id;

      var startTime = p.start_time ? new Date(p.start_time).toLocaleString() : '--';
      var dur = p.duration ? formatDuration(p.duration) : '--';
      var dist = p.distance ? (p.distance < 1000 ? p.distance.toFixed(0) + 'm' : (p.distance / 1000).toFixed(1) + 'km') : '--';

      li.innerHTML =
        '<div class="path-list-meta">' +
          '<span class="path-list-mac">' + (p.mac_hash || '???') + '</span>' +
          '<span class="path-list-info">' + startTime + ' &middot; ' + dur + '</span>' +
        '</div>' +
        '<span class="path-list-distance">' + dist + '</span>';

      li.addEventListener('click', function () {
        document.querySelectorAll('.path-list-item').forEach(function (i) { i.classList.remove('selected'); });
        li.classList.add('selected');
        pathViewer.selectPath(p.id);
        document.getElementById('playback-panel').style.display = 'block';
      });

      list.appendChild(li);
    });
  }

  // ---- Playback Controls ----
  document.getElementById('pb-play').addEventListener('click', function () {
    if (pathViewer.playing) {
      pathViewer.pause();
    } else {
      pathViewer.play();
    }
  });

  document.getElementById('pb-stop').addEventListener('click', function () {
    pathViewer.stop();
  });

  document.querySelectorAll('.speed-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.speed-btn').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      pathViewer.setSpeed(parseInt(btn.dataset.speed));
    });
  });

  document.getElementById('pb-scrub').addEventListener('input', function () {
    pathViewer.seekTo(parseInt(this.value));
  });

  function updatePlaybackUI(state) {
    var playBtn = document.getElementById('pb-play');
    playBtn.innerHTML = state.playing ? '&#9646;&#9646;' : '&#9654;';
    playBtn.classList.toggle('active', state.playing);

    if (state.total > 0) {
      var scrub = document.getElementById('pb-scrub');
      scrub.max = state.total - 1;
      scrub.value = state.index;
    }

    document.getElementById('pb-time-current').textContent = state.time || '--';
  }

  // ---- Calibration ----
  function renderCalibrationStatus(data) {
    if (data.r_squared != null) {
      document.getElementById('cal-r2').textContent = data.r_squared.toFixed(3);
    }
    if (data.sample_count != null) {
      document.getElementById('cal-samples').textContent = data.sample_count;
    }
    if (data.confidence != null) {
      document.getElementById('cal-confidence').textContent = (data.confidence * 100).toFixed(0) + '%';
    }
    if (data.status) {
      document.getElementById('cal-status-text').textContent = data.status;
    }

    if (data.node_params) {
      var container = document.getElementById('cal-node-params');
      container.innerHTML = '';
      Object.entries(data.node_params).forEach(function (entry) {
        var nodeId = entry[0], params = entry[1];
        var row = document.createElement('div');
        row.className = 'cal-node-param-row';
        row.innerHTML =
          '<span class="cal-node-param-name">' + nodeId + '</span>' +
          '<span class="cal-node-param-value">n=' + (params.n || '--') + ' A=' + (params.A || '--') + '</span>';
        container.appendChild(row);
      });
    }

    if (data.rssi_readings) {
      var rssiContainer = document.getElementById('cal-rssi-readings');
      rssiContainer.innerHTML = '';
      Object.entries(data.rssi_readings).forEach(function (entry) {
        var nodeId = entry[0], rssi = entry[1];
        var pct = Math.max(0, Math.min(100, ((rssi + 100) / 70) * 100));
        var color = pct > 60 ? '#22c55e' : pct > 30 ? '#eab308' : '#ef4444';

        var bar = document.createElement('div');
        bar.className = 'cal-rssi-bar';
        bar.innerHTML =
          '<span class="cal-rssi-label">' + nodeId + '</span>' +
          '<div class="cal-rssi-bar-track"><div class="cal-rssi-bar-fill" style="width:' + pct + '%;background:' + color + '"></div></div>' +
          '<span class="cal-rssi-value">' + rssi + ' dBm</span>';
        rssiContainer.appendChild(bar);
      });
    }
  }

  document.getElementById('cal-start-manual').addEventListener('click', function () {
    var nodes = nodePlacer.getNodes();
    if (!nodes.length) {
      showToast('Place scanner nodes first', 'error');
      return;
    }
    var waypoints = nodes.map(function (n) { return { lat: n.lat, lng: n.lng, node_id: n.node_id }; });
    calibrationView.startManualCalibration(waypoints);
    document.getElementById('cal-mark-here').disabled = false;
    document.getElementById('cal-stop-manual').style.display = '';
    updateCalManualProgress();
  });

  document.getElementById('cal-mark-here').addEventListener('click', function () {
    calibrationView.markCurrentWaypoint();
    updateCalManualProgress();

    if (calibrationView.getCurrentWaypointIndex() >= calibrationView.getTotalWaypoints()) {
      document.getElementById('cal-mark-here').disabled = true;
      showToast('Calibration complete!');
    }
  });

  document.getElementById('cal-stop-manual').addEventListener('click', function () {
    calibrationView.stopManualCalibration();
    document.getElementById('cal-mark-here').disabled = true;
    document.getElementById('cal-stop-manual').style.display = 'none';
    document.getElementById('cal-manual-progress').textContent = '';
  });

  function updateCalManualProgress() {
    var idx = calibrationView.getCurrentWaypointIndex();
    var total = calibrationView.getTotalWaypoints();
    document.getElementById('cal-manual-progress').textContent =
      'Waypoint ' + (idx + 1) + ' of ' + total;
  }

  // ---- Firmware ----
  function loadFirmwareNodes() {
    fetch(apiUrl('/api/nodes'))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderFirmwareNodes(data.nodes || []);
      })
      .catch(function () {
        document.getElementById('firmware-node-list').innerHTML =
          '<div class="empty-state">Could not load nodes</div>';
      });
  }

  function renderFirmwareNodes(nodes) {
    var container = document.getElementById('firmware-node-list');
    container.innerHTML = '';

    if (!nodes.length) {
      container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#9881;</div>No nodes found</div>';
      return;
    }

    nodes.forEach(function (node) {
      var statusClass = node.online ? 'online' : 'offline';
      var statusText = node.online ? 'Online' : 'Offline';

      var card = document.createElement('div');
      card.className = 'firmware-node-card';
      card.innerHTML =
        '<div class="firmware-node-header">' +
          '<span class="firmware-node-name">' + node.node_id + '</span>' +
          '<div class="firmware-node-status">' +
            '<span class="status-dot ' + statusClass + '"></span> ' + statusText +
          '</div>' +
        '</div>' +
        '<div class="firmware-node-details">' +
          '<div class="detail-row"><span class="detail-label">Type</span><span>' + (node.type || '--') + '</span></div>' +
          '<div class="detail-row"><span class="detail-label">Firmware</span><span>' + (node.firmware_version || '--') + '</span></div>' +
          '<div class="detail-row"><span class="detail-label">IP</span><span>' + (node.ip || '--') + '</span></div>' +
          '<div class="detail-row"><span class="detail-label">Uptime</span><span>' + (node.uptime ? formatDuration(node.uptime) : '--') + '</span></div>' +
        '</div>' +
        '<div class="btn-group">' +
          '<button class="btn-primary btn-sm ota-update-btn" data-node-id="' + node.node_id + '"' +
            (node.online ? '' : ' disabled') + '>OTA Update</button>' +
          '<button class="btn-secondary btn-sm ota-restart-btn" data-node-id="' + node.node_id + '"' +
            (node.online ? '' : ' disabled') + '>Restart</button>' +
        '</div>' +
        '<div class="firmware-progress" style="display:none;" data-progress-for="' + node.node_id + '">' +
          '<div class="progress-bar"><div class="progress-bar-fill" style="width:0%"></div></div>' +
          '<div class="progress-text">Preparing...</div>' +
        '</div>';

      container.appendChild(card);
    });

    container.querySelectorAll('.ota-update-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        triggerOtaUpdate(btn.dataset.nodeId);
      });
    });

    container.querySelectorAll('.ota-restart-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        restartNode(btn.dataset.nodeId);
      });
    });
  }

  function triggerOtaUpdate(nodeId) {
    var progressEl = document.querySelector('[data-progress-for="' + nodeId + '"]');
    if (progressEl) progressEl.style.display = 'block';

    fetch(apiUrl('/api/ota/update'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_id: nodeId }),
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        pollOtaProgress(nodeId);
      })
      .catch(function (err) {
        showToast('OTA failed: ' + err.message, 'error');
        if (progressEl) progressEl.style.display = 'none';
      });
  }

  function pollOtaProgress(nodeId) {
    var progressEl = document.querySelector('[data-progress-for="' + nodeId + '"]');
    if (!progressEl) return;

    var fill = progressEl.querySelector('.progress-bar-fill');
    var text = progressEl.querySelector('.progress-text');

    var interval = setInterval(function () {
      fetch(apiUrl('/api/ota/status/' + nodeId))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var pct = data.progress || 0;
          fill.style.width = pct + '%';
          text.textContent = data.status || (pct + '%');

          if (data.status === 'complete') {
            fill.classList.add('success');
            text.textContent = 'Update complete!';
            clearInterval(interval);
            setTimeout(loadFirmwareNodes, 3000);
          } else if (data.status === 'error') {
            fill.classList.add('error');
            text.textContent = 'Update failed: ' + (data.error || 'unknown');
            clearInterval(interval);
          }
        })
        .catch(function () {
          clearInterval(interval);
        });
    }, 1000);
  }

  function restartNode(nodeId) {
    if (!confirm('Restart node ' + nodeId + '?')) return;
    fetch(apiUrl('/api/nodes/' + nodeId + '/restart'), { method: 'POST' })
      .then(function () {
        showToast(nodeId + ' restarting...');
        setTimeout(loadFirmwareNodes, 5000);
      })
      .catch(function (err) {
        showToast('Restart failed: ' + err.message, 'error');
      });
  }

  // ---- Firmware Upload ----
  var uploadZone = document.getElementById('firmware-upload-zone');
  var fileInput = document.getElementById('firmware-file-input');

  uploadZone.addEventListener('click', function () {
    fileInput.click();
  });

  uploadZone.addEventListener('dragover', function (e) {
    e.preventDefault();
    uploadZone.classList.add('dragover');
  });

  uploadZone.addEventListener('dragleave', function () {
    uploadZone.classList.remove('dragover');
  });

  uploadZone.addEventListener('drop', function (e) {
    e.preventDefault();
    uploadZone.classList.remove('dragover');
    var files = e.dataTransfer.files;
    if (files.length) uploadFirmwareFile(files[0]);
  });

  fileInput.addEventListener('change', function () {
    if (fileInput.files.length) uploadFirmwareFile(fileInput.files[0]);
  });

  function uploadFirmwareFile(file) {
    var statusEl = document.getElementById('firmware-upload-status');
    statusEl.textContent = 'Uploading ' + file.name + '...';

    var formData = new FormData();
    formData.append('firmware', file);

    fetch(apiUrl('/api/ota/upload'), { method: 'POST', body: formData })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        statusEl.textContent = 'Uploaded: ' + (data.filename || file.name);
        showToast('Firmware uploaded');
      })
      .catch(function (err) {
        statusEl.textContent = 'Upload failed: ' + err.message;
        showToast('Upload failed', 'error');
      });
  }

  // ---- Settings ----
  function loadSettings(loadOverlay) {
    fetch(apiUrl('/api/settings'))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.mqtt_prefix != null)
          document.getElementById('setting-mqtt-prefix').value = data.mqtt_prefix;
        if (data.scan_interval != null)
          document.getElementById('setting-scan-interval').value = data.scan_interval;
        if (data.report_interval != null)
          document.getElementById('setting-report-interval').value = data.report_interval;
        if (data.active_scan != null)
          document.getElementById('setting-active-scan').checked = data.active_scan;
        if (data.device_timeout != null)
          document.getElementById('setting-device-timeout').value = data.device_timeout;
        if (data.path_min_points != null)
          document.getElementById('setting-path-min-points').value = data.path_min_points;
        if (loadOverlay) {
          loadOverlayFromSettings(data);
        }
      })
      .catch(function () {
        console.warn('Could not load settings');
      });
  }

  document.getElementById('save-settings').addEventListener('click', function () {
    var settings = {
      mqtt_prefix: document.getElementById('setting-mqtt-prefix').value,
      scan_interval: parseInt(document.getElementById('setting-scan-interval').value),
      report_interval: parseInt(document.getElementById('setting-report-interval').value),
      active_scan: document.getElementById('setting-active-scan').checked,
      device_timeout: parseInt(document.getElementById('setting-device-timeout').value),
      path_min_points: parseInt(document.getElementById('setting-path-min-points').value),
    };

    fetch(apiUrl('/api/settings'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    })
      .then(function (r) { return r.json(); })
      .then(function () {
        showToast('Settings saved');
      })
      .catch(function (err) {
        showToast('Save failed: ' + err.message, 'error');
      });
  });

  // ---- Map Overlay ----
  var overlayUploadZone = document.getElementById('overlay-upload-zone');
  var overlayFileInput = document.getElementById('overlay-file-input');
  var overlayHasImage = false;

  overlayUploadZone.addEventListener('click', function () {
    overlayFileInput.click();
  });

  overlayUploadZone.addEventListener('dragover', function (e) {
    e.preventDefault();
    overlayUploadZone.classList.add('dragover');
  });

  overlayUploadZone.addEventListener('dragleave', function () {
    overlayUploadZone.classList.remove('dragover');
  });

  overlayUploadZone.addEventListener('drop', function (e) {
    e.preventDefault();
    overlayUploadZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) uploadOverlayFile(e.dataTransfer.files[0]);
  });

  overlayFileInput.addEventListener('change', function () {
    if (overlayFileInput.files.length) uploadOverlayFile(overlayFileInput.files[0]);
  });

  function uploadOverlayFile(file) {
    var statusEl = document.getElementById('overlay-upload-status');
    statusEl.textContent = 'Uploading ' + file.name + '...';

    var formData = new FormData();
    formData.append('image', file);

    fetch(apiUrl('/api/overlay/upload'), { method: 'POST', body: formData })
      .then(function (r) { return r.json(); })
      .then(function () {
        statusEl.textContent = '';
        showToast('Overlay image uploaded');
        showOverlayControls();
        updateOverlayPreview();
        probeOverlayImageSize(function () {
          calcOverlayBounds();
        });
      })
      .catch(function (err) {
        statusEl.textContent = 'Upload failed: ' + err.message;
        showToast('Upload failed', 'error');
      });
  }

  function showOverlayControls() {
    overlayHasImage = true;
    overlayUploadZone.classList.add('has-image');
    document.getElementById('overlay-upload-label').textContent = 'Replace image (click or drop)';
    document.getElementById('overlay-bounds-section').style.display = '';
    document.getElementById('overlay-preview').style.display = '';
  }

  function hideOverlayControls() {
    overlayHasImage = false;
    overlayUploadZone.classList.remove('has-image');
    document.getElementById('overlay-upload-label').textContent = 'Drop image here or click to select';
    document.getElementById('overlay-bounds-section').style.display = 'none';
    document.getElementById('overlay-preview').style.display = 'none';
  }

  function updateOverlayPreview() {
    var img = document.getElementById('overlay-preview-img');
    img.src = apiUrl('/api/overlay/image') + '?t=' + Date.now();
    document.getElementById('overlay-preview').style.display = '';
  }

  var overlayImageSize = null;

  function calcOverlayBounds() {
    var lat = parseFloat(document.getElementById('overlay-center-lat').value);
    var lng = parseFloat(document.getElementById('overlay-center-lng').value);
    var zoom = parseInt(document.getElementById('overlay-zoom').value);
    if (isNaN(lat) || isNaN(lng) || isNaN(zoom)) return;
    if (!overlayImageSize) return;

    var groundRes = 156543.03392 * Math.cos(lat * Math.PI / 180) / Math.pow(2, zoom);
    var widthM = overlayImageSize.w * groundRes;
    var heightM = overlayImageSize.h * groundRes;

    var latPerM = 1.0 / 111320.0;
    var lngPerM = 1.0 / (111320.0 * Math.cos(lat * Math.PI / 180));

    var halfLat = (heightM / 2) * latPerM;
    var halfLng = (widthM / 2) * lngPerM;

    document.getElementById('overlay-north').value = (lat + halfLat).toFixed(8);
    document.getElementById('overlay-south').value = (lat - halfLat).toFixed(8);
    document.getElementById('overlay-east').value = (lng + halfLng).toFixed(8);
    document.getElementById('overlay-west').value = (lng - halfLng).toFixed(8);
  }

  function probeOverlayImageSize(callback) {
    var img = new Image();
    img.onload = function () {
      overlayImageSize = { w: img.naturalWidth, h: img.naturalHeight };
      if (callback) callback();
    };
    img.src = apiUrl('/api/overlay/image') + '?t=' + Date.now();
  }

  document.getElementById('overlay-center-lat').addEventListener('input', calcOverlayBounds);
  document.getElementById('overlay-center-lng').addEventListener('input', calcOverlayBounds);
  document.getElementById('overlay-zoom').addEventListener('input', calcOverlayBounds);

  document.getElementById('overlay-use-map-center').addEventListener('click', function () {
    var center = map.getCenter();
    var zoom = map.getZoom();
    document.getElementById('overlay-center-lat').value = center.lat.toFixed(8);
    document.getElementById('overlay-center-lng').value = center.lng.toFixed(8);
    document.getElementById('overlay-zoom').value = Math.round(zoom);
    calcOverlayBounds();
    showToast('Center captured from map');
  });

  function getOverlayBounds() {
    var n = parseFloat(document.getElementById('overlay-north').value);
    var s = parseFloat(document.getElementById('overlay-south').value);
    var w = parseFloat(document.getElementById('overlay-west').value);
    var e = parseFloat(document.getElementById('overlay-east').value);
    if (isNaN(n) || isNaN(s) || isNaN(w) || isNaN(e)) return null;
    return { north: n, south: s, west: w, east: e };
  }

  document.getElementById('overlay-apply').addEventListener('click', function () {
    var bounds = getOverlayBounds();
    if (!bounds) {
      showToast('Enter center coordinates and zoom first', 'error');
      return;
    }
    var opacity = parseInt(document.getElementById('overlay-opacity').value) / 100;
    var rotation = parseFloat(document.getElementById('overlay-rotation').value) || 0;
    var imageUrl = apiUrl('/api/overlay/image') + '?t=' + Date.now();
    map.setOverlayImage(imageUrl, bounds, { opacity: opacity, rotation: rotation });

    var settings = {
      overlay_bounds: bounds,
      overlay_opacity: parseInt(document.getElementById('overlay-opacity').value),
      overlay_rotation: rotation,
      overlay_center_lat: parseFloat(document.getElementById('overlay-center-lat').value),
      overlay_center_lng: parseFloat(document.getElementById('overlay-center-lng').value),
      overlay_zoom: parseInt(document.getElementById('overlay-zoom').value),
    };
    fetch(apiUrl('/api/settings'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }).then(function () {
      showToast('Overlay applied and saved');
    });
  });

  document.getElementById('overlay-fit').addEventListener('click', function () {
    map.fitOverlay();
  });

  document.getElementById('overlay-remove').addEventListener('click', function () {
    if (!confirm('Remove the overlay image?')) return;
    fetch(apiUrl('/api/overlay'), { method: 'DELETE' })
      .then(function () {
        map.removeOverlayImage();
        hideOverlayControls();
        showToast('Overlay removed');
      });
  });

  document.getElementById('overlay-opacity').addEventListener('input', function () {
    var pct = parseInt(this.value);
    document.getElementById('overlay-opacity-val').textContent = pct;
    map.setOverlayOpacity(pct / 100);
  });

  document.getElementById('overlay-rotation').addEventListener('input', function () {
    var deg = parseFloat(this.value);
    document.getElementById('overlay-rotation-val').textContent = deg.toFixed(1);
    map.setOverlayRotation(deg);
  });

  function syncBoundsFields() {
    var b = map._overlayBounds;
    if (!b) return;
    document.getElementById('overlay-north').value = b.north.toFixed(8);
    document.getElementById('overlay-south').value = b.south.toFixed(8);
    document.getElementById('overlay-east').value = b.east.toFixed(8);
    document.getElementById('overlay-west').value = b.west.toFixed(8);
    document.getElementById('overlay-center-lat').value = ((b.north + b.south) / 2).toFixed(8);
    document.getElementById('overlay-center-lng').value = ((b.east + b.west) / 2).toFixed(8);
  }

  document.querySelectorAll('.overlay-nudge-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var dir = btn.dataset.dir;
      var step = parseFloat(document.getElementById('overlay-nudge-step').value);
      var dlat = 0, dlng = 0;
      if (dir === 'n') dlat = step;
      else if (dir === 's') dlat = -step;
      else if (dir === 'e') dlng = step;
      else if (dir === 'w') dlng = -step;
      map.nudgeOverlay(dlat, dlng);
      syncBoundsFields();
    });
  });

  var overlayScale = 1.0;

  document.getElementById('overlay-scale-up').addEventListener('click', function () {
    var step = parseFloat(document.getElementById('overlay-scale-step').value);
    var factor = 1 + step;
    overlayScale *= factor;
    map.scaleOverlay(factor);
    syncBoundsFields();
    document.getElementById('overlay-scale-label').textContent = (overlayScale * 100).toFixed(1) + '%';
  });

  document.getElementById('overlay-scale-down').addEventListener('click', function () {
    var step = parseFloat(document.getElementById('overlay-scale-step').value);
    var factor = 1 - step;
    overlayScale *= factor;
    map.scaleOverlay(factor);
    syncBoundsFields();
    document.getElementById('overlay-scale-label').textContent = (overlayScale * 100).toFixed(1) + '%';
  });

  function loadOverlayFromSettings(settings) {
    if (!settings.overlay_bounds) return;

    var img = new Image();
    img.onload = function () {
      overlayImageSize = { w: img.naturalWidth, h: img.naturalHeight };
      showOverlayControls();
      updateOverlayPreview();

      if (settings.overlay_center_lat != null)
        document.getElementById('overlay-center-lat').value = settings.overlay_center_lat;
      if (settings.overlay_center_lng != null)
        document.getElementById('overlay-center-lng').value = settings.overlay_center_lng;
      if (settings.overlay_zoom != null)
        document.getElementById('overlay-zoom').value = settings.overlay_zoom;

      var b = settings.overlay_bounds;
      document.getElementById('overlay-north').value = b.north;
      document.getElementById('overlay-south').value = b.south;
      document.getElementById('overlay-west').value = b.west;
      document.getElementById('overlay-east').value = b.east;

      var opacity = settings.overlay_opacity != null ? settings.overlay_opacity : 85;
      document.getElementById('overlay-opacity').value = opacity;
      document.getElementById('overlay-opacity-val').textContent = opacity;

      var rotation = settings.overlay_rotation || 0;
      document.getElementById('overlay-rotation').value = rotation;
      document.getElementById('overlay-rotation-val').textContent = rotation.toFixed(1);

      map.setOverlayImage(apiUrl('/api/overlay/image'), b, { opacity: opacity / 100, rotation: rotation });
    };
    img.src = apiUrl('/api/overlay/image') + '?t=' + Date.now();
  }

  // ---- Status Polling ----
  function pollStatus() {
    fetch(apiUrl('/api/status'))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var dot = document.getElementById('mqtt-status-dot');
        var text = document.getElementById('mqtt-status-text');
        if (data.mqtt_connected) {
          dot.className = 'status-dot online';
          text.textContent = 'MQTT';
        } else {
          dot.className = 'status-dot offline';
          text.textContent = 'MQTT';
        }
        if (data.node_count != null) {
          updateActiveNodesCount(data.node_count);
        }
        if (data.tracked_devices != null) {
          document.getElementById('tracked-devices-count').textContent = data.tracked_devices + ' devices';
        }

        if (data.node_statuses) {
          Object.entries(data.node_statuses).forEach(function (entry) {
            var nid = entry[0], ns = entry[1];
            nodePlacer.updateNodeStatus(nid, ns.online ? 'online' : 'offline', ns.last_seen);
          });
        }
      })
      .catch(function () {});
  }

  function updateActiveNodesCount(count) {
    document.getElementById('active-nodes-count').textContent = count + ' nodes';
  }

  // ---- Toast ----
  function showToast(message, type) {
    var existing = document.querySelector('.toast');
    if (existing) existing.remove();

    var toast = document.createElement('div');
    toast.className = 'toast' + (type === 'error' ? ' toast-error' : '');
    toast.textContent = message;
    toast.style.cssText =
      'position:fixed;bottom:60px;left:50%;transform:translateX(-50%);' +
      'padding:10px 20px;border-radius:8px;font-size:13px;z-index:10001;' +
      'background:' + (type === 'error' ? '#3a1a1a' : '#1e2e1e') + ';' +
      'color:' + (type === 'error' ? '#f66' : '#6f6') + ';' +
      'border:1px solid ' + (type === 'error' ? '#5a2a2a' : '#2a4a2a') + ';' +
      'box-shadow:0 4px 12px rgba(0,0,0,0.4);';
    document.body.appendChild(toast);
    setTimeout(function () { toast.remove(); }, 3000);
  }

  // ---- Helpers ----
  function formatDuration(seconds) {
    if (seconds < 60) return seconds + 's';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    return h + 'h ' + m + 'm';
  }

  // ---- Init ----
  loadConfig();
  loadSettings(true);
  pollStatus();
  setInterval(pollStatus, 10000);
})();
