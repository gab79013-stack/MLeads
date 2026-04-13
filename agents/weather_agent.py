"""
agents/weather_agent.py
━━━━━━━━━━━━━━━━━━━━━━━
🌧️ Pronóstico de Lluvia — Bay Area (Open-Meteo)

Detecta tormentas próximas en las 9 ciudades principales del Bay Area.
Lluvia intensa = oportunidad inmediata para roofing, gutters, waterproofing
y reparaciones de drywall por infiltración de agua.

API: Open-Meteo (https://open-meteo.com)
  - Completamente GRATUITA, sin API key
  - Datos: precipitación diaria + código de clima 7 días
  - Actualización: cada hora
  - Límite: sin límite documentado (uso razonable)

Lógica:
  - Alerta cuando precipitación > 15mm en próximas 48h (lluvia significativa)
  - Alerta cuando precipitación > 50mm en próximas 48h (tormenta fuerte)
  - Evita re-alertar la misma ciudad/fecha (dedup por ciudad+fecha)
"""

import os
import logging
import requests
from datetime import datetime, timezone
from agents.base import BaseAgent
from utils.telegram import send_lead
from utils.lead_scoring import score_lead, format_score_line

logger = logging.getLogger(__name__)

# Umbral de precipitación para generar lead (mm en 48h)
RAIN_THRESHOLD_MM    = float(os.getenv("WEATHER_RAIN_THRESHOLD_MM", "15"))
STORM_THRESHOLD_MM   = float(os.getenv("WEATHER_STORM_THRESHOLD_MM", "50"))

# Ciudades Bay Area con coordenadas
_BAY_AREA_CITIES = [
    {"city": "San Francisco",  "lat": 37.7749, "lon": -122.4194, "county": "San Francisco"},
    {"city": "Oakland",        "lat": 37.8044, "lon": -122.2712, "county": "Alameda"},
    {"city": "San Jose",       "lat": 37.3382, "lon": -121.8863, "county": "Santa Clara"},
    {"city": "Fremont",        "lat": 37.5485, "lon": -121.9886, "county": "Alameda"},
    {"city": "Berkeley",       "lat": 37.8716, "lon": -122.2727, "county": "Alameda"},
    {"city": "San Mateo",      "lat": 37.5630, "lon": -122.3255, "county": "San Mateo"},
    {"city": "Walnut Creek",   "lat": 37.9101, "lon": -122.0652, "county": "Contra Costa"},
    {"city": "Santa Rosa",     "lat": 38.4405, "lon": -122.7144, "county": "Sonoma"},
    {"city": "Napa",           "lat": 38.2975, "lon": -122.2869, "county": "Napa"},
]

# Códigos de clima de Open-Meteo que representan lluvia/tormenta
# https://open-meteo.com/en/docs#weathervariables
_RAIN_CODES = {
    51: "Llovizna ligera",
    53: "Llovizna moderada",
    55: "Llovizna intensa",
    61: "Lluvia ligera",
    63: "Lluvia moderada",
    65: "Lluvia intensa",
    71: "Nieve ligera",
    73: "Nieve moderada",
    75: "Nieve intensa",
    80: "Chubascos ligeros",
    81: "Chubascos moderados",
    82: "Chubascos violentos",
    95: "Tormenta eléctrica",
    96: "Tormenta con granizo ligero",
    99: "Tormenta con granizo intenso",
}


