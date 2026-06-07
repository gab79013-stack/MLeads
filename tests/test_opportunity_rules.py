from utils.bot_users import _lead_service_keys
from utils.gc_detector import enrich_lead_with_gc_detection
from utils.opportunity_rules import extract_contractor_name, infer_trade_from_license, infer_trade_from_text


def test_florida_ccc_license_marks_roofing_self_pull_and_blocks_roofing_route():
    lead = {
        "id": "miami-ccc-roof",
        "description": "REROOF asphalt shingle",
        "contractor": "BIGFOOT CONSTRUCTION INC",
        "contractor_number": "CCC1333168",
        "_trade": "ROOFING",
        "primary_service_type": "roofing",
    }

    enrich_lead_with_gc_detection(lead)

    assert lead["_is_gc_self_pull"] is True
    assert lead["_original_trade"] == "ROOFING"
    assert lead["_trade"] == "DRYWALL"
    services = _lead_service_keys(lead, "permits")
    assert "roofing" not in services
    assert "drywall" in services


def test_roofing_keyword_without_self_pull_still_routes_to_roofing():
    lead = {
        "id": "owner-roof",
        "description": "REROOF asphalt shingle",
        "owner": "JOHN HOMEOWNER",
        "_trade": "ROOFING",
        "primary_service_type": "roofing",
    }

    services = _lead_service_keys(lead, "permits")

    assert "roofing" in services


def test_license_prefix_inference_handles_cslb_and_florida_codes():
    assert infer_trade_from_license({"lic_number": "C-39 123456"}) == "ROOFING"
    assert infer_trade_from_license({"contractor_license": "CAC1812345"}) == "HVAC"
    assert infer_trade_from_license({"contractor_number": "EC13000000"}) == "ELECTRICAL"


def test_raw_miami_dade_fields_are_used_for_license_and_contractor_name():
    lead = {
        "id": "raw-miami-roof",
        "description": "REROOF asphalt shingle",
        "_trade": "ROOFING",
        "primary_service_type": "roofing",
        "raw": {
            "ContractorName": "BIGFOOT CONSTRUCTION INC",
            "ContractorNumber": "CCC1333168",
        },
    }

    assert extract_contractor_name(lead) == "BIGFOOT CONSTRUCTION INC"
    assert infer_trade_from_license(lead) == "ROOFING"

    enrich_lead_with_gc_detection(lead)
    services = _lead_service_keys(lead, "permits")

    assert lead["_is_gc_self_pull"] is True
    assert "roofing" not in services
    assert {"drywall", "paint", "insulation"} & services


def test_general_contractor_roof_scope_is_not_blocked_for_roofing():
    lead = {
        "id": "gc-roof-open",
        "description": "New commercial build with roof trusses and roof membrane scope",
        "contractor": "SUNRISE GENERAL CONTRACTORS LLC",
        "contractor_number": "CGC1520000",
        "_trade": "ROOFING",
        "primary_service_type": "roofing",
    }

    enrich_lead_with_gc_detection(lead)
    services = _lead_service_keys(lead, "permits")

    assert lead["_is_gc_self_pull"] is False
    assert "roofing" in services


def test_no_ai_trade_still_blocks_roofing_when_license_matches_roof_scope():
    lead = {
        "id": "miami-no-ai-ccc-roof",
        "description": "REROOF asphalt shingle",
        "contractor": "BIGFOOT CONSTRUCTION INC",
        "contractor_number": "CCC1333168",
    }

    assert infer_trade_from_text(lead) == "ROOFING"
    enrich_lead_with_gc_detection(lead)
    services = _lead_service_keys(lead, "permits")

    assert lead["_is_gc_self_pull"] is True
    assert lead["_original_trade"] == "ROOFING"
    assert "roofing" not in services
    assert "drywall" in services


def test_no_ai_trade_owner_roof_scope_still_routes_to_roofing():
    lead = {
        "id": "owner-roof-no-ai",
        "description": "REROOF asphalt shingle",
        "owner": "JOHN HOMEOWNER",
    }

    services = _lead_service_keys(lead, "permits")

    assert "roofing" in services
