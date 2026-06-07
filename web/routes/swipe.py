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
bp = Blueprint("swipe", __name__)

# Lazy imports to avoid circular dependency with web.app
def _get_app_const(name, default=None):
    """Get a constant from web.app without circular import."""
    try:
        import web.app as _app_mod
        return getattr(_app_mod, name, default)
    except Exception:
        return default


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
        conn2 = get_db_connection()
        c2 = conn2.cursor()
        c2.execute("SELECT COALESCE(is_paid, 0) FROM users WHERE id = ?", (user_id,))
        row2 = c2.fetchone()
        conn2.close()
        is_paid = bool(row2 and row2[0])
        if not is_paid and swipes_count >= _get_app_const("FREE_USER_LEAD_LIMIT", 40):
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "upgrade",
                "free_limit":   _get_app_const("FREE_USER_LEAD_LIMIT", 40),
                "swipes_count": swipes_count,
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
        cat_or_parts = []
        trade_map = _get_app_const("_TRADE_SERVICE_TO_AI", {
            "roofing": "ROOFING", "drywall": "DRYWALL", "paint": "PAINTING",
            "electrical": "ELECTRICAL", "plumbing": "PLUMBING", "hvac": "HVAC",
            "flooring": "FLOORING", "concrete": "CONCRETE", "framing": "FRAMING",
            "windows": "WINDOWS", "landscaping": "LANDSCAPING", "deconstruction": "DEMOLITION",
            "insulation": "INSULATION",
        })
        service_type_cats = _get_app_const("_SERVICE_TYPE_CATS", set())
        for cat in selected_cats:
            if cat in trade_map:
                ai_trade = trade_map[cat]
                cat_or_parts.append(
                    "(primary_service_type = ? "
                    "OR UPPER(COALESCE(json_extract(lead_data, '$._trade'), '')) = ? "
                    "OR UPPER(COALESCE(json_extract(lead_data, '$._sub_trades'), '')) LIKE ?)"
                )
                params.extend([cat, ai_trade, f'%"{ai_trade}"%'])
            elif cat in service_type_cats:
                cat_or_parts.append("primary_service_type = ?")
                params.append(cat)
        if cat_or_parts:
            conditions.append("(" + " OR ".join(cat_or_parts) + ")")

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

    conn.close()

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

        leads.append({
            "id":               row_dict["address_key"],
            "address":          row_dict["address"],
            "city":             row_dict["city"],
            "description":      desc,
            "value":            lead_data.get("value_float") or 0,
            "score":            scoring.get("score", 0),
            "grade":            scoring.get("grade", ""),
            "grade_emoji":      scoring.get("grade_emoji", ""),
            "reasons":          scoring.get("reasons", [])[:4],
            "service_type":     service_type,
            "service_label":    service_info.get("label", ""),
            "service_emoji":    service_info.get("emoji", ""),
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
        return -(int(ld.get("score", 0)) + urgency + dist_bonus)
    leads.sort(key=_sort_key)

    response = {
        "leads":         leads,
        "auth_required": False,
        "anon_limit":    ANON_LEAD_LIMIT,
        "free_limit":    _get_app_const("FREE_USER_LEAD_LIMIT", 40),
        "swipes_count":  swipes_count,
        "is_paid":       locals().get('is_paid', False) if user_id else None,
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

    # Anonymous users can swipe freely - registration only needed to send estimates
    # (removed auth_required gate for anonymous users)
    else:
        # Check free-tier quota for authenticated non-paid users
        _conn = get_db_connection()
        _c = _conn.cursor()
        _c.execute("SELECT COALESCE(is_paid, 0) FROM users WHERE id = ?", (user_id,))
        _row = _c.fetchone()
        _conn.close()
        _is_paid = bool(_row and _row[0])
        # Free users can swipe freely - payment only needed for premium features
        # (removed auth_required gate for free users)

    conn = get_db_connection()
    c = conn.cursor()
    try:
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
    }), 200




@bp.route('/swipe/upgrade-info', methods=['GET'])
def swipe_upgrade_info():
    """Return current user's quota status."""
    user_id, _ = _resolve_swipe_identity()
    if not user_id:
        return jsonify({"anon": True, "limit": ANON_LEAD_LIMIT}), 200
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COALESCE(is_paid, 0) FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    is_paid = bool(row and row[0])
    swipes = _count_swipes(user_id, None)
    return jsonify({
        "is_paid":     is_paid,
        "swipes":      swipes,
        "free_limit":  _get_app_const("FREE_USER_LEAD_LIMIT", 40),
        "pro_limit":   _get_app_const("PRO_LEAD_LIMIT", 200),
        "remaining":   None if is_paid else max(_get_app_const("FREE_USER_LEAD_LIMIT", 40) - swipes, 0),
        "tiers": [
            {"id": "pro",     "price": 29,  "limit": _get_app_const("PRO_LEAD_LIMIT", 200), "label": "Pro"},
            {"id": "premium", "price": 99,  "limit": None,           "label": "Premium"},
        ],
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
    """Return a list of known city names for autocomplete (no auth required)."""
    q = (request.args.get('q') or '').strip().lower()
    cities = sorted(CITY_COORDS.keys())
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
    try:
        c.execute("""
            INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
            VALUES (?, ?, ?, '')
        """, (user_id, lead_id, contact_type))
        conn.commit()
    except Exception as e:
        logger.debug(f"swipe_log_contact failed: {e}")
    conn.close()
    return jsonify({"ok": True}), 200




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