class WeatherAgent(BaseAgent):
    name      = "🌧️ Pronóstico Lluvia — Bay Area"
    emoji     = "🌧️"
    agent_key = "weather"

    def fetch_leads(self) -> list:
        leads = []
        for location in _BAY_AREA_CITIES:
            try:
                result = self._fetch_forecast(location)
                if result:
                    leads.append(result)
            except Exception as e:
                logger.debug(f"[Weather/{location['city']}] {e}")

        logger.info(f"[Weather] {len(leads)} alertas de lluvia en {len(_BAY_AREA_CITIES)} ciudades")
        return leads

    def _fetch_forecast(self, location: dict) -> dict | None:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":   location["lat"],
                "longitude":  location["lon"],
                "daily":      "precipitation_sum,weathercode,precipitation_hours",
                "forecast_days": 7,
                "timezone":   "America/Los_Angeles",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        daily = data.get("daily", {})
        dates      = daily.get("time", [])
        precip     = daily.get("precipitation_sum", [])
        codes      = daily.get("weathercode", [])
        hours      = daily.get("precipitation_hours", [])

        if not dates:
            return None

        # Suma de precipitación en las próximas 48h (índices 0 y 1)
        rain_48h = sum(float(p or 0) for p in precip[:2])

        if rain_48h < RAIN_THRESHOLD_MM:
            return None

        # Determinar severidad
        is_storm = rain_48h >= STORM_THRESHOLD_MM
        severity = "TORMENTA" if is_storm else "LLUVIA"
        emoji_sev = "⛈️" if is_storm else "🌧️"

        # Mejor día con más lluvia en próximas 48h
        peak_idx = 0 if float(precip[0] or 0) >= float(precip[1] or 0) else 1
        peak_date = dates[peak_idx] if peak_idx < len(dates) else dates[0]
        peak_mm   = float(precip[peak_idx] or 0)
        peak_code = int(codes[peak_idx] or 0)
        weather_desc = _RAIN_CODES.get(peak_code, "Precipitación")

        lead_id = f"weather_{location['city'].lower().replace(' ', '_')}_{peak_date}"

        score_data = score_lead(
            project_value=0,
            source_type="weather",
            days_ago=0,
            service_type="ROOFING",
        )

        # Boost por severidad de tormenta
        score = score_data.get("score", 50)
        if is_storm:
            score = min(100, score + 20)
        elif rain_48h >= 30:
            score = min(100, score + 10)

        return {
            "id":          lead_id,
            "city":        location["city"],
            "county":      location["county"],
            "address":     f"{location['city']}, CA",
            "description": (
                f"{emoji_sev} {severity} — {peak_mm:.0f}mm "
                f"({weather_desc}) en próximas 48h"
            ),
            "rain_48h_mm":   rain_48h,
            "peak_date":     peak_date,
            "peak_mm":       peak_mm,
            "weather_code":  peak_code,
            "weather_desc":  weather_desc,
            "rain_hours_day1": float(hours[0] or 0) if hours else 0,
            "is_storm":      is_storm,
            "severity":      severity,
            "forecast_7d":   list(zip(dates, precip, codes)),
            "_scoring":      {**score_data, "score": score},
            "_trade":        "ROOFING",
            "_agent_key":    "weather",
        }

    def notify(self, lead: dict):
        city     = lead.get("city", "")
        rain_mm  = lead.get("rain_48h_mm", 0)
        peak_dt  = lead.get("peak_date", "")
        is_storm = lead.get("is_storm", False)
        desc     = lead.get("description", "")

        score_line = format_score_line(lead.get("_scoring", {}))
        emoji_title = "⛈️" if is_storm else "🌧️"
        urgency = "URGENTE — Tormenta intensa" if is_storm else "Lluvia significativa"

        # Resumen forecast 7 días
        forecast_lines = []
        for date, mm, code in (lead.get("forecast_7d") or [])[:5]:
            mm_val = float(mm or 0)
            if mm_val > 0:
                code_desc = _RAIN_CODES.get(int(code or 0), "lluvia")
                forecast_lines.append(f"  {date}: {mm_val:.0f}mm — {code_desc}")
        forecast_str = "\n".join(forecast_lines) if forecast_lines else "Sin datos"

        send_lead(
            agent_name=self.name,
            emoji=self.emoji,
            title=f"{emoji_title} {urgency} — {city}",
            fields={
                "📍 Ciudad":      city,
                "🌧️ Lluvia 48h":  f"{rain_mm:.0f}mm",
                "📅 Pico":        peak_dt,
                "⚠️ Tipo":        desc,
                score_line:       "",
                "📊 Pronóstico":  f"\n{forecast_str}",
            },
            url=f"https://open-meteo.com/en/docs",
            cta=(
                "⚡ Lluvia próxima = leads urgentes de roofing, gutters y waterproofing. "
                "Contacta propietarios en zonas bajas y edificios con techo plano."
            ),
        )
