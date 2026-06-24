import json
import sqlite3

from web.helpers.service_filter import build_service_category_filter


def _matches_category(row, category):
    service_sql, params = build_service_category_filter([category])
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE leads (primary_service_type TEXT, lead_data TEXT)")
    conn.execute(
        "INSERT INTO leads (primary_service_type, lead_data) VALUES (?, ?)",
        (row["primary_service_type"], json.dumps(row["lead_data"])),
    )
    result = conn.execute(f"SELECT COUNT(*) FROM leads WHERE {service_sql}", params).fetchone()[0]
    conn.close()
    return result == 1


def test_stale_primary_roofing_does_not_leak_taken_roof_permit_to_roofing_feed():
    row = {
        "primary_service_type": "roofing",  # stale column from pre-refresh insert
        "lead_data": {
            "_is_gc_self_pull": True,
            "_original_trade": "ROOFING",
            "_trade": "DRYWALL",
            "_sub_trades": ["DRYWALL", "PAINTING", "INSULATION"],
        },
    }

    assert _matches_category(row, "roofing") is False
    assert _matches_category(row, "drywall") is True
    assert _matches_category(row, "paint") is True


def test_open_gc_roof_scope_still_matches_roofing_feed():
    row = {
        "primary_service_type": "roofing",
        "lead_data": {
            "_is_gc_self_pull": False,
            "_trade": "ROOFING",
            "_sub_trades": [],
        },
    }

    assert _matches_category(row, "roofing") is True


def test_post_sale_remodel_category_matches_new_service_type():
    row = {
        "primary_service_type": "post_sale_remodel",
        "lead_data": {
            "lead_type": "post_sale_cash_buyer",
            "buyer_name": "Sunrise Homes LLC",
            "_scoring": {"score": 91},
        },
    }

    assert _matches_category(row, "post_sale_remodel") is True
