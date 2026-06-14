from agents.weather_agent import (
    _is_actionable_report,
    _nearest_priority_metro,
    _parse_spc_reports,
    build_spc_lead,
)


def test_spc_parser_detects_wind_and_hail_sections():
    sample = """Time,F_Scale,Location,County,State,Lat,Lon,Comments
Time,Speed,Location,County,State,Lat,Lon,Comments
2156,UNK,2 S Dallas,Dallas,TX,32.75,-96.80,Tree has fallen on a home with roof damage. (FWD)
Time,Size,Location,County,State,Lat,Lon,Comments
2200,1.75,Plano,Collin,TX,33.02,-96.70,Quarter to golf ball hail reported. (FWD)
"""

    reports = _parse_spc_reports(sample, "test")

    assert [report["event_type"] for report in reports] == ["wind", "hail"]
    assert reports[0]["location"] == "2 S Dallas"
    assert reports[1]["metric"] == "1.75"


def test_report_is_matched_to_dfw_priority_metro_and_scored_for_gc():
    report = {
        "event_type": "wind",
        "time": "2156",
        "metric": "UNK",
        "location": "2 S Dallas",
        "county": "Dallas",
        "state": "TX",
        "lat": 32.75,
        "lon": -96.80,
        "comments": "Tree has fallen on a home with roof damage.",
        "report_date_label": "test",
    }

    assert _is_actionable_report(report)
    metro, distance = _nearest_priority_metro(report["lat"], report["lon"], report["state"])
    assert metro is not None
    assert distance is not None
    lead = build_spc_lead(report, metro)

    assert metro["key"] == "dfw"
    assert distance < 5
    assert lead["city"] == "Dallas"
    assert lead["metro"] == "Dallas–Fort Worth"
    assert lead["lead_type"] == "weather_impact_zone"
    assert lead["_disclaimer"].startswith("Weather impact zone")
    assert "roofing" in lead["recommended_services"]
    assert lead["_scoring"]["score"] >= 90


def test_non_target_report_is_ignored_by_metro_filter():
    metro, distance = _nearest_priority_metro(45.04, -90.34, "WI")

    assert metro is None
    assert distance is None
