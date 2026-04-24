(function () {
  var basePath = window.location.pathname.replace(/\/$/, '');

  function apiUrl(path) {
    return basePath + path;
  }

  // ---- Modal Dialog ----
  function showModal(title, fields, callback) {
    var overlay = document.getElementById('modal-overlay');
    var titleEl = document.getElementById('modal-title');
    var bodyEl = document.getElementById('modal-body');
    var actionsEl = document.getElementById('modal-actions');

    titleEl.textContent = title;
    bodyEl.innerHTML = '';
    actionsEl.innerHTML = '';

    var inputs = {};
    fields.forEach(function (f) {
      var label = document.createElement('label');
      label.textContent = f.label;
      bodyEl.appendChild(label);

      if (f.type === 'select') {
        var select = document.createElement('select');
        f.options.forEach(function (opt) {
          var o = document.createElement('option');
          o.value = opt.value;
          o.textContent = opt.label;
          if (opt.value === f.value) o.selected = true;
          select.appendChild(o);
        });
        bodyEl.appendChild(select);
        inputs[f.id] = select;
      } else {
        var input = document.createElement('input');
        input.type = f.type || 'text';
        input.placeholder = f.placeholder || '';
        input.value = f.value != null ? f.value : '';
        if (f.step) input.step = f.step;
        bodyEl.appendChild(input);
        inputs[f.id] = input;
      }
    });

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn-secondary btn-sm';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', function () { overlay.style.display = 'none'; });

    var okBtn = document.createElement('button');
    okBtn.className = 'btn-primary btn-sm';
    okBtn.textContent = 'OK';
    okBtn.addEventListener('click', function () {
      var result = {};
      Object.keys(inputs).forEach(function (k) { result[k] = inputs[k].value; });
      overlay.style.display = 'none';
      callback(result);
    });

    actionsEl.appendChild(cancelBtn);
    actionsEl.appendChild(okBtn);
    overlay.style.display = 'flex';

    var firstInput = bodyEl.querySelector('input');
    if (firstInput) setTimeout(function () { firstInput.focus(); }, 50);
  }

  function showConfirm(message, callback) {
    showModal('Confirm', [{ id: '_msg', label: message, type: 'text', placeholder: '', value: '' }], function () {
      callback();
    });
    var bodyEl = document.getElementById('modal-body');
    var input = bodyEl.querySelector('input');
    if (input) input.style.display = 'none';
  }

  // ---- Map ----
  var map = initMap('map');
  var selectedNodeType = 'perimeter';
  var gridVisible = false;

  // ---- Node Placer ----
  var nodePlacer = new NodePlacer(map, {
    onNodeChange: function (nodes) {
      renderNodeList(nodes);
      updateActiveNodesCount(nodes.length);
    },
  });

  window._showNodeEditModal = function (node, placer) {
    var types = Object.keys(placer.getTypeColors());
    var typeOptions = types.map(function (t) { return { value: t, label: t.charAt(0).toUpperCase() + t.slice(1) }; });
    showModal('Edit Node', [
      { id: 'nodeId', label: 'Node ID', type: 'text', value: node.node_id },
      { id: 'nodeType', label: 'Type', type: 'select', options: typeOptions, value: node.type },
      { id: 'nodeZ', label: 'Height (m)', type: 'number', value: node.z || 0, step: '0.1' },
    ], function (result) {
      placer.applyNodeEdit(node, result.nodeId, result.nodeType, result.nodeZ);
    });
  };

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
    var clickX = e.latlng.lng;
    var clickY = e.latlng.lat;

    if (placingNodeId) {
      var nid = placingNodeId;
      placingNodeId = null;
      document.getElementById('map').style.cursor = '';
      nodePlacer.placeDiscoveredNode(nid, { x: clickX, y: clickY }, selectedNodeType);
      showToast(nid + ' placed on map');
      return;
    }

    showModal('Place Node', [
      { id: 'nodeId', label: 'Node ID', type: 'text', placeholder: 'e.g. front-porch' },
      { id: 'nodeZ', label: 'Height (m)', type: 'number', placeholder: '0', value: '0', step: '0.1' },
    ], function (result) {
      if (!result.nodeId) return;
      var z = parseFloat(result.nodeZ) || 0;
      nodePlacer.addNode({ x: clickX, y: clickY }, result.nodeId, selectedNodeType, { z: z });
    });
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

    var placedNodes = nodes.filter(function (n) { return !(n.auto_discovered && n.x === 0 && n.y === 0); });
    var unplacedNodes = nodes.filter(function (n) { return n.auto_discovered && n.x === 0 && n.y === 0; });

    var colors = nodePlacer.getTypeColors();

    placedNodes.forEach(function (node) {
      var li = document.createElement('li');
      var color = colors[node.type] || '#6b7280';

      li.innerHTML =
        '<div class="node-list-item">' +
          '<span class="node-color-dot" style="background:' + color + '"></span>' +
          '<span class="node-list-name">' + node.node_id + '</span>' +
          '<span class="node-list-type">' + node.type + ' z=' + (node.z || 0).toFixed(1) + 'm</span>' +
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

    if (unplacedNodes.length) {
      var divider = document.createElement('li');
      divider.className = 'node-list-divider';
      divider.innerHTML = '<span class="node-list-divider-text">Discovered — click map to place</span>';
      list.appendChild(divider);

      unplacedNodes.forEach(function (node) {
        var li = document.createElement('li');
        li.className = 'node-list-unplaced';
        li.innerHTML =
          '<div class="node-list-item">' +
            '<span class="node-color-dot unplaced" style="background:#6b7280"></span>' +
            '<span class="node-list-name">' + node.node_id + '</span>' +
            '<span class="node-new-badge">New</span>' +
          '</div>';

        var placeBtn = document.createElement('button');
        placeBtn.className = 'btn-secondary btn-sm node-place-btn';
        placeBtn.textContent = 'Place';
        placeBtn.addEventListener('click', function () {
          startPlacingDiscoveredNode(node.node_id);
        });
        li.appendChild(placeBtn);
        list.appendChild(li);
      });
    }
  }

  var placingNodeId = null;

  function startPlacingDiscoveredNode(nodeId) {
    placingNodeId = nodeId;
    showToast('Click the map to place ' + nodeId);
    document.getElementById('map').style.cursor = 'crosshair';
  }

  // ---- Floor Plan Dimensions ----
  document.getElementById('apply-dimensions').addEventListener('click', function () {
    var w = parseFloat(document.getElementById('area-width').value) || 30;
    var h = parseFloat(document.getElementById('area-height').value) || 20;
    map.setDimensions(w, h);
    map.fitBounds([[0, 0], [h, w]], { padding: [20, 20] });
    if (gridVisible) {
      map.showGrid(5);
    }
    showToast('Dimensions set: ' + w + 'm \u00D7 ' + h + 'm');
  });

  document.getElementById('toggle-grid').addEventListener('click', function () {
    gridVisible = !gridVisible;
    if (gridVisible) {
      map.showGrid(5);
      this.textContent = 'Hide Grid';
    } else {
      map.hideGrid();
      this.textContent = 'Show Grid';
    }
  });

  // ---- Save Config ----
  document.getElementById('save-config').addEventListener('click', function () {
    var config = {
      nodes: nodePlacer.getNodes(),
      dimensions: {
        width: parseFloat(document.getElementById('area-width').value) || 30,
        height: parseFloat(document.getElementById('area-height').value) || 20,
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
        if (data.dimensions) {
          var w = data.dimensions.width || 30;
          var h = data.dimensions.height || 20;
          document.getElementById('area-width').value = w;
          document.getElementById('area-height').value = h;
          map.setDimensions(w, h);
          map.fitBounds([[0, 0], [h, w]], { padding: [20, 20] });
        }
        if (data.nodes) {
          nodePlacer.loadNodes(data.nodes);
        }
      })
      .catch(function () {
        console.warn('Could not load config');
        map.fitBounds([[0, 0], [20, 30]], { padding: [20, 20] });
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

      var startTime = p.start_time ? new Date(p.start_time * 1000).toLocaleString() : '--';
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
    var waypoints = nodes.map(function (n) { return { x: n.x, y: n.y, z: n.z, node_id: n.node_id }; });
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
      var badgeHtml = node.auto_discovered ? '<span class="node-new-badge">New</span>' : '';

      var card = document.createElement('div');
      card.className = 'firmware-node-card' + (node.auto_discovered ? ' firmware-node-discovered' : '');
      card.innerHTML =
        '<div class="firmware-node-header">' +
          '<span class="firmware-node-name">' + node.node_id + badgeHtml + '</span>' +
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
    showConfirm('Restart node ' + nodeId + '?', function () {
      fetch(apiUrl('/api/nodes/' + nodeId + '/restart'), { method: 'POST' })
        .then(function () {
          showToast(nodeId + ' restarting...');
          setTimeout(loadFirmwareNodes, 5000);
        })
        .catch(function (err) {
          showToast('Restart failed: ' + err.message, 'error');
        });
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
    document.getElementById('overlay-controls-section').style.display = '';
    document.getElementById('overlay-preview').style.display = '';

    var d = map._dimensions;
    document.getElementById('overlay-width-m').value = d.width;
    document.getElementById('overlay-height-m').value = d.height;
  }

  function hideOverlayControls() {
    overlayHasImage = false;
    overlayUploadZone.classList.remove('has-image');
    document.getElementById('overlay-upload-label').textContent = 'Drop image here or click to select';
    document.getElementById('overlay-controls-section').style.display = 'none';
    document.getElementById('overlay-preview').style.display = 'none';
  }

  function updateOverlayPreview() {
    var img = document.getElementById('overlay-preview-img');
    img.src = apiUrl('/api/overlay/image') + '?t=' + Date.now();
    document.getElementById('overlay-preview').style.display = '';
  }

  document.getElementById('overlay-apply').addEventListener('click', function () {
    var w = parseFloat(document.getElementById('overlay-width-m').value);
    var h = parseFloat(document.getElementById('overlay-height-m').value);
    if (!w || !h || isNaN(w) || isNaN(h)) {
      showToast('Enter width and height in meters', 'error');
      return;
    }
    var opacity = parseInt(document.getElementById('overlay-opacity').value) / 100;
    var rotation = parseFloat(document.getElementById('overlay-rotation').value) || 0;
    var imageUrl = apiUrl('/api/overlay/image') + '?t=' + Date.now();

    map.setDimensions(w, h);
    map.setOverlayImage(imageUrl, { width: w, height: h }, { opacity: opacity, rotation: rotation });
    map.fitToOverlay();

    var settings = {
      overlay_width_m: w,
      overlay_height_m: h,
      overlay_opacity: parseInt(document.getElementById('overlay-opacity').value),
      overlay_rotation: rotation,
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
    map.fitToOverlay();
  });

  document.getElementById('overlay-remove').addEventListener('click', function () {
    showConfirm('Remove the overlay image?', function () {
      fetch(apiUrl('/api/overlay'), { method: 'DELETE' })
        .then(function () {
          map.removeOverlayImage();
          hideOverlayControls();
          showToast('Overlay removed');
        });
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

  function loadOverlayFromSettings(settings) {
    if (!settings.overlay_width_m || !settings.overlay_height_m) return;

    var img = new Image();
    img.onload = function () {
      showOverlayControls();
      updateOverlayPreview();

      var w = settings.overlay_width_m;
      var h = settings.overlay_height_m;
      document.getElementById('overlay-width-m').value = w;
      document.getElementById('overlay-height-m').value = h;

      var opacity = settings.overlay_opacity != null ? settings.overlay_opacity : 100;
      document.getElementById('overlay-opacity').value = opacity;
      document.getElementById('overlay-opacity-val').textContent = opacity;

      var rotation = settings.overlay_rotation || 0;
      document.getElementById('overlay-rotation').value = rotation;
      document.getElementById('overlay-rotation-val').textContent = rotation.toFixed(1);

      map.setDimensions(w, h);
      map.setOverlayImage(
        apiUrl('/api/overlay/image'),
        { width: w, height: h },
        { opacity: opacity / 100, rotation: rotation }
      );
    };
    img.src = apiUrl('/api/overlay/image') + '?t=' + Date.now();
  }

  // ---- Status Polling ----
  var lastKnownNodeCount = -1;

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

          if (lastKnownNodeCount >= 0 && data.node_count !== lastKnownNodeCount) {
            loadConfig();
            if (currentTab === 'firmware') {
              loadFirmwareNodes();
            }
          }
          lastKnownNodeCount = data.node_count;
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
