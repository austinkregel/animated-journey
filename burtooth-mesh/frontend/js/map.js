function initMap(containerId) {
  var map = L.map(containerId, {
    center: [42.98880, -84.18284],
    zoom: 19,
  });

  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      attribution:
        "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
      maxZoom: 22,
    }
  ).addTo(map);

  return map;
}
