#!/usr/bin/env python3
"""
Audit real Swipe inventory for Elite sellability.

This script intentionally uses public, non-sensitive endpoints so it can be
run before a sales call without database access.
"""

from __future__ import annotations

import argparse
import json
import statistics
import urllib.parse
import urllib.request
import uuid


ELITE_SCORE_MIN = 70


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.load(resp)


def feed_url(base_url: str, city: str, service: str, limit: int) -> str:
    params = {
        "anon_id": f"a_audit_{uuid.uuid4().hex[:16]}",
        "limit": str(limit),
    }
    if city:
        params["city"] = city
    if service:
        params["service_cats"] = service
    return f"{base_url.rstrip('/')}/api/swipe/feed?{urllib.parse.urlencode(params)}"


def elite_score(lead: dict) -> int:
    points = 0
    insight = lead.get("gc_insight") or {}
    if lead.get("gc_confidence") == "verified" or insight.get("confidence") == "verified":
        points += 30
    if lead.get("source_url") or insight.get("source_url"):
        points += 15
    if lead.get("phone"):
        points += 20
    if int(lead.get("score") or 0) >= 90:
        points += 15
    if float(lead.get("value") or 0) > 0:
        points += 10
    if lead.get("inspection_date"):
        points += 10
    return min(points, 100)


def audit_market(base_url: str, city: str, service: str, limit: int) -> dict:
    data = fetch_json(feed_url(base_url, city, service, limit))
    leads = data.get("leads") or []
    q_scores = [elite_score(lead) for lead in leads]
    elite_leads = [lead for lead, q in zip(leads, q_scores) if q >= ELITE_SCORE_MIN]
    available = data.get("available_service_counts") or {}
    return {
        "city": city or "All",
        "service": service or "all",
        "available_service_counts": available,
        "returned": len(leads),
        "elite_in_sample": len(elite_leads),
        "avg_lead_score": round(statistics.mean([int(l.get("score") or 0) for l in leads]), 1) if leads else 0,
        "avg_elite_quality_score": round(statistics.mean(q_scores), 1) if q_scores else 0,
        "source_coverage_pct": round(sum(1 for l in leads if l.get("source_url")) * 100 / len(leads), 1) if leads else 0,
        "phone_coverage_pct": round(sum(1 for l in leads if l.get("phone")) * 100 / len(leads), 1) if leads else 0,
        "value_coverage_pct": round(sum(1 for l in leads if float(l.get("value") or 0) > 0) * 100 / len(leads), 1) if leads else 0,
        "sellability": sellability(available, service, len(elite_leads), len(leads), q_scores),
        "samples": [
            {
                "score": lead.get("score"),
                "quality_score": elite_score(lead),
                "city": lead.get("city"),
                "service_type": lead.get("service_type"),
                "address": lead.get("address"),
                "value": lead.get("value"),
                "has_phone": bool(lead.get("phone")),
                "source_label": lead.get("source_label"),
            }
            for lead in leads[:5]
        ],
    }


def sellability(available: dict, service: str, elite_count: int, returned: int, q_scores: list[int]) -> str:
    inventory_count = int(available.get(service or "permits") or max(available.values() or [0]))
    avg_quality = statistics.mean(q_scores) if q_scores else 0
    sample_ratio = (elite_count / returned) if returned else 0
    if inventory_count >= 50 and returned >= 5 and sample_ratio >= 0.8 and avg_quality >= 80:
        return "ready_for_elite"
    if inventory_count >= 15 and returned >= 5 and sample_ratio >= 0.5:
        return "pilot_market"
    return "needs_inventory"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit real Swipe inventory for Elite sales readiness.")
    parser.add_argument("--base-url", default="http://2.25.162.58", help="Production/staging base URL")
    parser.add_argument("--market", action="append", default=[], help="Market to audit, e.g. Honolulu:permits")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    markets = args.market or ["Honolulu:permits", "Nashville:weather", "Dallas:permits", "Houston:permits"]
    health = fetch_json(f"{args.base_url.rstrip('/')}/api/health")
    print(json.dumps({"health": health}, ensure_ascii=False, indent=2))
    for item in markets:
        city, _, service = item.partition(":")
        report = audit_market(args.base_url, city.strip(), service.strip(), args.limit)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
