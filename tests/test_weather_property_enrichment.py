from utils.weather_property_enrichment import (
    build_property_candidate,
    enrich_weather_zones,
    score_property_candidate,
)


def _zone():
    return {
        "id": "weather_spc_test_dfw_wind_2156_dallas",
        "city": "Dallas",
        "county": "Dallas",
        "state": "TX",
        "lat": 32.75,
        "lon": -96.80,
        "date": "2026-06-10",
        "source_url": "https://www.spc.noaa.gov/climo/reports/",
        "event_type": "wind",
        "event_label": "💨 Viento severo",
        "event_metric": "UNK",
        "event_time": "2156",
        "severity": "HIGH",
        "metro": "Dallas–Fort Worth",
        "impact_radius_miles": 7,
        "recommended_services": ["roofing", "siding", "windows"],
    }


def _element(osm_id=123, building="house", lat=32.751, lon=-96.801):
    return {
        "type": "way",
        "id": osm_id,
        "center": {"lat": lat, "lon": lon},
        "tags": {
            "building": building,
            "addr:housenumber": "100",
            "addr:street": "Main St",
            "addr:city": "Dallas",
            "addr:state": "TX",
            "addr:postcode": "75201",
        },
    }


def test_property_candidate_keeps_weather_disclaimer_and_address():
    candidate = build_property_candidate(_zone(), _element(), rank=0)

    assert candidate is not None
    assert candidate["lead_type"] == "weather_property_candidate"
    assert candidate["address"] == "100 Main St, Dallas, TX 75201"
    assert candidate["parent_weather_zone_id"] == "weather_spc_test_dfw_wind_2156_dallas"
    assert candidate["_disclaimer"] == "Property is inside a probable weather impact zone; damage is not confirmed."
    assert "No afirmar daño confirmado" in candidate["description"]
    assert candidate["_scoring"]["score"] >= 70


def test_multifamily_scores_above_basic_house():
    zone = _zone()
    house = score_property_candidate(zone, _element(building="house"))["score"]
    apartments = score_property_candidate(zone, _element(osm_id=456, building="apartments"))["score"]

    assert apartments > house


def test_enrich_weather_zones_uses_overpass_results(monkeypatch):
    def fake_fetch(lat, lon, radius_meters, limit):
        assert radius_meters > 0
        second = _element(osm_id=2, building="apartments", lat=32.752, lon=-96.802)
        second["tags"]["addr:housenumber"] = "200"
        return [_element(osm_id=1), second]

    monkeypatch.setattr("utils.weather_property_enrichment._fetch_overpass_buildings", fake_fetch)

    leads = enrich_weather_zones([_zone()], per_zone_limit=2, total_limit=2)

    assert len(leads) == 2
    assert all(lead["lead_type"] == "weather_property_candidate" for lead in leads)
    assert leads[0]["_scoring"]["score"] >= leads[1]["_scoring"]["score"]
