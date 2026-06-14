import os
import json
from flask import Blueprint, request, jsonify, g
from functools import wraps
import logging
from datetime import datetime, timedelta
from decimal import Decimal
logger = logging.getLogger('mleads')
from web.auth import require_auth
from utils.web_db import get_db_connection
from web.helpers.geocode import _get_ip_geo, _geo_locate_ip
from web.helpers.swipe import _resolve_swipe_identity, _already_swiped_ids, _count_swipes, ANON_LEAD_LIMIT
from web.helpers.geocode import _haversine_miles, _city_coords, CITY_COORDS
from web.helpers.service_filter import build_service_category_filter, DEFAULT_TRADE_SERVICE_TO_AI
from web.helpers.gc_interest import build_gc_insight, build_gc_interest_sql_filter, build_public_real_lead_sql_filter, is_gc_interesting_lead
bp = Blueprint("swipe", __name__)

# Lazy imports to avoid circular dependency with web.app
def _get_app_const(name, default=None):
    """Get a constant from web.app without circular import."""
    try:
        import web.app as _app_mod
        return getattr(_app_mod, name, default)
    except Exception:
        return default


def _get_web_subscription(user_id) -> tuple[bool, str]:
    if not user_id:
        return False, "free"
    conn = get_db_connection()
    c = conn.cursor()
    credit_granted = False
    replacement_credits = 0
    try:
        c.execute("SELECT COALESCE(is_paid, 0), COALESCE(subscription_tier, 'free'), paid_until FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        is_paid = bool(row and row[0])
        tier = str((row[1] if row and len(row) > 1 else "free") or "free").lower()
        paid_until = row[2] if row and len(row) > 2 else None
        if is_paid and paid_until:
            try:
                is_paid = datetime.strptime(str(paid_until)[:19], "%Y-%m-%d %H:%M:%S") > datetime.utcnow()
            except Exception:
                pass
        if is_paid and tier == "free":
            tier = "premium"
        if not is_paid:
            tier = "free"
        return is_paid, tier
    finally:
        conn.close()


def _tier_lead_limit(tier: str, is_paid: bool) -> int | None:
    if not is_paid:
        return _get_app_const("FREE_USER_LEAD_LIMIT", 40)
    if tier == "pro":
        return _get_app_const("PRO_LEAD_LIMIT", 200)
    if tier == "elite":
        return _get_app_const("ELITE_LEAD_LIMIT", 80)
    return None


def _lead_age_days(lead_data: dict, fallback_date: str = "") -> int | None:
    for field in ("issued_date", "issue_date", "event_date", "created_at", "last_updated", "_first_seen"):
        raw = (lead_data.get(field) or "").strip() if isinstance(lead_data.get(field), str) else lead_data.get(field)
        if not raw:
            continue
        try:
            text = str(raw).strip().replace("Z", "").replace("T", " ")
            if len(text) >= 19:
                dt = datetime.fromisoformat(text[:19])
            else:
                dt = datetime.fromisoformat(text[:10])
            return max((datetime.utcnow() - dt).days, 0)
        except Exception:
            continue
    if fallback_date:
        return _lead_age_days({"_first_seen": fallback_date})
    return None


def _premium_quality(lead_data: dict, gc_insight: dict, service_type: str, scoring: dict, inspection_date: str = "", first_seen: str = "") -> tuple[int, list[str], bool]:
    score = int(scoring.get("score") or 0)
    service = str(service_type or "").lower()
    has_source = bool(gc_insight.get("source_url"))
    has_phone = bool((lead_data.get("contact_phone") or "").strip())
    has_value = bool(lead_data.get("value_float"))
    age_days = _lead_age_days(lead_data, first_seen)
    has_action_window = bool(inspection_date)
    has_direct_owner_intent = str(lead_data.get("_lead_channel") or "") == "homeowner_intake"
    fresh_limit_days = 21 if service in {"weather", "flood", "disaster"} else 45
    has_recent_signal = has_direct_owner_intent or has_action_window or (age_days is not None and age_days <= fresh_limit_days)
    points = 0
    checks: list[str] = []
    if gc_insight.get("confidence") == "verified":
        points += 30
        checks.append("Fuente oficial verificada")
    if has_source:
        points += 15
        checks.append("Link de fuente auditable")
    if has_phone:
        points += 20
        checks.append("Teléfono disponible")
    if score >= 90:
        points += 15
        checks.append("Score HOT 90+")
    if has_value:
        points += 10
        checks.append("Valor de proyecto detectado")
    if inspection_date:
        points += 10
        checks.append("Ventana de visita/inspección")
    if service in {"weather", "flood", "disaster"}:
        checks.append("Oportunidad sensible al tiempo")
    if has_direct_owner_intent:
        checks.append("Homeowner pidió GC directamente")
    if age_days is not None and age_days <= fresh_limit_days:
        checks.append(f"Señal fresca ({age_days} días)")
    is_elite = (
        points >= 70
        and has_source
        and has_phone
        and score >= 85
        and (has_value or has_action_window or has_direct_owner_intent)
        and has_recent_signal
    )
    if not is_elite and not has_phone:
        checks.append("No Elite: falta teléfono")
    if not is_elite and not has_recent_signal:
        checks.append("No Elite: señal vieja o sin fecha")
    return min(points, 100), checks[:5], is_elite


def _elite_certificate(
    lead_data: dict,
    gc_insight: dict,
    q_score: int,
    q_checks: list[str],
    is_elite: bool,
    inspection_date: str = "",
    first_seen: str = "",
    claimed_by_me: bool = False,
    claim_expires_at: str = "",
) -> dict:
    """Structured buyer-facing proof for why an Elite lead is worth paying for."""
    age_days = _lead_age_days(lead_data, first_seen)
    evidence = []
    if gc_insight.get("source_url"):
        evidence.append({
            "label": "Fuente auditable",
            "value": gc_insight.get("source_label") or "Fuente oficial",
            "status": "verified" if gc_insight.get("confidence") == "verified" else "present",
        })
    if (lead_data.get("contact_phone") or "").strip():
        evidence.append({"label": "Contacto directo", "value": "Teléfono disponible", "status": "verified"})
    if lead_data.get("value_float"):
        try:
            value = f"${float(lead_data.get('value_float') or 0):,.0f}"
        except Exception:
            value = "Detectado"
        evidence.append({"label": "Valor del proyecto", "value": value, "status": "verified"})
    if inspection_date:
        evidence.append({"label": "Ventana de acción", "value": str(inspection_date)[:10], "status": "timely"})
    elif age_days is not None:
        evidence.append({"label": "Frescura", "value": f"{age_days} días", "status": "fresh" if age_days <= 45 else "aging"})
    if claimed_by_me:
        evidence.append({"label": "Exclusividad", "value": f"Reservado hasta {claim_expires_at[:10]}", "status": "reserved"})
    elif is_elite:
        evidence.append({"label": "Exclusividad", "value": "Disponible para reservar", "status": "available"})

    return {
        "certified": bool(is_elite),
        "quality_score": int(q_score or 0),
        "headline": "Certificado Elite" if is_elite else "Evidencia de calidad",
        "checks": q_checks[:5],
        "evidence": evidence[:6],
    }


def _is_elite_lead_record(row_dict: dict, lead_data: dict | None = None) -> tuple[bool, int, list[str]]:
    try:
        lead_data = lead_data if lead_data is not None else json.loads(row_dict.get("lead_data") or "{}")
    except Exception:
        lead_data = {}
    service_type = (row_dict.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
    if not service_type or not is_gc_interesting_lead(lead_data, service_type):
        return False, 0, []
    gc_insight = build_gc_insight(lead_data, service_type)
    if not gc_insight.get("source_url"):
        return False, 0, []
    scoring = lead_data.get("_scoring", {}) or {}
    inspection_date = (
        lead_data.get("inspection_date")
        or lead_data.get("next_inspection_date")
        or lead_data.get("next_scheduled_inspection_date")
        or ""
    )
    q_score, q_checks, is_elite = _premium_quality(lead_data, gc_insight, service_type, scoring, str(inspection_date).strip()[:10], row_dict.get("first_seen", ""))
    return is_elite, q_score, q_checks


def _active_elite_claim(c, lead_id: str):
    c.execute("""
        SELECT user_id, expires_at
        FROM elite_lead_claims
        WHERE lead_id = ?
          AND status = 'active'
          AND datetime(expires_at) > datetime('now')
        LIMIT 1
    """, (lead_id,))
    return c.fetchone()


def _claim_elite_lead(conn, c, user_id: int, lead_id: str, claim_type: str = "like") -> dict:
    claim = _active_elite_claim(c, lead_id)
    if claim and int(claim["user_id"]) != int(user_id):
        return {"claimed": False, "blocked": True, "expires_at": claim["expires_at"]}
    expires_at = (datetime.utcnow() + timedelta(days=_get_app_const("ELITE_CLAIM_DAYS", 14))).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO elite_lead_claims (lead_id, user_id, claim_type, status, claimed_at, expires_at)
        VALUES (?, ?, ?, 'active', CURRENT_TIMESTAMP, ?)
        ON CONFLICT(lead_id) DO UPDATE SET
            user_id = excluded.user_id,
            claim_type = excluded.claim_type,
            status = 'active',
            claimed_at = CURRENT_TIMESTAMP,
            expires_at = excluded.expires_at
    """, (lead_id, int(user_id), claim_type, expires_at))
    conn.commit()
    return {"claimed": True, "blocked": False, "expires_at": expires_at}


def _elite_inventory_payload(city_filter: str = "", service_filter: str = "") -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    conditions = [build_public_real_lead_sql_filter(), build_gc_interest_sql_filter()]
    params: list = []
    if city_filter:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")
    if service_filter:
        cats = [x.strip().lower() for x in service_filter.split(",") if x.strip()]
        service_sql, service_params = build_service_category_filter(
            cats,
            _get_app_const("_TRADE_SERVICE_TO_AI", DEFAULT_TRADE_SERVICE_TO_AI),
            _get_app_const("_SERVICE_TYPE_CATS", set()),
        )
        if service_sql:
            conditions.append(service_sql)
            params.extend(service_params)
    where_sql = "WHERE " + " AND ".join(conditions)
    c.execute(f"""
        SELECT address_key, address, city, lead_data, primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY first_seen DESC
        LIMIT 2500
    """, params)
    rows = c.fetchall()
    conn.close()

    by_city: dict[str, int] = {}
    by_service: dict[str, int] = {}
    samples: list[dict] = []
    total = 0
    quality_sum = 0
    for row in rows:
        rd = dict(row)
        try:
            lead_data = json.loads(rd.get("lead_data") or "{}")
        except Exception:
            continue
        service_type = (rd.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
        if not service_type or not is_gc_interesting_lead(lead_data, service_type):
            continue
        gc_insight = build_gc_insight(lead_data, service_type)
        if not gc_insight.get("source_url"):
            continue
        scoring = lead_data.get("_scoring", {}) or {}
        q_score, q_checks, is_elite = _premium_quality(lead_data, gc_insight, service_type, scoring, "", rd.get("first_seen", ""))
        if not is_elite:
            continue
        total += 1
        quality_sum += q_score
        city = rd.get("city") or "Unknown"
        by_city[city] = by_city.get(city, 0) + 1
        by_service[service_type] = by_service.get(service_type, 0) + 1
        if len(samples) < 8:
            samples.append({
                "city": city,
                "service_type": service_type,
                "score": int(scoring.get("score") or 0),
                "quality_score": q_score,
                "checks": q_checks,
                "value": lead_data.get("value_float") or 0,
                "source_label": gc_insight.get("source_label", ""),
            })
    return {
        "total_elite_leads": total,
        "average_quality_score": round(quality_sum / total, 1) if total else 0,
        "by_city": dict(sorted(by_city.items(), key=lambda kv: (-kv[1], kv[0]))[:25]),
        "by_service": dict(sorted(by_service.items(), key=lambda kv: (-kv[1], kv[0]))),
        "top_markets": [
            {"city": city, "elite_leads": count}
            for city, count in sorted(by_city.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        ],
        "samples": samples,
        "filters": {"city": city_filter, "service": service_filter},
    }


@bp.route('/swipe/feed', methods=['GET'])
def swipe_feed():
    """
    Public feed of leads for the Tinder-style swipe UI.

    Query params:
      - limit:         how many leads to return (default 10, max 20)
      - anon_id:       stable client-generated id for anonymous visitors
      - hot_only:      '1' to return only HOT leads (score >= 90)
      - min_score:     minimum score (0-100, default 0)
      - min_value:     minimum project value in USD (default 0)
      - max_value:     maximum project value in USD (0 = no limit)
      - city:          filter by city name (partial match)
      - radius_miles:  miles radius from city (requires city param)
      - service_cats:  comma-separated list of categories
                       subcontractor: roofing, drywall, paint, electrical,
                         plumbing, hvac, flooring, concrete, framing, windows, landscaping
                       lead type: solar, permits, construction, realestate,
                         flood, energy, rodents, deconstruction, remodel

    Anonymous visitors can view up to ANON_LEAD_LIMIT leads total.
    """
    user_id, anon_id = _resolve_swipe_identity()

    try:
        limit = min(int(request.args.get("limit", 10)), 20)
    except (TypeError, ValueError):
        limit = 10

    # ── Filter params ──────────────────────────────────────────────────────────
    hot_only = request.args.get("hot_only", "0") == "1"
    try:
        min_score = int(request.args.get("min_score", 0))
    except (TypeError, ValueError):
        min_score = 0
    if hot_only:
        min_score = max(min_score, 90)

    try:
        min_value = float(request.args.get("min_value", 0))
    except (TypeError, ValueError):
        min_value = 0.0
    try:
        max_value = float(request.args.get("max_value", 0))
    except (TypeError, ValueError):
        max_value = 0.0

    city_filter = (request.args.get("city") or "").strip()
    try:
        radius_miles = float(request.args.get("radius_miles", 0))
    except (TypeError, ValueError):
        radius_miles = 0.0

    raw_cats = (request.args.get("service_cats") or "").strip()
    selected_cats = [c.strip().lower() for c in raw_cats.split(",") if c.strip()] if raw_cats else []
    elite_only = request.args.get("elite_only", "0") == "1"

    # Pre-compute origin coords for radius filtering
    origin_coords = _city_coords(city_filter) if (city_filter and radius_miles > 0) else None
    do_radius = origin_coords is not None or (city_filter and radius_miles > 0)

    already_swiped = _already_swiped_ids(user_id, anon_id)
    swipes_count = len(already_swiped)

    remaining = None
    if not user_id:
        if not anon_id:
            # Auto-generate anon_id instead of rejecting
            import uuid
            anon_id = f"a_{uuid.uuid4().hex[:18]}"
        if elite_only:
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "register",
                "required_tier": "elite",
                "anon_limit":   ANON_LEAD_LIMIT,
                "swipes_count": swipes_count,
                "remaining":    0,
            }), 200

        remaining = max(ANON_LEAD_LIMIT - swipes_count, 0)
        if remaining == 0:
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "register",
                "anon_limit":   ANON_LEAD_LIMIT,
                "swipes_count": swipes_count,
                "remaining":    0,
            }), 200
        limit = min(limit, remaining)
    else:
        # Check free-tier quota for authenticated non-paid users
        is_paid, subscription_tier = _get_web_subscription(user_id)
        if elite_only and subscription_tier != "elite":
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "upgrade",
                "required_tier": "elite",
                "swipes_count": swipes_count,
                "remaining":    0,
            }), 200
        if subscription_tier == "elite":
            elite_only = True
        tier_limit = _tier_lead_limit(subscription_tier, is_paid)
        credit_count_fn = _get_app_const("_elite_replacement_credit_count", lambda _user_id: 0)
        replacement_credits = credit_count_fn(user_id) if subscription_tier == "elite" else 0
        billable_swipes_count = max(swipes_count - replacement_credits, 0)
        if tier_limit is not None and billable_swipes_count >= tier_limit:
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "upgrade",
                "free_limit":   _get_app_const("FREE_USER_LEAD_LIMIT", 40),
                "tier_limit":   tier_limit,
                "tier":         subscription_tier,
                "swipes_count": swipes_count,
                "billable_swipes_count": billable_swipes_count,
                "replacement_credits": replacement_credits,
                "remaining":    0,
            }), 200

    conn = get_db_connection()
    c = conn.cursor()

    # ── Build WHERE clause ─────────────────────────────────────────────────────
    conditions: list[str] = []
    params: list = []

    if already_swiped:
        placeholders = ",".join("?" * len(already_swiped))
        conditions.append(f"address_key NOT IN ({placeholders})")
        params.extend(already_swiped)

    if min_score > 0:
        conditions.append(
            "CAST(COALESCE(json_extract(lead_data, '$._scoring.score'), 15) AS INTEGER) >= ?"
        )
        params.append(min_score)

    if min_value > 0:
        conditions.append(
            "CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) >= ?"
        )
        params.append(min_value)

    if max_value > 0:
        conditions.append(
            "CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) <= ?"
        )
        params.append(max_value)

    # Phone filter — always required: only leads with a phone number are shown.
    # has_phone is a pre-computed indexed column (set at insert time in dedup.py
    # and re-synced at startup in web_db.py) so this is a fast index scan.
    conditions.append("has_phone = 1")

    # Dead-lead filter — exclude GC self-pull leads (contractor pulling own permit).
    # is_dead_lead is a pre-computed indexed column set by gc_detector.py via base.py.
    conditions.append("COALESCE(is_dead_lead, 0) = 0")
    conditions.append(build_gc_interest_sql_filter())

    # ── Show all leads, AI-enriched ones sorted first ───────────────────────────
    # No hard filter — the sort key gives AI leads priority instead.
    # This ensures the feed is never empty for any trade category.

    # City filter: without radius → simple LIKE; with radius → post-process
    if city_filter and not do_radius:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")

    # ── Service category filter ────────────────────────────────────────────────
    # For subcontractor trades, match the CURRENT opportunity trade only.
    # Do not fall back to raw description/permit_type: a taken REROOF permit still
    # says "roof", but after self-pull detection it may be a drywall/paint lead.
    if selected_cats:
        trade_map = _get_app_const("_TRADE_SERVICE_TO_AI", DEFAULT_TRADE_SERVICE_TO_AI)
        service_type_cats = _get_app_const("_SERVICE_TYPE_CATS", set())
        service_sql, service_params = build_service_category_filter(selected_cats, trade_map, service_type_cats)
        if service_sql:
            conditions.append(service_sql)
            params.extend(service_params)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Fetch a large pool for diversity, then apply city round-robin
    # This prevents any single city from monopolizing the feed
    fetch_limit = max(limit * 30, 300) if not do_radius else limit * 10

    query = f"""
        SELECT address_key, address, city, agent_sources, lead_data,
               primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY first_seen DESC,
                 CAST(json_extract(lead_data, '$._scoring.score') AS INTEGER) DESC
        LIMIT ?
    """
    params.append(fetch_limit)
    c.execute(query, params)
    raw_rows = c.fetchall()

    # ── City round-robin: pick from each city in rotation ─────────────────────
    # Groups leads by city and interleaves them so no single city dominates.
    # Within each city, leads are already ordered best-first (score/25 DESC).
    if not city_filter and not do_radius and len(raw_rows) > limit:
        from collections import defaultdict
        city_buckets: dict = defaultdict(list)
        for r in raw_rows:
            city_buckets[dict(r).get("city", "")].append(r)
        # Sort cities by number of leads (more leads = more variety)
        ordered_cities = sorted(city_buckets.keys(), key=lambda c: -len(city_buckets[c]))
        diversified = []
        i = 0
        while len(diversified) < fetch_limit and any(city_buckets.values()):
            city = ordered_cities[i % len(ordered_cities)]
            if city_buckets[city]:
                diversified.append(city_buckets[city].pop(0))
            i += 1
            if i > len(ordered_cities) * 1000:
                break
        rows = diversified
    else:
        rows = list(raw_rows)

    c.execute("SELECT name, display_label, emoji FROM service_types")
    service_types_map = {
        row[0]: {"label": row[1], "emoji": row[2]} for row in c.fetchall()
    }

    # ── Batch-fetch upcoming scheduled inspections for all addresses ──────────
    all_addresses = [dict(r).get("address") for r in rows if dict(r).get("address")]
    insp_map: dict = {}
    if all_addresses:
        try:
            ph = ",".join("?" * len(all_addresses))
            c.execute(f"""
                SELECT si.address, si.inspection_date, si.inspection_type,
                       si.inspector_name, si.time_window_start, si.time_window_end,
                       si.gc_presence_probability
                FROM scheduled_inspections si
                INNER JOIN (
                    SELECT address, MIN(inspection_date) AS min_date
                    FROM scheduled_inspections
                    WHERE inspection_date >= date('now') AND address IN ({ph})
                    GROUP BY address
                ) best ON si.address = best.address AND si.inspection_date = best.min_date
            """, all_addresses)
            for r in c.fetchall():
                rd = dict(r)
                insp_map[rd["address"]] = rd
        except Exception as ie:
            logger.debug(f"Inspection batch lookup failed: {ie}")

    leads = []
    for row in rows:
        row_dict = dict(row)
        try:
            lead_data = json.loads(row_dict.get("lead_data") or "{}")
        except Exception:
            lead_data = {}

        # ── Phone guard (belt-and-suspenders after DB filter) ─────────────────
        # The WHERE clause already filters has_phone=1, but lead_data may have
        # been updated after the column was computed. Skip stale rows.
        phone_check = (lead_data.get("contact_phone") or "").strip()
        if not phone_check:
            continue

        # ── Radius filter (post-process) ───────────────────────────────────────
        if do_radius and radius_miles > 0:
            # Prefer actual lat/lon stored in lead_data
            lead_lat = lead_data.get("lat")
            lead_lon = lead_data.get("lon")
            if lead_lat and lead_lon:
                try:
                    ref = origin_coords or _city_coords(city_filter)
                    if ref:
                        dist = _haversine_miles(ref[0], ref[1], float(lead_lat), float(lead_lon))
                        if dist > radius_miles:
                            continue
                except (TypeError, ValueError):
                    pass
            else:
                # Fall back to city-name lookup
                lead_city_coords = _city_coords(row_dict.get("city", ""))
                if lead_city_coords and origin_coords:
                    dist = _haversine_miles(
                        origin_coords[0], origin_coords[1],
                        lead_city_coords[0], lead_city_coords[1],
                    )
                    if dist > radius_miles:
                        continue
                elif city_filter:
                    # Unknown city — include only if city name matches
                    lead_city = (row_dict.get("city") or "").lower()
                    if city_filter.lower() not in lead_city:
                        continue

        scoring = lead_data.get("_scoring", {}) or {}
        service_type = (
            row_dict.get("primary_service_type")
            or (row_dict["agent_sources"].split(",")[0]
                if row_dict.get("agent_sources") else None)
        )
        if not is_gc_interesting_lead(lead_data, service_type):
            continue
        gc_insight = build_gc_insight(lead_data, service_type)
        if not gc_insight.get("source_url"):
            continue
        service_info = service_types_map.get(service_type, {})

        desc = (lead_data.get("description") or lead_data.get("desc") or "")[:300]
        phone = (lead_data.get("contact_phone") or "").strip()
        email = (lead_data.get("contact_email") or "").strip()
        contractor = (lead_data.get("contractor") or "").strip()
        owner = (lead_data.get("owner") or "").strip()
        permit_type = (lead_data.get("permit_type") or "").strip()
        issued_date = (lead_data.get("issued_date") or lead_data.get("issue_date") or "").strip()[:10]
        lic_number  = (lead_data.get("lic_number") or lead_data.get("license") or "").strip()
        permit_id   = (lead_data.get("permit_id") or lead_data.get("id") or "").strip()
        state       = (lead_data.get("state") or lead_data.get("property_state") or lead_data.get("site_state") or "").strip()
        zip_code    = (lead_data.get("zip") or lead_data.get("zipcode") or lead_data.get("postal_code") or lead_data.get("site_zip") or "").strip()

        # Inspection data: prefer calendar table, fall back to lead_data predictor
        insp = insp_map.get(row_dict.get("address"), {})
        inspection_date = (
            insp.get("inspection_date")
            or lead_data.get("next_scheduled_inspection_date")
            or ""
        )
        if inspection_date:
            inspection_date = str(inspection_date).strip()[:10]
        inspection_type   = (insp.get("inspection_type") or lead_data.get("next_inspection_type") or "").strip()
        inspector_name    = (insp.get("inspector_name") or "").strip()
        tw_start          = (insp.get("time_window_start") or "").strip()
        tw_end            = (insp.get("time_window_end") or "").strip()
        time_window       = f"{tw_start} – {tw_end}" if tw_start and tw_end else tw_start or tw_end
        inspection_source = (lead_data.get("inspection_source") or "").strip()
        gc_probability    = insp.get("gc_presence_probability") or lead_data.get("_gc_presence_probability") or 0
        premium_quality_score, premium_quality_checks, is_elite_quality = _premium_quality(
            lead_data, gc_insight, service_type, scoring, inspection_date, row_dict.get("first_seen", "")
        )
        if elite_only and not is_elite_quality:
            continue
        elite_claimed_by_me = False
        elite_claim_expires_at = ""
        if is_elite_quality:
            claim = _active_elite_claim(c, row_dict["address_key"])
            if claim:
                if not user_id or int(claim["user_id"]) != int(user_id):
                    continue
                elite_claimed_by_me = True
                elite_claim_expires_at = claim["expires_at"] or ""

        leads.append({
            "id":               row_dict["address_key"],
            "address":          row_dict["address"],
            "city":             row_dict["city"],
            "state":            state,
            "zip":              zip_code,
            "description":      desc,
            "value":            lead_data.get("value_float") or 0,
            "score":            scoring.get("score", 0),
            "grade":            scoring.get("grade", ""),
            "grade_emoji":      scoring.get("grade_emoji", ""),
            "reasons":          scoring.get("reasons", [])[:4],
            "service_type":     service_type,
            "service_label":    service_info.get("label", ""),
            "service_emoji":    service_info.get("emoji", ""),
            "gc_insight":       gc_insight,
            "gc_confidence":    gc_insight.get("confidence", ""),
            "gc_badges":        gc_insight.get("badges", []),
            "source_url":       gc_insight.get("source_url", ""),
            "source_label":     gc_insight.get("source_label", ""),
            "contractor":       contractor,
            "owner":            owner,
            "phone":            phone,
            "email":            email,
            "permit_type":      permit_type,
            "issued_date":      issued_date,
            "lic_number":       lic_number,
            "permit_id":        permit_id,
            "inspection_date":  inspection_date,
            "inspection_type":  inspection_type,
            "inspector_name":   inspector_name,
            "time_window":      time_window,
            "inspection_source":inspection_source,
            "gc_probability":   round(float(gc_probability or 0), 2),
            "premium_quality_score": premium_quality_score,
            "premium_quality_checks": premium_quality_checks,
            "is_elite_quality": is_elite_quality,
            "elite_claimed_by_me": elite_claimed_by_me,
            "elite_claim_expires_at": elite_claim_expires_at,
            "elite_certificate": _elite_certificate(
                lead_data,
                gc_insight,
                premium_quality_score,
                premium_quality_checks,
                is_elite_quality,
                inspection_date,
                row_dict.get("first_seen", ""),
                elite_claimed_by_me,
                elite_claim_expires_at,
            ),
            "created_at":       row_dict.get("first_seen", ""),
            # AI classification fields (Qwen)
            "ai_trade":         lead_data.get("_trade", ""),
            "ai_urgency":       lead_data.get("_urgency", ""),
            "ai_summary":       lead_data.get("_ai_summary", ""),
            "ai_budget_min":    lead_data.get("_budget_min"),
            "ai_budget_max":    lead_data.get("_budget_max"),
            "ai_services":      lead_data.get("_services", []),
            "ai_is_residential":lead_data.get("_is_residential", False),
            "ai_is_commercial": lead_data.get("_is_commercial", False),
            # GC self-pull detection (gc_detector.py)
            "is_gc_self_pull":  lead_data.get("_is_gc_self_pull", False),
            "gc_pull_reason":   lead_data.get("_gc_pull_reason", ""),
        })

        if len(leads) >= limit:
            break

    conn.close()

    # Smart sort: location-based priority + score + inspection urgency
    from datetime import date as _date
    _today = _date.today()

    # Detect user location from IP
    _user_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    _user_geo = _get_ip_geo(_user_ip) if _user_ip else None

    def _sort_key(ld):
        insp = ld.get("inspection_date", "")
        urgency = 0
        if insp:
            try:
                days = (_date.fromisoformat(insp[:10]) - _today).days
                if 0 <= days <= 1:   urgency = 300
                elif days <= 3:      urgency = 200
                elif days <= 7:      urgency = 100
            except Exception:
                pass
        # Distance bonus: LOCAL leads always shown first
        dist_bonus = 0
        if _user_geo:
            lead_city = ld.get("city", "")
            lead_coords = _city_coords(lead_city)
            if lead_coords:
                dist = _haversine_miles(_user_geo[0], _user_geo[1], lead_coords[0], lead_coords[1])
                if dist <= 25:    dist_bonus = 1000  # Same city - ALWAYS first
                elif dist <= 50:  dist_bonus = 700
                elif dist <= 100: dist_bonus = 400
                elif dist <= 250: dist_bonus = 200
                elif dist <= 500: dist_bonus = 100
        return -(int(ld.get("score", 0)) + int(ld.get("premium_quality_score", 0)) + urgency + dist_bonus)
    leads.sort(key=_sort_key)

    response = {
        "leads":         leads,
        "auth_required": False,
        "anon_limit":    ANON_LEAD_LIMIT,
        "free_limit":    _get_app_const("FREE_USER_LEAD_LIMIT", 40),
        "pro_limit":     _get_app_const("PRO_LEAD_LIMIT", 200),
        "elite_limit":   _get_app_const("ELITE_LEAD_LIMIT", 80),
        "swipes_count":  swipes_count,
        "is_paid":       locals().get('is_paid', False) if user_id else None,
        "tier":          locals().get('subscription_tier', "free") if user_id else "anon",
        "elite_only":    elite_only,
        "billable_swipes_count": locals().get("billable_swipes_count", swipes_count),
        "replacement_credits": locals().get("replacement_credits", 0),
        "anon_id":       anon_id if not user_id else None,
    }
    if remaining is not None:
        response["remaining"] = remaining
    return jsonify(response), 200




@bp.route('/swipe/action', methods=['POST'])
def swipe_action():
    """
    Record a like/dislike swipe.

    Body: {"lead_id": "...", "action": "like"|"dislike", "anon_id": "..."}

    Returns the updated swipe counters and whether the anonymous
    budget is exhausted (so the client can open the login wall).
    """
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    action = (data.get("action") or "").strip().lower()

    if not lead_id or action not in ("like", "dislike"):
        return jsonify({"error": "lead_id and valid action required"}), 400

    user_id, anon_id = _resolve_swipe_identity()
    if not user_id and not anon_id:
        return jsonify({"error": "anon_id or auth required"}), 400

    if not user_id:
        current = _count_swipes(None, anon_id)
        if current >= ANON_LEAD_LIMIT:
            return jsonify({
                "ok": False,
                "auth_required": True,
                "auth_mode":    "register",
                "anon_limit":   ANON_LEAD_LIMIT,
                "swipes_count": current,
                "remaining":    0,
            }), 200
    else:
        _is_paid, _tier = _get_web_subscription(user_id)
        _current = _count_swipes(user_id, None)
        _limit = _tier_lead_limit(_tier, _is_paid)
        if _limit is not None and _current >= _limit:
            return jsonify({
                "ok": False,
                "auth_required": True,
                "auth_mode":    "upgrade",
                "free_limit":   _get_app_const("FREE_USER_LEAD_LIMIT", 40),
                "tier_limit":   _limit,
                "tier":         _tier,
                "swipes_count": _current,
                "remaining":    0,
            }), 200

    conn = get_db_connection()
    c = conn.cursor()
    claim_result = None
    try:
        if action == 'like' and user_id:
            c.execute("""
                SELECT address_key, lead_data, primary_service_type, first_seen
                FROM consolidated_leads
                WHERE address_key = ?
                LIMIT 1
            """, (lead_id,))
            lead_row = c.fetchone()
            if lead_row:
                is_elite_lead, _, _ = _is_elite_lead_record(dict(lead_row))
                if is_elite_lead:
                    _, user_tier = _get_web_subscription(user_id)
                    if user_tier != "elite":
                        conn.close()
                        return jsonify({
                            "ok": False,
                            "auth_required": True,
                            "auth_mode": "upgrade",
                            "required_tier": "elite",
                            "message": "Este lead Elite requiere plan Elite para reservarlo.",
                        }), 200
                    claim_result = _claim_elite_lead(conn, c, int(user_id), lead_id, "like")
                    if claim_result.get("blocked"):
                        conn.close()
                        return jsonify({
                            "ok": False,
                            "exclusive_unavailable": True,
                            "message": "Este lead Elite ya fue reservado por otro contratista.",
                            "expires_at": claim_result.get("expires_at", ""),
                        }), 409

        c.execute("""
            INSERT INTO swipe_actions (user_id, anon_id, lead_id, action)
            VALUES (?, ?, ?, ?)
        """, (user_id, anon_id, lead_id, action))
        # If an authenticated user liked the lead, also log it as a contact
        if action == 'like' and user_id:
            try:
                c.execute("""
                    INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
                    VALUES (?, ?, 'swipe_like', '')
                """, (user_id, lead_id))
            except Exception as log_err:
                logger.debug(f"lead_contacts log failed: {log_err}")
        # Auto-add to pipeline on like
        if action == 'like':
            _uid = user_id or anon_id
            if _uid:
                try:
                    c.execute(
                        "INSERT OR IGNORE INTO lead_pipeline (lead_id, user_id, status) VALUES (?, ?, 'Nuevo')",
                        (lead_id, _uid)
                    )
                except Exception as pl_err:
                    logger.debug(f"pipeline insert failed: {pl_err}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.warning(f"swipe_action insert failed: {e}")
        return jsonify({"error": "failed to record swipe"}), 500
    conn.close()

    # ── Alert admin after 50 consecutive rejections ───────────────────────────
    if action == 'dislike':
        try:
            _get_app_const("_check_and_alert_rejections", lambda *a: None)(user_id, anon_id)
        except Exception as _alert_err:
            logger.debug(f"rejection alert check failed: {_alert_err}")

    swipes_count = _count_swipes(user_id, anon_id)
    remaining = None
    auth_required = False
    if not user_id:
        remaining = max(ANON_LEAD_LIMIT - swipes_count, 0)
        auth_required = remaining == 0

    return jsonify({
        "ok":            True,
        "auth_required": auth_required,
        "anon_limit":    ANON_LEAD_LIMIT,
        "swipes_count":  swipes_count,
        "remaining":     remaining,
        "elite_claim":    claim_result,
    }), 200




@bp.route('/swipe/upgrade-info', methods=['GET'])
def swipe_upgrade_info():
    """Return current user's quota status."""
    user_id, _ = _resolve_swipe_identity()
    if not user_id:
        return jsonify({"anon": True, "limit": ANON_LEAD_LIMIT}), 200
    is_paid, tier = _get_web_subscription(user_id)
    swipes = _count_swipes(user_id, None)
    tier_limit = _tier_lead_limit(str(tier).lower(), is_paid)
    credit_count_fn = _get_app_const("_elite_replacement_credit_count", lambda _user_id: 0)
    replacement_credits = credit_count_fn(user_id) if str(tier).lower() == "elite" else 0
    billable_swipes = max(swipes - replacement_credits, 0)
    return jsonify({
        "is_paid":     is_paid,
        "tier":        tier,
        "swipes":      swipes,
        "billable_swipes": billable_swipes,
        "replacement_credits": replacement_credits,
        "free_limit":  _get_app_const("FREE_USER_LEAD_LIMIT", 40),
        "pro_limit":   _get_app_const("PRO_LEAD_LIMIT", 200),
        "elite_limit": _get_app_const("ELITE_LEAD_LIMIT", 80),
        "remaining":   None if tier_limit is None else max(tier_limit - billable_swipes, 0),
        "tiers": [
            {"id": "pro",     "price": 29,  "limit": _get_app_const("PRO_LEAD_LIMIT", 200), "label": "Pro"},
            {"id": "premium", "price": 99,  "limit": None,           "label": "Premium"},
            {"id": "elite",   "price": 500, "limit": _get_app_const("ELITE_LEAD_LIMIT", 80), "label": "Elite", "curated": True},
        ],
    }), 200


@bp.route('/swipe/elite-inventory', methods=['GET'])
def swipe_elite_inventory():
    """Return non-sensitive inventory counts for Elite plan sales/ops."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    return jsonify(_elite_inventory_payload(city, service)), 200


@bp.route('/swipe/market-readiness', methods=['GET'])
def swipe_market_readiness():
    """Return public-safe readiness by market for selling Elite."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    payload_fn = _get_app_const("_elite_market_readiness_payload")
    if callable(payload_fn):
        return jsonify(payload_fn(city, service)), 200
    return jsonify({
        "summary": {
            "ready_markets": 0,
            "pilot_markets": 0,
            "needs_inventory_markets": 0,
            "total_candidate_leads": 0,
            "total_elite_leads": 0,
        },
        "markets": [],
        "filters": {"city": city, "service": service},
        "thresholds": {},
    }), 200


@bp.route('/swipe/elite-sales-proof', methods=['GET'])
def swipe_elite_sales_proof():
    """Return public-safe proof points for selling the Elite tier."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    payload_fn = _get_app_const("_elite_sales_proof_payload")
    if callable(payload_fn):
        return jsonify(payload_fn(city, service)), 200
    return jsonify({
        "status": "needs_inventory",
        "recommended_price": 0,
        "headline": "Elite sales proof unavailable.",
        "proof_points": [],
        "market": None,
        "readiness": {},
    }), 200


@bp.route('/swipe/filter-options', methods=['GET'])
def swipe_filter_options():
    """Return live inventory counts for the Swipe filter drawer."""
    payload_fn = _get_app_const("_swipe_filter_options_payload")
    if callable(payload_fn):
        return jsonify(payload_fn(request.args)), 200
    return jsonify({
        "total_available": 0,
        "available_service_counts": {},
        "raw_service_counts": {},
        "filter_categories": [],
        "available_service_types": [],
        "top_cities": [],
        "score_buckets": {},
        "value_buckets": {},
        "filters": {},
    }), 200




@bp.route('/swipe/claim-anon', methods=['POST'])
@require_auth
def swipe_claim_anon():
    """
    After a successful OAuth login, migrate any anonymous swipes the
    user made (tracked by anon_id) onto their new user_id so their
    history carries over.

    Body: {"anon_id": "..."}
    """
    data = request.get_json(silent=True) or {}
    anon_id = (data.get("anon_id") or "").strip()
    if not anon_id:
        return jsonify({"ok": True, "migrated": 0}), 200

    conn = get_db_connection()
    c = conn.cursor()

    # Get anon likes before migrating (to create lead_contacts records)
    c.execute("""
        SELECT lead_id FROM swipe_actions
        WHERE anon_id = ? AND action = 'like' AND user_id IS NULL
          AND lead_id NOT IN (SELECT lead_id FROM swipe_actions WHERE user_id = ?)
    """, (anon_id, g.user_id))
    anon_like_ids = [row[0] for row in c.fetchall()]

    c.execute("""
        UPDATE swipe_actions
           SET user_id = ?, anon_id = NULL
         WHERE anon_id = ?
           AND user_id IS NULL
           AND lead_id NOT IN (
               SELECT lead_id FROM swipe_actions WHERE user_id = ?
           )
    """, (g.user_id, anon_id, g.user_id))
    migrated = c.rowcount

    # Also insert lead_contacts for every migrated like so history shows up in profile
    for lead_id in anon_like_ids:
        try:
            c.execute("""
                INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
                VALUES (?, ?, 'swipe_like', 'migrated from anonymous session')
            """, (g.user_id, lead_id))
        except Exception:
            pass

    # Drop any remaining anon rows for leads the user already swiped
    c.execute("DELETE FROM swipe_actions WHERE anon_id = ?", (anon_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "migrated": migrated}), 200




@bp.route('/swipe/cities', methods=['GET'])
def swipe_cities():
    """Return known city names from live inventory plus geocode fallbacks."""
    q = (request.args.get('q') or '').strip().lower()
    city_set = set(CITY_COORDS.keys())
    try:
        conn = get_db_connection()
        c = conn.cursor()
        if q:
            c.execute("""
                SELECT city, COUNT(*) AS n
                FROM consolidated_leads
                WHERE TRIM(COALESCE(city, '')) != ''
                  AND LOWER(city) LIKE LOWER(?)
                GROUP BY city
                ORDER BY n DESC, city ASC
                LIMIT 80
            """, (f"%{q}%",))
        else:
            c.execute("""
                SELECT city, COUNT(*) AS n
                FROM consolidated_leads
                WHERE TRIM(COALESCE(city, '')) != ''
                GROUP BY city
                ORDER BY n DESC, city ASC
                LIMIT 80
            """)
        city_set.update(str(row[0]).strip().lower() for row in c.fetchall() if row[0])
        conn.close()
    except Exception as e:
        logger.debug(f"City autocomplete DB lookup failed: {e}")

    cities = sorted(city_set)
    if q:
        # Prefix matches first, then contains matches
        prefix = [c for c in cities if c.startswith(q)]
        contains = [c for c in cities if q in c and not c.startswith(q)]
        cities = (prefix + contains)[:20]
    else:
        cities = cities[:40]
    return jsonify([c.title() for c in cities]), 200




@bp.route('/swipe/feedback', methods=['POST'])
def swipe_feedback():
    """Store beta feedback from users (no auth required)."""
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()[:2000]
    if not message:
        return jsonify({"error": "message required"}), 400
    anon_id = (data.get('anon_id') or '').strip()[:64] or None
    user_id, _ = _resolve_swipe_identity()
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO beta_feedback (message, anon_id, user_id) VALUES (?, ?, ?)",
            (message, anon_id, user_id)
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"beta_feedback insert failed: {e}")
        conn.close()
        return jsonify({"error": "failed to save feedback"}), 500
    conn.close()
    return jsonify({"ok": True}), 200


@bp.route('/swipe/report-lead', methods=['POST'])
@require_auth
def swipe_report_lead_quality():
    """Let users report bad leads so Elite inventory can be replaced/improved."""
    user_id = g.user_id
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    reason = (data.get("reason") or "other").strip().lower()
    details = (data.get("details") or "").strip()[:1000]
    allowed_reasons = {"no_contact", "wrong_number", "already_taken", "not_interested", "bad_source", "duplicate", "other"}
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    if reason not in allowed_reasons:
        reason = "other"

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT address_key, lead_data, primary_service_type, first_seen
            FROM consolidated_leads
            WHERE address_key = ?
            LIMIT 1
        """, (lead_id,))
        lead_row = c.fetchone()
        if not lead_row:
            return jsonify({"error": "lead not found"}), 404
        is_elite_lead, _, _ = _is_elite_lead_record(dict(lead_row))
        c.execute("""
            INSERT INTO lead_quality_reports (lead_id, user_id, reason, details, is_elite, status)
            VALUES (?, ?, ?, ?, ?, 'open')
            ON CONFLICT(user_id, lead_id, reason) DO UPDATE SET
                details = excluded.details,
                is_elite = excluded.is_elite,
                status = 'open',
                updated_at = CURRENT_TIMESTAMP
        """, (lead_id, int(user_id), reason, details, 1 if is_elite_lead else 0))
        if is_elite_lead:
            c.execute("""
                UPDATE elite_lead_claims
                   SET status = 'reported',
                       expires_at = CURRENT_TIMESTAMP
                 WHERE lead_id = ? AND user_id = ?
            """, (lead_id, int(user_id)))
            grant_credit_fn = _get_app_const("_grant_elite_replacement_credit")
            if callable(grant_credit_fn):
                credit_granted = grant_credit_fn(
                    c,
                    int(user_id),
                    lead_id,
                    reason,
                    "Auto-granted from Elite lead quality report",
                )
            c.execute("""
                SELECT COUNT(*)
                FROM elite_replacement_credits
                WHERE user_id = ? AND status = 'open'
            """, (int(user_id),))
            row = c.fetchone()
            replacement_credits = int(row[0]) if row else 0
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"lead quality report failed: {e}")
        return jsonify({"error": "failed to report lead"}), 500
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "is_elite": is_elite_lead,
        "replacement_review": bool(is_elite_lead),
        "replacement_credit_granted": credit_granted,
        "replacement_credits": replacement_credits,
        "message": "Gracias. Revisaremos este lead para reemplazo/mejora de calidad.",
    }), 200





