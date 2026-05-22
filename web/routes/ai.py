"""
ai_routes.py — Ai API routes
Extracted from app.py by refactor_extract4.py
"""
from flask import Blueprint, request, jsonify

bp = Blueprint('ai_routes', __name__)

def ai_classify_lead():
    """
    Clasifica un lead con Qwen.
    Body: { "lead_id": "..." } o { "lead": { lead dict } }
    """
    body = request.get_json(silent=True) or {}
    lead_id = body.get("lead_id")
    lead_dict = body.get("lead")

    if lead_id:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (lead_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Lead not found"}), 404
        try:
            lead_dict = json.loads(row["lead_data"])
        except Exception:
            return jsonify({"error": "Invalid lead_data"}), 500

    if not lead_dict:
        return jsonify({"error": "Provide lead_id or lead"}), 400

    try:
        from utils.ai_classifier import enrich_lead_with_classification, get_cache_stats
        enriched = enrich_lead_with_classification(dict(lead_dict))
        return jsonify({
            "trade":       enriched.get("_trade"),
            "urgency":     enriched.get("_urgency"),
            "budget_min":  enriched.get("_budget_min"),
            "budget_max":  enriched.get("_budget_max"),
            "services":    enriched.get("_services", []),
            "summary":     enriched.get("_ai_summary"),
            "is_residential": enriched.get("_is_residential"),
            "is_commercial":  enriched.get("_is_commercial"),
            "owner_type":     enriched.get("_owner_type"),
            "source":         enriched.get("_classifier_source"),
            "cache_stats":    get_cache_stats(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def ai_classify_batch():
    """
    Clasifica los N leads más recientes sin clasificar con Qwen.
    Body: { "limit": 100 }
    """
    body = request.get_json(silent=True) or {}
    limit = min(int(body.get("limit", 50)), 500)

    conn = get_db_connection()
    rows = conn.execute("""
        SELECT address_key, lead_data FROM consolidated_leads
        WHERE lead_data NOT LIKE '%_classifier_source%'
          AND (lead_data LIKE '%description%' OR lead_data LIKE '%permit_type%')
        ORDER BY last_updated DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    if not rows:
        return jsonify({"status": "nothing_to_classify", "count": 0})

    import threading
    def _batch():
        try:
            from utils.ai_classifier import enrich_lead_with_classification
            classified = 0
            CHUNK = 20  # commit every 20 records to avoid long DB locks
            for i in range(0, len(rows), CHUNK):
                chunk = rows[i:i + CHUNK]
                db = get_db_connection()
                try:
                    for row in chunk:
                        try:
                            ld = json.loads(row["lead_data"] or "{}")
                            enriched = enrich_lead_with_classification(ld)
                            db.execute(
                                "UPDATE consolidated_leads SET lead_data=?, last_updated=? WHERE address_key=?",
                                (json.dumps(enriched, default=str), datetime.utcnow().isoformat(), row["address_key"])
                            )
                            classified += 1
                        except Exception as e:
                            logger.warning(f"batch classify error: {e}")
                    db.commit()
                except Exception as e:
                    logger.error(f"[AI Batch] chunk commit failed: {e}")
                finally:
                    db.close()
            logger.info(f"[AI Batch] classified {classified} leads with Qwen")
        except Exception as e:
            logger.error(f"[AI Batch] failed: {e}")

    threading.Thread(target=_batch, daemon=True).start()
    return jsonify({"status": "started", "leads_queued": len(rows)}), 202

def crossdata_run():
    """Dispara manualmente un ciclo de predicción cross-data."""
    import threading
    def _run():
        try:
            from agents.crossdata_agent import run_cross_prediction
            run_cross_prediction()
        except Exception as e:
            logger.error(f"crossdata manual run error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "message": "Cross-data prediction running in background"}), 202

def crossdata_stats():
    """Estadísticas del agente cross-data."""
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM consolidated_leads WHERE lead_data LIKE '%_cross_prediction%'")
        cross_leads = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM consolidated_leads WHERE agent_sources LIKE '%,%'")
        multi_agent = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM scheduled_inspections")
        inspections = c.fetchone()[0]

        c.execute("""
            SELECT json_extract(lead_data, '$._cross_prediction.combo_matched') as combo, COUNT(*) as cnt
            FROM consolidated_leads
            WHERE lead_data LIKE '%combo_matched%'
            GROUP BY combo ORDER BY cnt DESC LIMIT 10
        """)
        combos = [{"combo": r[0], "count": r[1]} for r in c.fetchall()]

        c.execute("""
            SELECT json_extract(lead_data, '$._cross_prediction.urgency') as urgency, COUNT(*) as cnt
            FROM consolidated_leads
            WHERE lead_data LIKE '%_cross_prediction%'
            GROUP BY urgency ORDER BY cnt DESC
        """)
        by_urgency = {r[0]: r[1] for r in c.fetchall()}

        c.execute("SELECT COUNT(*) FROM property_signals WHERE agent_key='crossdata'")
        crossdata_signals = c.fetchone()[0]

    finally:
        conn.close()

    return jsonify({
        "cross_predicted_leads": cross_leads,
        "multi_agent_leads": multi_agent,
        "scheduled_inspections": inspections,
        "crossdata_signals": crossdata_signals,
        "by_urgency": by_urgency,
        "top_combos": combos,
    })

def get_active_disasters():
    """Get currently active disaster events (requires PostgreSQL)."""
    use_pg = os.getenv("USE_POSTGRES", "").lower() in ("1", "true")
    if not use_pg:
        return jsonify({"error": "Disaster Intelligence requires PostgreSQL (USE_POSTGRES=1)"}), 501
    try:
        from db_postgres import get_conn, put_conn
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, event_type, source, severity, description,
                       affected_cities, started_at, ended_at
                FROM disaster_events
                WHERE ended_at IS NULL OR ended_at > NOW()
                ORDER BY started_at DESC LIMIT 50
            """)
            disasters = []
            for row in cur.fetchall():
                r = dict(row)
                r['started_at'] = str(r.get('started_at', ''))
                r['ended_at'] = str(r.get('ended_at', ''))
                disasters.append(r)
        put_conn(conn)
        return jsonify({"disasters": disasters, "count": len(disasters)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def run_disaster_check():
    """Manually trigger disaster intelligence scan."""
    import threading
    def _run():
        try:
            from agents.disaster_agent import DisasterAgent
            agent = DisasterAgent()
            leads = agent.fetch_leads()
            if leads:
                agent.send_batch(leads)
        except Exception as e:
            logger.error(f"Disaster manual run error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"}), 202

def get_property_dna(lead_id):
    """Get Property DNA data for a specific lead."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (lead_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Lead not found"}), 404
    ld = json.loads(row["lead_data"] or "{}") if row["lead_data"] else {}
    return jsonify({
        "lead_id": lead_id,
        "property_year_built": ld.get("property_year_built"),
        "property_roof_material": ld.get("property_roof_material"),
        "property_value": ld.get("property_value"),
        "property_sqft": ld.get("property_sqft"),
        "flood_zone": ld.get("flood_zone"),
        "source": ld.get("_property_dna_source"),
    }), 200

def enrich_property_dna():
    """Batch enrich leads with Property DNA."""
    data = request.get_json(silent=True) or {}
    limit = min(int(data.get("limit", 20)), 100)
    import threading
    def _enrich():
        try:
            from utils.property_dna import get_property_dna
            import sqlite3
            dna = get_property_dna()
            conn2 = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            conn2.row_factory = sqlite3.Row
            rows = conn2.execute("""
                SELECT address_key, address, city, lead_data FROM consolidated_leads
                WHERE lead_data NOT LIKE '%property_year_built%' LIMIT ?
            """, (limit,)).fetchall()
            conn2.close()
            enriched = 0
            for row in rows:
                try:
                    lead = {"address": row["address"], "city": row["city"]}
                    lead = dna.enrich_lead(lead)
                    ld = json.loads(row["lead_data"] or "{}")
                    for k in ["property_year_built", "property_roof_material",
                              "property_value", "property_sqft", "flood_zone",
                              "_property_dna_source"]:
                        if k in lead:
                            ld[k] = lead[k]
                    db = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
                    db.execute("UPDATE consolidated_leads SET lead_data=? WHERE address_key=?",
                              (json.dumps(ld, default=str), row["address_key"]))
                    db.commit()
                    db.close()
                    enriched += 1
                except Exception as e:
                    logger.debug(f"Property DNA enrich error: {e}")
            logger.info(f"[PropertyDNA] Enriched {enriched}/{len(rows)} leads")
        except Exception as e:
            logger.error(f"Property DNA batch error: {e}")
    threading.Thread(target=_enrich, daemon=True).start()
    return jsonify({"status": "started", "limit": limit}), 202

def get_tripartite_score(lead_id):
    """Get tripartite scores (sub/gc/insurance) for a lead."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (lead_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Lead not found"}), 404
    ld = json.loads(row["lead_data"] or "{}") if row["lead_data"] else {}
    if "_tripartite" in ld:
        return jsonify({"lead_id": lead_id, **ld["_tripartite"]}), 200
    try:
        from utils.tripartite_scoring import calculate_tripartite_scores
        scores = calculate_tripartite_scores(ld)
        return jsonify({"lead_id": lead_id, **scores}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def batch_tripartite_scoring():
    """Calculate tripartite scores for all leads missing them."""
    data = request.get_json(silent=True) or {}
    limit = min(int(data.get("limit", 50)), 500)
    import threading
    def _score():
        try:
            from utils.tripartite_scoring import calculate_tripartite_scores
            import sqlite3
            conn2 = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            conn2.row_factory = sqlite3.Row
            rows = conn2.execute("""
                SELECT address_key, lead_data FROM consolidated_leads
                WHERE lead_data NOT LIKE '%_tripartite%' LIMIT ?
            """, (limit,)).fetchall()
            conn2.close()
            scored = 0
            for row in rows:
                try:
                    ld = json.loads(row["lead_data"] or "{}")
                    scores = calculate_tripartite_scores(ld)
                    ld["_tripartite"] = scores
                    db = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
                    db.execute("UPDATE consolidated_leads SET lead_data=? WHERE address_key=?",
                              (json.dumps(ld, default=str), row["address_key"]))
                    db.commit()
                    db.close()
                    scored += 1
                except Exception as e:
                    logger.debug(f"Tripartite score error: {e}")
            logger.info(f"[Tripartite] Scored {scored}/{len(rows)} leads")
        except Exception as e:
            logger.error(f"Tripartite batch error: {e}")
    threading.Thread(target=_score, daemon=True).start()
    return jsonify({"status": "started", "limit": limit}), 202

def verify_cslb_license(license_number):
    """Verify a CSLB license by number."""
    try:
        from utils.cslb_verifier import verify_license
        result = verify_license(license_number)
        if result:
            return jsonify(result), 200
        return jsonify({"error": "License not found", "license_number": license_number}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def search_cslb_license():
    """Search CSLB by business name."""
    business_name = request.args.get('name', '')
    city = request.args.get('city', '')
    if not business_name or len(business_name) < 3:
        return jsonify({"error": "Name must be at least 3 characters"}), 400
    try:
        from utils.cslb_verifier import search_by_name
        results = search_by_name(business_name, city)
        return jsonify({"results": results, "count": len(results)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def verify_subcontractor_cslb():
    """Verify a subcontractor's license against claimed specialties."""
    data = request.get_json() or {}
    license_number = data.get('license_number', '')
    specialties = data.get('specialties', [])
    if not license_number:
        return jsonify({"error": "license_number required"}), 400
    try:
        from utils.cslb_verifier import verify_subcontractor_profile
        result = verify_subcontractor_profile(license_number, specialties)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def batch_verify_cslb():
    """Batch verify multiple CSLB licenses."""
    data = request.get_json() or {}
    licenses = data.get('licenses', [])
    if not licenses or len(licenses) > 20:
        return jsonify({"error": "Provide 1-20 license numbers"}), 400
    import threading
    def _batch():
        try:
            from utils.cslb_verifier import batch_verify
            results = batch_verify(licenses)
            verified = sum(1 for v in results.values() if v and v.get("is_active"))
            logger.info(f"[CSLB] Batch verified {verified}/{len(licenses)} licenses")
        except Exception as e:
            logger.error(f"[CSLB] Batch verify error: {e}")
    threading.Thread(target=_batch, daemon=True).start()
    return jsonify({"status": "started", "count": len(licenses)}), 202
