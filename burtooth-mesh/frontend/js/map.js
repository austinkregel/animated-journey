function initMap(containerId) {
  var map = L.map(containerId, {
    center: [0, 0],
    zoom: 3,
    maxZoom: 24,
  });

  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      attribution:
        "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
      maxNativeZoom: 20,
      maxZoom: 24,
    }
  ).addTo(map);

  map._overlayLayer = null;
  map._overlayBounds = null;
  map._overlayRotation = 0;

  map.setOverlayImage = function (imageUrl, bounds, options) {
    if (map._overlayLayer) {
      map.removeLayer(map._overlayLayer);
      map._overlayLayer = null;
    }
    if (!imageUrl || !bounds) return null;

    var leafletBounds = L.latLngBounds(
      [bounds.south, bounds.west],
      [bounds.north, bounds.east]
    );
    map._overlayBounds = bounds;
    map._overlayRotation = (options && options.rotation != null) ? options.rotation : 0;

    var opts = {
      opacity: (options && options.opacity != null) ? options.opacity : 0.85,
      interactive: false,
      zIndex: 500,
    };

    map._overlayLayer = L.imageOverlay(imageUrl, leafletBounds, opts).addTo(map);

    if (map._overlayRotation !== 0) {
      _applyOverlayRotation(map);
    }

    return map._overlayLayer;
  };

  map.setOverlayRotation = function (degrees) {
    map._overlayRotation = degrees;
    _applyOverlayRotation(map);
  };

  map.nudgeOverlay = function (dlat, dlng) {
    if (!map._overlayBounds || !map._overlayLayer) return;
    var b = map._overlayBounds;
    b.north += dlat;
    b.south += dlat;
    b.east += dlng;
    b.west += dlng;
    _refreshOverlayBounds(map);
  };

  map.scaleOverlay = function (factor) {
    if (!map._overlayBounds || !map._overlayLayer) return;
    var b = map._overlayBounds;
    var centerLat = (b.north + b.south) / 2;
    var centerLng = (b.east + b.west) / 2;
    var halfLat = (b.north - b.south) / 2;
    var halfLng = (b.east - b.west) / 2;
    b.north = centerLat + halfLat * factor;
    b.south = centerLat - halfLat * factor;
    b.east = centerLng + halfLng * factor;
    b.west = centerLng - halfLng * factor;
    _refreshOverlayBounds(map);
  };

  function _refreshOverlayBounds(m) {
    var b = m._overlayBounds;
    var leafletBounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);
    m._overlayLayer.setBounds(leafletBounds);
    if (m._overlayRotation !== 0) {
      _applyOverlayRotation(m);
    }
  }

  map.removeOverlayImage = function () {
    if (map._overlayLayer) {
      map.removeLayer(map._overlayLayer);
      map._overlayLayer = null;
      map._overlayBounds = null;
      map._overlayRotation = 0;
    }
  };

  map.setOverlayOpacity = function (opacity) {
    if (map._overlayLayer) {
      map._overlayLayer.setOpacity(opacity);
    }
  };

  map.fitOverlay = function () {
    if (map._overlayLayer) {
      map.fitBounds(map._overlayLayer.getBounds(), { padding: [20, 20] });
    }
  };

  return map;
}

function _applyOverlayRotation(map) {
  if (!map._overlayLayer) return;
  var deg = map._overlayRotation || 0;

  var origReset = map._overlayLayer.__origReset || map._overlayLayer._reset;
  map._overlayLayer.__origReset = origReset;

  map._overlayLayer._reset = function () {
    origReset.call(this);
    if (this._image) {
      this._image.style.transformOrigin = 'center center';
      var current = this._image.style.transform || '';
      this._image.style.transform = current + ' rotate(' + deg + 'deg)';
    }
  };

  map._overlayLayer._reset();
}
