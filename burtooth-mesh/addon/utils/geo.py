import math

METERS_PER_DEGREE = 111320.0


def gps_to_local(lat: float, lng: float, origin_lat: float, origin_lng: float) -> tuple[float, float]:
    x = (lng - origin_lng) * math.cos(math.radians(origin_lat)) * METERS_PER_DEGREE
    y = (lat - origin_lat) * METERS_PER_DEGREE
    return x, y


def local_to_gps(x: float, y: float, origin_lat: float, origin_lng: float) -> tuple[float, float]:
    lat = origin_lat + y / METERS_PER_DEGREE
    lng = origin_lng + x / (math.cos(math.radians(origin_lat)) * METERS_PER_DEGREE)
    return lat, lng


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return r * c