@bp.route('/swipe/save-to-crm', methods=['POST'])
def save_to_crm():
    """Save a lead to Huly CRM for follow-up tracking."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id") or data.get("address_key")
    
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    
    user_id, anon_id = _resolve_swipe_identity()
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lead_data, address, city, primary_service_type FROM consolidated_leads WHERE address_key = ?", (lead_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "Lead not found"}), 404
    
    lead_data = json.loads(row["lead_data"] or "{}")
    lead = {
        "id": lead_id,
        "address": row["address"],
        "city": row["city"],
        "contractor": lead_data.get("contractor", ""),
        "contact_phone": lead_data.get("contact_phone", ""),
        "value_float": lead_data.get("value_float", 0),
        "description": lead_data.get("description", ""),
        "ai_trade": lead_data.get("_trade", ""),
        "ai_urgency": lead_data.get("_urgency", "MEDIUM"),
        "ai_summary": lead_data.get("_ai_summary", ""),
        "ai_key_pain_point": lead_data.get("_key_pain_point", ""),
        "ai_upsell_opportunity": lead_data.get("_upsell_opportunity", ""),
        "ai_best_contact_time": lead_data.get("_best_contact_time", ""),
        "ai_project_scope": lead_data.get("_project_scope", ""),
        "ai_decision_maker": lead_data.get("_decision_maker", ""),
        "is_gc_self_pull": lead_data.get("_is_gc_self_pull", False),
        "original_trade": lead_data.get("_original_trade", ""),
        "score": lead_data.get("_scoring", {}).get("score", 0) if isinstance(lead_data.get("_scoring"), dict) else 0,
    }
    
    # Format for Huly CRM
    trade = lead.get("ai_trade") or "GENERAL"
    score = lead.get("score", 0)
    urgency = lead.get("ai_urgency", "MEDIUM")
    address = lead.get("address", "")
    city = lead.get("city", "")
    contractor = lead.get("contractor", "")
    phone = lead.get("contact_phone", "")
    value = lead.get("value_float", 0)
    desc = (lead.get("description") or "")[:200]
    ai_summary = lead.get("ai_summary", "")
    pain = lead.get("ai_key_pain_point", "")
    upsell = lead.get("ai_upsell_opportunity", "")
    best_time = lead.get("ai_best_contact_time", "")
    
    prefix = "🔥" if score >= 90 else "🌡️" if score >= 70 else ""
    project_name = f"{prefix} [{trade}] {address[:50]}"
    
    project_desc = f"Source: MLeads Swipe\nScore: {score}/100 | {urgency}"
    if contractor: project_desc += f"\nGC: {contractor}"
    if phone: project_desc += f"\nPhone: {phone}"
    if value: project_desc += f"\nValue: ${value:,.0f}"
    if desc: project_desc += f"\n{desc}"
    if ai_summary: project_desc += f"\nAI: {ai_summary}"
    if pain: project_desc += f"\nPain: {pain}"
    if upsell: project_desc += f"\nUpsell: {upsell}"
    
    # Return the formatted data for the frontend to open in Huly
    return jsonify({
        "ok": True,
        "project_name": project_name,
        "project_description": project_desc,
        "huly_url": "http://45.32.89.38:8080",
        "lead": lead,
    })



@bp.route('/swipe/my-contacts', methods=['GET'])
def swipe_my_contacts():
    """Return leads the authenticated user has swiped right on (liked)."""
    user_id, _ = _resolve_swipe_identity()
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT sa.lead_id, MAX(sa.created_at) as contacted_at,
               cl.address, cl.city, cl.lead_data
        FROM swipe_actions sa
        JOIN consolidated_leads cl ON cl.address_key = sa.lead_id
        WHERE sa.user_id = ? AND sa.action = 'like'
        GROUP BY sa.lead_id
        ORDER BY contacted_at DESC
        LIMIT 100
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    contacts = []
    for row in rows:
        rd = dict(row)
        try:
            ld = json.loads(rd.get('lead_data') or '{}')
        except Exception:
            ld = {}
        scoring = ld.get('_scoring', {}) or {}
        contacts.append({
            'id':           rd['lead_id'],
            'address':      rd['address'],
            'city':         rd['city'],
            'contacted_at': rd['contacted_at'],
            'score':        scoring.get('score', 0),
            'grade':        scoring.get('grade', ''),
            'phone':        (ld.get('contact_phone') or '').strip(),
            'email':        (ld.get('contact_email') or '').strip(),
            'value':        ld.get('value_float', 0),
        })
    return jsonify({'contacts': contacts}), 200




@bp.route('/swipe/log-contact', methods=['POST'])
def swipe_log_contact():
    """Log that an authenticated user clicked a phone/email contact on a lead."""
    user_id, _ = _resolve_swipe_identity()
    if not user_id:
        return jsonify({"ok": False}), 200  # silently ignore anon
    data = request.get_json(silent=True) or {}
    lead_id = (data.get('lead_id') or '').strip()
    contact_type = data.get('contact_type', 'phone')
    if contact_type not in {'phone', 'email', 'text', 'visit', 'other'}:
        contact_type = 'other'
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    conn = get_db_connection()
    c = conn.cursor()
    claim_result = None
    try:
        c.execute("""
            SELECT address_key, lead_data, primary_service_type, first_seen
            FROM consolidated_leads
            WHERE address_key = ?
            LIMIT 1
        """, (lead_id,))
        lead_row = c.fetchone()
        if lead_row:
            is_elite_lead, _, _ = _is_elite_lead_record(dict(lead_row))
            if is_elite_lead:
                _, user_tier = _get_web_subscription(user_id)
                if user_tier != "elite":
                    conn.close()
                    return jsonify({"ok": False, "auth_required": True, "auth_mode": "upgrade", "required_tier": "elite"}), 200
                claim_result = _claim_elite_lead(conn, c, int(user_id), lead_id, contact_type)
                if claim_result.get("blocked"):
                    conn.close()
                    return jsonify({"ok": False, "exclusive_unavailable": True, "expires_at": claim_result.get("expires_at", "")}), 409
        c.execute("""
            INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
            VALUES (?, ?, ?, '')
        """, (user_id, lead_id, contact_type))
        conn.commit()
    except Exception as e:
        logger.debug(f"swipe_log_contact failed: {e}")
    conn.close()
    return jsonify({"ok": True, "elite_claim": claim_result}), 200




@bp.route('/swipe/pulse', methods=['GET'])
def swipe_pulse():
    """
    Lightweight polling endpoint for real-time sync.
    Returns the count of leads newer than `since` (ISO timestamp).
    The swipe UI polls this every 30s and refreshes the deck when new leads arrive.
    """
    since = request.args.get("since", "")
    conn = get_db_connection()
    c = conn.cursor()
    try:
        if since:
            c.execute(
                "SELECT COUNT(*) FROM consolidated_leads WHERE last_updated > ?",
                (since,),
            )
        else:
            c.execute("SELECT COUNT(*) FROM consolidated_leads")
        total_new = c.fetchone()[0]

        c.execute("SELECT MAX(last_updated) FROM consolidated_leads")
        latest = c.fetchone()[0] or ""
    finally:
        conn.close()

    return jsonify({
        "new_since": total_new,
        "latest_update": latest,
        "server_time": datetime.utcnow().isoformat(),
    }), 200
