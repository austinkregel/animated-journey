function initMap(containerId) {
  var map = L.map(containerId, {
    crs: L.CRS.Simple,
    center: [0, 0],
    zoom: 1,
    minZoom: -3,
    maxZoom: 8,
  });

  map._overlayLayer = null;
  map._overlayBounds = null;
  map._overlayRotation = 0;
  map._gridLayer = null;
  map._dimensions = { width: 30, height: 20 };

  map.setDimensions = function (widthM, heightM) {
    map._dimensions = { width: widthM, height: heightM };
  };

  map.setOverlayImage = function (imageUrl, dimensions, options) {
    if (map._overlayLayer) {
      map.removeLayer(map._overlayLayer);
      map._overlayLayer = null;
    }
    if (!imageUrl) return null;

    var w = (dimensions && dimensions.width) || map._dimensions.width;
    var h = (dimensions && dimensions.height) || map._dimensions.height;
    map._dimensions = { width: w, height: h };

    var bounds = [[0, 0], [h, w]];
    map._overlayBounds = { width: w, height: h };
    map._overlayRotation = (options && options.rotation != null) ? options.rotation : 0;

    var opts = {
      opacity: (options && options.opacity != null) ? options.opacity : 1.0,
      interactive: false,
    };

    map._overlayLayer = L.imageOverlay(imageUrl, bounds, opts).addTo(map);

    if (map._overlayRotation !== 0) {
      _applyOverlayRotation(map);
    }

    return map._overlayLayer;
  };

  map.fitToOverlay = function () {
    if (map._overlayLayer) {
      map.fitBounds(map._overlayLayer.getBounds(), { padding: [20, 20] });
    } else {
      var d = map._dimensions;
      map.fitBounds([[0, 0], [d.height, d.width]], { padding: [20, 20] });
    }
  };

  map.setOverlayRotation = function (degrees) {
    map._overlayRotation = degrees;
    _applyOverlayRotation(map);
  };

  map.setOverlayOpacity = function (opacity) {
    if (map._overlayLayer) {
      map._overlayLayer.setOpacity(opacity);
    }
  };

  map.removeOverlayImage = function () {
    if (map._overlayLayer) {
      map.removeLayer(map._overlayLayer);
      map._overlayLayer = null;
      map._overlayBounds = null;
      map._overlayRotation = 0;
    }
  };

  map.showGrid = function (spacingM) {
    spacingM = spacingM || 5;
    if (map._gridLayer) {
      map.removeLayer(map._gridLayer);
    }
    map._gridLayer = L.layerGroup();

    var d = map._dimensions;
    var gridStyle = { color: '#ffffff', weight: 0.5, opacity: 0.15 };

    for (var x = 0; x <= d.width; x += spacingM) {
      L.polyline([[0, x], [d.height, x]], gridStyle).addTo(map._gridLayer);
    }
    for (var y = 0; y <= d.height; y += spacingM) {
      L.polyline([[y, 0], [y, d.width]], gridStyle).addTo(map._gridLayer);
    }

    map._gridLayer.addTo(map);
  };

  map.hideGrid = function () {
    if (map._gridLayer) {
      map.removeLayer(map._gridLayer);
      map._gridLayer = null;
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
