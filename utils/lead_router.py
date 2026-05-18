"""
utils/lead_router.py
🔀 Lead Assignment Router — Asigna leads al GC o Sub correcto

Lógica:
  1. Lead entra con tripartite scores
  2. Si gc_score >= 60 → router busca GCs en el área
  3. Si sub_score >= 60 → router busca Subs con la especialidad
  4. Si insurance_score >= 60 → marca para follow-up aseguradora (Fase 2)
  5. Asigna el lead al mejor match (score más alto + disponibilidad)
  6. Notifica al asignado vía su canal preferido

Criterios de matching:
  - Especialidad del sub coincide con trade del lead
  - Zona de servicio del GC/Sub incluye la ciudad del lead
  - El GC/Sub no ha superado su límite mensual de leads
  - Prioridad: Pro/Premium > Free (within score range)
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class LeadRouter:
    """Routes leads to the most appropriate GC or Sub."""

    def __init__(self):
        self.min_gc_score = int(os.getenv("ROUTER_MIN_GC_SCORE", "60"))
        self.min_sub_score = int(os.getenv("ROUTER_MIN_SUB_SCORE", "60"))
        self.min_ins_score = int(os.getenv("ROUTER_MIN_INS_SCORE", "50"))

    def route_lead(self, lead: dict, tripartite: dict) -> dict:
        """
        Route a lead to appropriate users.
        
        Args:
            lead: The lead data
            tripartite: Output from calculate_tripartite_scores()
            
        Returns:
            {
                "assigned_gc": Optional[int],  # user ID
                "assigned_sub": Optional[int], # user ID
                "gc_candidates": int,           # how many GCs matched
                "sub_candidates": int,          # how many Subs matched
                "insurance_flagged": bool,      # should insurance follow up
                "routing_reason": str,
            }
        """
        result = {
            "assigned_gc": None,
            "assigned_sub": None,
            "gc_candidates": 0,
            "sub_candidates": 0,
            "insurance_flagged": False,
            "routing_reason": "",
        }

        gc_score = tripartite.get("gc_score", 0)
        sub_score = tripartite.get("sub_score", 0)
        ins_score = tripartite.get("insurance_score", 0)

        city = lead.get("city", "")
        trade = lead.get("_trade", "").upper()

        # ── Route to GC ──────────────────────────────────────
        if gc_score >= self.min_gc_score:
            gc_match = self._find_best_gc(city, trade, gc_score)
            if gc_match:
                result["assigned_gc"] = gc_match["id"]
                result["gc_candidates"] = gc_match.get("candidates", 1)
                result["routing_reason"] += f"GC match (score {gc_score}). "

        # ── Route to Sub ─────────────────────────────────────
        if sub_score >= self.min_sub_score:
            sub_match = self._find_best_sub(city, trade, sub_score)
            if sub_match:
                result["assigned_sub"] = sub_match["id"]
                result["sub_candidates"] = sub_match.get("candidates", 1)
                result["routing_reason"] += f"Sub match ({trade}, score {sub_score}). "

        # ── Insurance flag ───────────────────────────────────
        if ins_score >= self.min_ins_score:
            result["insurance_flagged"] = True
            result["routing_reason"] += f"Insurance flagged (score {ins_score}). "

        if not result["routing_reason"]:
            result["routing_reason"] = f"Below thresholds (gc={gc_score}, sub={sub_score}, ins={ins_score})"

        return result

    def _find_best_gc(self, city: str, trade: str, lead_score: int) -> Optional[dict]:
        """Find the best GC for a lead."""
        use_postgres = os.getenv("USE_POSTGRES", "").lower() in ("1", "true")

        if use_postgres:
            return self._find_best_gc_pg(city, trade, lead_score)
        else:
            return self._find_best_gc_sqlite(city, trade, lead_score)

    def _find_best_gc_sqlite(self, city: str, trade: str, lead_score: int) -> Optional[dict]:
        """Find best GC using SQLite."""
        try:
            import sqlite3
            conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # Find GCs whose service area includes this city
            c.execute("""
                SELECT u.id, u.username, u.email, u.full_name, u.plan_tier,
                       u.monthly_lead_limit, u.current_month_leads,
                       u.notification_prefs, u.profile_data
                FROM users u
                WHERE u.role = 'gc'
                  AND u.is_active = 1
                  AND (u.expires_at IS NULL OR u.expires_at > datetime('now'))
                  AND u.current_month_leads < u.monthly_lead_limit
                ORDER BY
                    CASE u.plan_tier
                        WHEN 'premium' THEN 3
                        WHEN 'pro' THEN 2
                        WHEN 'free' THEN 1
                        ELSE 0
                    END DESC,
                    u.current_month_leads ASC
                LIMIT 5
            """)

            candidates = []
            for row in c.fetchall():
                r = dict(row)
                # Check if GC's service area includes this city
                profile = {}
                try:
                    profile = json.loads(r.get("profile_data") or "{}")
                except:
                    pass

                service_areas = profile.get("service_areas", [])
                preferred_trades = profile.get("preferred_trades", [])
                min_lead_score = profile.get("min_lead_score", 0)

                # City check: if service_areas specified, must match
                if service_areas and city not in service_areas:
                    # Also check ZIP codes (service_areas might be ZIPs)
                    continue

                # Trade check: if preferred_trades specified, should match
                trade_match = True
                if preferred_trades and trade.lower() not in [t.lower() for t in preferred_trades]:
                    trade_match = False

                # Score threshold check
                if lead_score < min_lead_score:
                    continue

                candidates.append({
                    "id": r["id"],
                    "username": r.get("username", ""),
                    "trade_match": trade_match,
                    "candidates": 0,  # Will be set by caller
                })

            conn.close()

            if not candidates:
                return None

            # Pick best: prefer trade match, then lowest current_month_leads
            candidates.sort(key=lambda x: (0 if x["trade_match"] else 1))
            best = candidates[0]
            best["candidates"] = len(candidates)
            return best

        except Exception as e:
            logger.debug(f"[Router/GC/SQLite] {e}")
            return None

    def _find_best_gc_pg(self, city: str, trade: str, lead_score: int) -> Optional[dict]:
        """Find best GC using PostgreSQL."""
        try:
            from db_postgres import get_conn, put_conn
            conn = get_conn()
            with conn.cursor() as cur:
                # Find GCs with matching service area and trade
                cur.execute("""
                    SELECT u.id, u.username, u.email, u.full_name,
                           u.plan_tier, u.monthly_lead_limit,
                           u.current_month_leads, u.profile_data,
                           u.notification_prefs
                    FROM users u
                    WHERE u.role = 'gc'
                      AND u.is_active = TRUE
                      AND (u.expires_at IS NULL OR u.expires_at > NOW())
                      AND u.current_month_leads < u.monthly_lead_limit
                    ORDER BY
                        CASE u.plan_tier
                            WHEN 'premium' THEN 3
                            WHEN 'pro' THEN 2
                            WHEN 'free' THEN 1
                            ELSE 0
                        END DESC,
                        u.current_month_leads ASC
                    LIMIT 5
                """)

                candidates = []
                for row in cur.fetchall():
                    r = dict(row)
                    profile = r.get("profile_data") or {}

                    service_areas = profile.get("service_areas", [])
                    preferred_trades = profile.get("preferred_trades", [])
                    min_lead_score = profile.get("min_lead_score", 0)

                    if service_areas and city not in service_areas:
                        continue

                    trade_match = True
                    if preferred_trades and trade.lower() not in [t.lower() for t in preferred_trades]:
                        trade_match = False

                    if lead_score < min_lead_score:
                        continue

                    candidates.append({
                        "id": r["id"],
                        "username": r.get("username", ""),
                        "trade_match": trade_match,
                        "candidates": 0,
                    })

            put_conn(conn)

            if not candidates:
                return None

            candidates.sort(key=lambda x: (0 if x["trade_match"] else 1))
            best = candidates[0]
            best["candidates"] = len(candidates)
            return best

        except Exception as e:
            logger.debug(f"[Router/GC/PG] {e}")
            return None

    def _find_best_sub(self, city: str, trade: str, lead_score: int) -> Optional[dict]:
        """Find the best Sub for a lead."""
        use_postgres = os.getenv("USE_POSTGRES", "").lower() in ("1", "true")

        if use_postgres:
            return self._find_best_sub_pg(city, trade, lead_score)
        else:
            return self._find_best_sub_sqlite(city, trade, lead_score)

    def _find_best_sub_sqlite(self, city: str, trade: str, lead_score: int) -> Optional[dict]:
        """Find best sub using SQLite."""
        try:
            import sqlite3
            conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            c.execute("""
                SELECT u.id, u.username, u.email, u.full_name, u.plan_tier,
                       u.monthly_lead_limit, u.current_month_leads, u.profile_data
                FROM users u
                WHERE u.role = 'subcontractor'
                  AND u.is_active = 1
                  AND (u.expires_at IS NULL OR u.expires_at > datetime('now'))
                  AND u.current_month_leads < u.monthly_lead_limit
                ORDER BY u.current_month_leads ASC
                LIMIT 10
            """)

            candidates = []
            for row in c.fetchall():
                r = dict(row)
                profile = {}
                try:
                    profile = json.loads(r.get("profile_data") or "{}")
                except:
                    pass

                specialties = [s.upper() for s in profile.get("specialties", [])]
                service_areas = profile.get("service_areas", [])
                disaster_certified = profile.get("disaster_certified", False)

                # Must have the right specialty
                if specialties and trade.upper() not in specialties:
                    continue

                # Must serve the area
                if service_areas and city not in service_areas:
                    continue

                # Disaster-certified subs get priority for disaster leads
                candidates.append({
                    "id": r["id"],
                    "username": r.get("username", ""),
                    "disaster_certified": disaster_certified,
                    "specialty_match": trade.upper() in specialties if specialties else False,
                    "candidates": 0,
                })

            conn.close()

            if not candidates:
                return None

            # Prefer disaster-certified, then specialty match
            candidates.sort(key=lambda x: (
                0 if x["disaster_certified"] else 1,
                0 if x["specialty_match"] else 1,
            ))
            best = candidates[0]
            best["candidates"] = len(candidates)
            return best

        except Exception as e:
            logger.debug(f"[Router/Sub/SQLite] {e}")
            return None

    def _find_best_sub_pg(self, city: str, trade: str, lead_score: int) -> Optional[dict]:
        """Find best sub using PostgreSQL."""
        try:
            from db_postgres import get_conn, put_conn
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT u.id, u.username, u.email, u.full_name,
                           u.plan_tier, u.profile_data
                    FROM users u
                    WHERE u.role = 'subcontractor'
                      AND u.is_active = TRUE
                      AND (u.expires_at IS NULL OR u.expires_at > NOW())
                      AND u.current_month_leads < u.monthly_lead_limit
                    ORDER BY u.current_month_leads ASC
                    LIMIT 10
                """)

                candidates = []
                for row in cur.fetchall():
                    r = dict(row)
                    profile = r.get("profile_data") or {}

                    specialties = [s.upper() for s in profile.get("specialties", [])]
                    service_areas = profile.get("service_areas", [])
                    disaster_certified = profile.get("disaster_certified", False)

                    if specialties and trade.upper() not in specialties:
                        continue
                    if service_areas and city not in service_areas:
                        continue

                    candidates.append({
                        "id": r["id"],
                        "username": r.get("username", ""),
                        "disaster_certified": disaster_certified,
                        "specialty_match": trade.upper() in specialties if specialties else False,
                        "candidates": 0,
                    })

            put_conn(conn)

            if not candidates:
                return None

            candidates.sort(key=lambda x: (
                0 if x["disaster_certified"] else 1,
                0 if x["specialty_match"] else 1,
            ))
            best = candidates[0]
            best["candidates"] = len(candidates)
            return best

        except Exception as e:
            logger.debug(f"[Router/Sub/PG] {e}")
            return None

    def assign_lead(self, lead_id: str, gc_id: int = None, sub_id: int = None,
                    scores: dict = None):
        """Persist the assignment in the database."""
        use_postgres = os.getenv("USE_POSTGRES", "").lower() in ("1", "true")

        if use_postgres:
            self._assign_pg(lead_id, gc_id, sub_id, scores)
        else:
            self._assign_sqlite(lead_id, gc_id, sub_id, scores)

        # Increment lead count for assigned users
        if gc_id:
            self._increment_lead_count(gc_id)
        if sub_id:
            self._increment_lead_count(sub_id)

    def _assign_sqlite(self, lead_id: str, gc_id: int, sub_id: int, scores: dict):
        """Persist assignment in SQLite."""
        try:
            import sqlite3
            conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            c = conn.cursor()

            updates = []
            params = []
            if gc_id:
                updates.append("assigned_to_gc = ?")
                params.append(gc_id)
            if sub_id:
                updates.append("assigned_to_sub = ?")
                params.append(sub_id)
            if scores:
                if scores.get("subcontractor_score") is not None:
                    updates.append("subcontractor_score = ?")
                    params.append(scores["subcontractor_score"])
                if scores.get("gc_score") is not None:
                    updates.append("gc_score = ?")
                    params.append(scores["gc_score"])
                if scores.get("insurance_score") is not None:
                    updates.append("insurance_score = ?")
                    params.append(scores["insurance_score"])

            if not updates:
                conn.close()
                return

            updates.append("assigned_at = ?")
            params.append(datetime.utcnow().isoformat())
            params.append(lead_id)

            c.execute(f"""
                UPDATE consolidated_leads
                SET {', '.join(updates)}
                WHERE address_key = ?
            """, params)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[Router/Assign/SQLite] {e}")

    def _assign_pg(self, lead_id: str, gc_id: int, sub_id: int, scores: dict):
        """Persist assignment in PostgreSQL."""
        try:
            from db_postgres import get_conn, put_conn
            conn = get_conn()
            with conn.cursor() as cur:
                updates = []
                params = []
                if gc_id:
                    updates.append("assigned_to_gc = %s")
                    params.append(gc_id)
                if sub_id:
                    updates.append("assigned_to_sub = %s")
                    params.append(sub_id)
                if scores:
                    if scores.get("subcontractor_score") is not None:
                        updates.append("subcontractor_score = %s")
                        params.append(scores["subcontractor_score"])
                    if scores.get("gc_score") is not None:
                        updates.append("gc_score = %s")
                        params.append(scores["gc_score"])
                    if scores.get("insurance_score") is not None:
                        updates.append("insurance_score = %s")
                        params.append(scores["insurance_score"])

                if not updates:
                    put_conn(conn)
                    return

                updates.append("assigned_at = NOW()")
                params.append(lead_id)

                cur.execute(f"""
                    UPDATE consolidated_leads
                    SET {', '.join(updates)}
                    WHERE address_key = %s
                """, params)

            conn.commit()
            put_conn(conn)
        except Exception as e:
            logger.warning(f"[Router/Assign/PG] {e}")

    def _increment_lead_count(self, user_id: int):
        """Increment the monthly lead count for a user."""
        use_postgres = os.getenv("USE_POSTGRES", "").lower() in ("1", "true")

        try:
            if use_postgres:
                from db_postgres import get_conn, put_conn
                conn = get_conn()
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET current_month_leads = current_month_leads + 1 WHERE id = %s",
                        (user_id,)
                    )
                conn.commit()
                put_conn(conn)
            else:
                import sqlite3
                conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
                conn.execute(
                    "UPDATE users SET current_month_leads = current_month_leads + 1 WHERE id = ?",
                    (user_id,)
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.debug(f"[Router/Increment] {e}")


# Singleton
_router: Optional[LeadRouter] = None


def get_lead_router() -> LeadRouter:
    global _router
    if _router is None:
        _router = LeadRouter()
    return _router
