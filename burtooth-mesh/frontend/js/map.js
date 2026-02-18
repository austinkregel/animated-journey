function initMap(containerId) {
  var map = L.map(containerId, {
    center: [0, 0],
    zoom: 3,
  });

  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      attribution:
        "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
      maxZoom: 22,
    }
  ).addTo(map);

  map._overlayLayer = null;
  map._overlayBounds = null;

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

    var opts = {
      opacity: (options && options.opacity != null) ? options.opacity : 0.85,
      interactive: false,
      zIndex: 500,
    };

    map._overlayLayer = L.imageOverlay(imageUrl, leafletBounds, opts).addTo(map);
    return map._overlayLayer;
  };

  map.removeOverlayImage = function () {
    if (map._overlayLayer) {
      map.removeLayer(map._overlayLayer);
      map._overlayLayer = null;
      map._overlayBounds = null;
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
