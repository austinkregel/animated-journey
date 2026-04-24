class NodePlacer {
  constructor(map, options = {}) {
    this.map = map;
    this.markers = {};
    this.nodes = [];
    this.onNodeChange = options.onNodeChange || (() => {});
    this.contextMenu = null;

    this.typeColors = {
      perimeter: '#22c55e',
      attic: '#3b82f6',
      strategic: '#f97316',
      lora: '#a855f7',
      scanner: '#6b7280',
    };

    this._initContextMenu();
  }

  _initContextMenu() {
    this.contextMenu = document.createElement('div');
    this.contextMenu.className = 'node-context-menu';
    this.contextMenu.style.display = 'none';
    document.body.appendChild(this.contextMenu);

    document.addEventListener('click', () => {
      this.contextMenu.style.display = 'none';
    });
  }

  _createIcon(type, status = 'online') {
    var color = this.typeColors[type] || this.typeColors.scanner;
    var opacity = status === 'offline' ? 0.4 : 1;
    var ring = status === 'online'
      ? `box-shadow: 0 0 0 3px ${color}33, 0 0 8px ${color}66;`
      : `box-shadow: 0 0 0 2px #555;`;

    return L.divIcon({
      className: 'node-marker-icon',
      html: `<div style="
        width: 18px; height: 18px; border-radius: 50%;
        background: ${color}; border: 2px solid #fff;
        opacity: ${opacity}; ${ring}
        display: flex; align-items: center; justify-content: center;
      "><div style="
        width: 6px; height: 6px; border-radius: 50%;
        background: rgba(255,255,255,0.7);
      "></div></div>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
      popupAnchor: [0, -14],
    });
  }

  _buildPopupContent(node) {
    var color = this.typeColors[node.type] || this.typeColors.scanner;
    var statusDot = node.status === 'online'
      ? '<span class="status-dot online"></span> Online'
      : '<span class="status-dot offline"></span> Offline';
    var lastSeen = node.last_seen
      ? new Date(node.last_seen).toLocaleString()
      : 'Never';

    return `
      <div class="node-popup">
        <div class="node-popup-header">
          <span class="node-popup-type" style="background:${color}">${node.type}</span>
          <strong>${node.node_id}</strong>
        </div>
        <div class="node-popup-body">
          <div class="node-popup-row">${statusDot}</div>
          <div class="node-popup-row">
            <span class="label">X:</span> ${node.x.toFixed(2)} m
          </div>
          <div class="node-popup-row">
            <span class="label">Y:</span> ${node.y.toFixed(2)} m
          </div>
          <div class="node-popup-row">
            <span class="label">Z (height):</span> ${node.z.toFixed(2)} m
          </div>
          <div class="node-popup-row">
            <span class="label">Last seen:</span> ${lastSeen}
          </div>
        </div>
      </div>
    `;
  }

  addNode(xy, nodeId, type = 'scanner', options = {}) {
    if (this.markers[nodeId]) {
      this.removeNode(nodeId);
    }

    var node = {
      node_id: nodeId,
      type: type,
      x: xy.x != null ? xy.x : (xy.lng != null ? xy.lng : 0),
      y: xy.y != null ? xy.y : (xy.lat != null ? xy.lat : 0),
      z: options.z != null ? options.z : 0,
      status: options.status || 'online',
      last_seen: options.last_seen || null,
      auto_discovered: options.auto_discovered || false,
    };

    // CRS.Simple: L.latLng(y, x)
    var marker = L.marker([node.y, node.x], {
      draggable: true,
      icon: this._createIcon(node.type, node.status),
    }).addTo(this.map);

    marker.bindPopup(this._buildPopupContent(node), {
      className: 'dark-popup',
      maxWidth: 250,
    });

    marker.on('dragend', (e) => {
      var pos = e.target.getLatLng();
      node.x = pos.lng;
      node.y = pos.lat;
      marker.setPopupContent(this._buildPopupContent(node));
      this.onNodeChange(this.getNodes());
    });

    marker.on('contextmenu', (e) => {
      L.DomEvent.preventDefault(e);
      this._showContextMenu(e.originalEvent, node);
    });

    this.markers[nodeId] = marker;
    this.nodes.push(node);
    this.onNodeChange(this.getNodes());
    return node;
  }

  _showContextMenu(event, node) {
    var menu = this.contextMenu;
    menu.innerHTML = `
      <div class="ctx-item ctx-edit" data-action="edit">Edit Node</div>
      <div class="ctx-item ctx-delete" data-action="delete">Delete Node</div>
    `;
    menu.style.display = 'block';
    menu.style.left = event.pageX + 'px';
    menu.style.top = event.pageY + 'px';

    menu.querySelector('.ctx-edit').addEventListener('click', (e) => {
      e.stopPropagation();
      menu.style.display = 'none';
      this._editNode(node);
    });

    menu.querySelector('.ctx-delete').addEventListener('click', (e) => {
      e.stopPropagation();
      menu.style.display = 'none';
      this.removeNode(node.node_id);
    });
  }

  _editNode(node) {
    if (typeof window._showNodeEditModal !== 'function') return;
    window._showNodeEditModal(node, this);
  }

  applyNodeEdit(node, newId, newType, newZ) {
    var oldId = node.node_id;
    var marker = this.markers[oldId];
    if (!marker) return;

    var types = Object.keys(this.typeColors);

    if (newId && newId !== oldId) {
      delete this.markers[oldId];
      this.markers[newId] = marker;
      node.node_id = newId;
    }

    if (newType && types.includes(newType)) {
      node.type = newType;
    }

    if (newZ != null && !isNaN(parseFloat(newZ))) {
      node.z = parseFloat(newZ);
    }

    marker.setIcon(this._createIcon(node.type, node.status));
    marker.setPopupContent(this._buildPopupContent(node));
    this.onNodeChange(this.getNodes());
  }

  removeNode(nodeId) {
    if (this.markers[nodeId]) {
      this.map.removeLayer(this.markers[nodeId]);
      delete this.markers[nodeId];
    }
    this.nodes = this.nodes.filter((n) => n.node_id !== nodeId);
    this.onNodeChange(this.getNodes());
  }

  getNodes() {
    return this.nodes.map((n) => ({
      node_id: n.node_id,
      type: n.type,
      x: n.x,
      y: n.y,
      z: n.z,
      status: n.status,
      last_seen: n.last_seen,
      auto_discovered: n.auto_discovered || false,
    }));
  }

  loadNodes(nodesArray) {
    Object.keys(this.markers).forEach((id) => {
      this.map.removeLayer(this.markers[id]);
    });
    this.markers = {};
    this.nodes = [];

    (nodesArray || []).forEach((n) => {
      var isUnplaced = n.auto_discovered && n.x === 0 && n.y === 0;
      if (isUnplaced) {
        var node = {
          node_id: n.node_id || n.id,
          type: n.type || 'scanner',
          x: 0,
          y: 0,
          z: n.z || 0,
          status: n.status || 'online',
          last_seen: n.last_seen || null,
          auto_discovered: true,
        };
        this.nodes.push(node);
      } else {
        this.addNode(
          { x: n.x || 0, y: n.y || 0 },
          n.node_id || n.id,
          n.type || 'scanner',
          { z: n.z || 0, status: n.status, last_seen: n.last_seen, auto_discovered: n.auto_discovered }
        );
      }
    });

    this.onNodeChange(this.getNodes());
  }

  updateNodeStatus(nodeId, status, lastSeen) {
    var node = this.nodes.find((n) => n.node_id === nodeId);
    if (!node) return;
    node.status = status;
    node.last_seen = lastSeen || new Date().toISOString();
    var marker = this.markers[nodeId];
    if (marker) {
      marker.setIcon(this._createIcon(node.type, node.status));
      marker.setPopupContent(this._buildPopupContent(node));
    }
  }

  getUnplacedNodes() {
    return this.nodes.filter((n) => n.auto_discovered && n.x === 0 && n.y === 0);
  }

  placeDiscoveredNode(nodeId, xy, type) {
    var node = this.nodes.find((n) => n.node_id === nodeId);
    if (!node) return;

    this.nodes = this.nodes.filter((n) => n.node_id !== nodeId);
    node.auto_discovered = false;
    this.addNode(xy, nodeId, type || node.type, { z: node.z, status: node.status, last_seen: node.last_seen });
  }

  getTypeColors() {
    return { ...this.typeColors };
  }
}
