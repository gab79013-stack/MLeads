from web.helpers.gc_interest import build_gc_insight, is_gc_interesting_lead


def test_active_construction_with_assigned_general_contractor_is_not_gc_sellable():
    lead = {
        "description": "Commercial tenant improvement under active construction",
        "contractor": "SUNRISE GENERAL CONTRACTORS LLC",
        "contact_phone": "2145550100",
        "_scoring": {"score": 92},
    }

    assert is_gc_interesting_lead(lead, "construction") is False


def test_owner_builder_permit_is_gc_sellable_even_with_contractor_text():
    lead = {
        "description": "Owner-builder residential addition permit ready for bids",
        "contractor": "OWNER BUILDER",
        "owner": "Maria Lopez",
        "contact_phone": "2145550101",
        "_scoring": {"score": 74},
    }

    assert is_gc_interesting_lead(lead, "permits") is True


def test_storm_damage_property_contact_is_gc_sellable_without_assigned_contractor():
    lead = {
        "description": "Severe hail and wind damage reported near property; roof and exterior repair likely",
        "owner": "Oak Creek HOA",
        "contact_phone": "2145550102",
        "_scoring": {"score": 81},
    }

    assert is_gc_interesting_lead(lead, "flood") is True


def test_specialty_or_trade_contractor_already_assigned_is_not_gc_sellable():
    lead = {
        "description": "Reroof permit pulled by licensed contractor",
        "contractor": "ABC ROOFING INC",
        "contractor_number": "CCC1333168",
        "contact_phone": "3055550103",
        "_trade": "ROOFING",
        "_scoring": {"score": 88},
    }

    assert is_gc_interesting_lead(lead, "permits") is False


def test_gc_insight_explains_owner_builder_permit_with_verifiable_source():
    lead = {
        "description": "Owner-builder residential addition permit ready for bids",
        "contractor": "OWNER BUILDER",
        "owner": "Maria Lopez",
        "contact_phone": "2145550101",
        "source_url": "https://permits.example.gov/P-123",
        "_scoring": {"score": 82},
    }

    insight = build_gc_insight(lead, "permits")

    assert insight["confidence"] == "verified"
    assert "Owner-builder" in insight["badges"]
    assert "Permiso abierto" in insight["badges"]
    assert any("No hay GC confirmado" in reason for reason in insight["reasons"])
    assert insight["source_url"] == "https://permits.example.gov/P-123"


def test_gc_insight_marks_storm_damage_as_candidate_when_source_missing():
    lead = {
        "description": "Hail and wind damage near property; exterior repair likely",
        "owner": "Oak Creek HOA",
        "contact_phone": "2145550102",
        "_scoring": {"score": 71},
    }

    insight = build_gc_insight(lead, "flood")

    assert insight["confidence"] == "candidate"
    assert "Daño por tormenta" in insight["badges"]
    assert any("confirmar daños" in reason.lower() for reason in insight["reasons"])
    assert insight["source_label"] == "Fuente no verificada"
