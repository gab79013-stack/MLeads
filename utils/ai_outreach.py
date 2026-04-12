"""
utils/ai_outreach.py
━━━━━━━━━━━━━━━━━━━━
IA #4 — Generador de Outreach Personalizado con Claude

Por cada lead genera mensajes listos para enviar:
  - SMS corto (160 chars)
  - Email subject + body
  - Script para llamada de 30 segundos
  - DM para LinkedIn/Facebook

El sub-contractor recibe el mensaje YA redactado — solo copia y pega.
Personalizado por: trade, tipo de propiedad, zona, valor del proyecto.

Usa claude-haiku (rápido y barato).
Fallback: templates pre-escritos si no hay API key.
"""

import os
import logging
import hashlib

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_ENABLED        = os.getenv("AI_ENABLED", "true").lower() not in ("false", "0", "no")
MODEL             = os.getenv("AI_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")

_cache: dict[str, dict] = {}

_SYSTEM_OUTREACH = """You are an expert sales copywriter for construction subcontractors.
Generate SHORT, direct outreach messages for a subcontractor to contact a property owner or GC.

The messages should sound natural, not spammy. Reference specific details about the project.

Respond ONLY with valid JSON. No markdown, no explanation.

JSON schema:
{
  "sms": "<max 155 chars, casual tone>",
  "email_subject": "<max 50 chars>",
  "email_body": "<3-4 sentences, professional>",
  "call_script": "<30-second phone script, conversational>",
  "dm": "<social media DM, max 200 chars>"
}"""


# ── Templates fallback ────────────────────────────────────────────────

_TEMPLATES = {
    "ROOFING": {
        "sms": "Hi {owner_or_contractor}, saw your roofing project at {address}. We specialize in {city} roofing & can start quickly. Interested? {sub_name}",
        "email_subject": "Roofing subcontract opportunity — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the roofing permit at {address} and wanted to reach out. Our crew specializes in residential and commercial roofing in the {city} area.\n\nWe're available to start within the week and can provide a same-day estimate. Would you be open to a quick conversation?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? Great. I'm {sub_name}, I'm a local roofing contractor. I saw a permit was issued for your property at {address} and wanted to see if you're still looking for a roofing crew. We're available this week — is this a good time to chat?",
        "dm": "Hi! Saw the roofing project at {address}. Our crew is local to {city} and available this week. Can we talk? — {sub_name}",
    },
    "DEMOLITION": {
        "sms": "Hi {owner_or_contractor}, saw the demo permit at {address}. Licensed C-21 crew in {city}, ready to start. Free estimate? {sub_name}",
        "email_subject": "Demolition subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the demolition permit filed for {address} and wanted to connect. We're a licensed C-21 demolition contractor serving {city} — experienced in structural demo, selective interior demo, and hazmat abatement.\n\nWe carry full insurance and can mobilize quickly. Would you have time for a quick call to discuss scope?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a licensed demolition contractor in {city}. I saw the demo permit for {address} — are you still looking for a demo crew? We handle everything from selective interior demo to full structural. Can we talk scope?",
        "dm": "Hey! Noticed the demo permit at {address}. Licensed C-21 crew in {city}, available now. Quick call? — {sub_name}",
    },
    "PAINTING": {
        "sms": "Hi {owner_or_contractor}, saw the paint project at {address}. Licensed C-33 painters in {city}, interior & exterior. Free quote? {sub_name}",
        "email_subject": "Painting subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the permit activity at {address} and wanted to reach out about the painting scope. We're a licensed C-33 painting contractor in {city} specializing in interior and exterior residential and commercial work.\n\nWe provide clean, professional results and can start within the week. Would love to give you a quick estimate.\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a licensed painter in {city}. I saw the permit for {address} and figured there might be painting work coming up. We do interior and exterior — can I give you a quick quote?",
        "dm": "Hi! Noticed the project at {address}. We're licensed painters in {city}, available this week. Need a quote? — {sub_name}",
    },
    "ELECTRICAL": {
        "sms": "Hi {owner_or_contractor}, noticed electrical permit at {address}. Licensed electricians ready in {city}. Panel upgrades & EV chargers our specialty. {sub_name}",
        "email_subject": "Electrical subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI saw the electrical permit filed for {address} and wanted to connect. We're a licensed electrical contractor serving {city} specializing in panel upgrades, service upgrades, and EV charger installations.\n\nWe can often start within 48 hours and work with your project timeline. Would love to give you a quick quote.\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a licensed electrician in {city}. I saw the electrical permit for {address} — are you still looking for an electrical sub? We specialize in panel upgrades and can start quickly.",
        "dm": "Hey! Noticed the electrical project at {address}. We're licensed electricians in {city}, available this week. Quick call? — {sub_name}",
    },
    "HVAC": {
        "sms": "Hi {owner_or_contractor}, saw HVAC work at {address}. Licensed C-20 contractor in {city}, heating & cooling specialist. {sub_name}",
        "email_subject": "HVAC subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the HVAC permit at {address} and wanted to reach out. We're a licensed C-20 HVAC contractor in {city} — we handle installations, replacements, and ductwork for residential and commercial projects.\n\nWe're available to start soon and can provide a same-day estimate. Interested in connecting?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a licensed HVAC contractor in {city}. I saw the permit for {address} — do you still need an HVAC sub? We specialize in installations and can start this week.",
        "dm": "Hey! Noticed the HVAC project at {address}. Licensed C-20 in {city}, available now. Quick call? — {sub_name}",
    },
    "PLUMBING": {
        "sms": "Hi {owner_or_contractor}, saw plumbing permit at {address}. Licensed C-36 plumber in {city}, ready to help. {sub_name}",
        "email_subject": "Plumbing subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI saw the plumbing permit filed for {address} and wanted to connect. We're a licensed C-36 plumbing contractor serving {city} — experienced with new installations, re-pipes, and water heater replacements.\n\nWe can start quickly and work around your schedule. Would you like a free estimate?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a licensed plumber in {city}. I saw the permit for {address} — are you looking for a plumbing sub? We can start this week.",
        "dm": "Hey! Noticed the plumbing project at {address}. Licensed C-36 in {city}, available now. Need a quote? — {sub_name}",
    },
    "CONCRETE": {
        "sms": "Hi {owner_or_contractor}, saw concrete work at {address}. Licensed C-8 in {city} — slabs, driveways, foundations. {sub_name}",
        "email_subject": "Concrete subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the permit for {address} and wanted to reach out about the concrete scope. We're a licensed C-8 concrete contractor in {city} — we handle foundations, slabs, driveways, and flatwork.\n\nWe're available to start soon. Would you like a free estimate?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a licensed concrete contractor in {city}. I saw the permit for {address} — do you need concrete work? We do slabs, foundations, and flatwork. Can we talk?",
        "dm": "Hey! Noticed the project at {address}. Licensed concrete contractor in {city}, available now. Need a bid? — {sub_name}",
    },
    "DRYWALL": {
        "sms": "Hi {owner_or_contractor}, saw drywall work at {address}. Local crew in {city} — hang, tape, texture. Available now. {sub_name}",
        "email_subject": "Drywall subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the permit activity at {address} and wanted to reach out about drywall. We're a local drywall crew in {city} specializing in hang, tape, and texture for residential and commercial projects.\n\nWe can provide a same-day estimate and start within the week. Interested?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, I run a drywall crew in {city}. I saw the permit for {address} — are you looking for a drywall sub? We can start this week.",
        "dm": "Hey! Noticed the project at {address}. Local drywall crew in {city}, available this week. Need a quote? — {sub_name}",
    },
    "LANDSCAPING": {
        "sms": "Hi {owner_or_contractor}, saw landscaping at {address}. Local crew in {city} — hardscape, irrigation, design. {sub_name}",
        "email_subject": "Landscaping opportunity — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the permit activity at {address} and wanted to reach out about landscaping. We're a local landscaping company in {city} specializing in hardscape, irrigation, and design.\n\nWe'd love to give you a free estimate. Would you be open to a quick call?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a local landscaper in {city}. I saw the project at {address} — are you looking for landscaping work? We handle hardscape, irrigation, and full design.",
        "dm": "Hey! Noticed the project at {address}. Local landscaping crew in {city}, available now. Free estimate? — {sub_name}",
    },
    "INSULATION": {
        "sms": "Hi {owner_or_contractor}, saw insulation work at {address}. Licensed C-2 in {city} — spray foam, batts, blown-in. {sub_name}",
        "email_subject": "Insulation subcontract — {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the permit at {address} and wanted to connect about insulation. We're a licensed C-2 insulation contractor in {city} — spray foam, batts, and blown-in for new construction and retrofits.\n\nWe can provide a free energy assessment and estimate. Interested?\n\nBest, {sub_name}",
        "call_script": "Hi, is this {owner_or_contractor}? I'm {sub_name}, a licensed insulation contractor in {city}. I saw the permit for {address} — do you need insulation work? We do spray foam, batts, and blown-in.",
        "dm": "Hey! Noticed the project at {address}. Licensed insulation contractor in {city}. Free estimate? — {sub_name}",
    },
    "GENERAL": {
        "sms": "Hi {owner_or_contractor}, saw your project at {address} in {city}. We're local contractors available to help. Can we connect? {sub_name}",
        "email_subject": "Construction opportunity at {address}",
        "email_body": "Hi {owner_or_contractor},\n\nI noticed the permit activity at {address} and wanted to reach out. We're a local contractor in {city} and would love to discuss how we can support your project.\n\nPlease let me know if you'd like to chat about scope and timeline.\n\nBest, {sub_name}",
        "call_script": "Hi {owner_or_contractor}, I'm {sub_name}, a local contractor in {city}. I saw the permit for your project at {address} and wanted to see if you need any help with the work. Do you have a few minutes?",
        "dm": "Hi! Saw your project at {address}. We're local contractors in {city} and would love to connect. — {sub_name}",
    },
}


def _fill_template(template: str, lead: dict, sub_name: str = "Your Local Contractor") -> str:
    owner = (lead.get("owner") or lead.get("contractor") or lead.get("buyer") or "there")[:30]
    address = (lead.get("address") or "your property")[:50]
    city = (lead.get("city") or "your area").split("(")[0].strip()[:30]
    return template.format(
        owner_or_contractor=owner,
        address=address,
        city=city,
        sub_name=sub_name,
    )


def generate_outreach(lead: dict, sub_name: str = "Your Local Contractor") -> dict:
    """
    Genera mensajes de outreach personalizados para un lead.

    Args:
        lead:     dict del lead (con _trade, address, city, owner, etc.)
        sub_name: nombre del sub-contractor que contactará

    Returns:
        dict con sms, email_subject, email_body, call_script, dm
    """
    trade    = lead.get("_trade", "GENERAL")
    desc     = (lead.get("description") or lead.get("desc") or "")[:300]
    address  = lead.get("address", "")
    city     = lead.get("city", "")
    owner    = lead.get("owner", "")
    value    = lead.get("value_float", 0)
    ai_sum   = lead.get("_ai_summary", "")

    # Cache
    cache_key = hashlib.md5(f"{trade}{address}{desc[:100]}".encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    # Fallback si no hay API key
    if not ANTHROPIC_API_KEY or not AI_ENABLED:
        tpl = _TEMPLATES.get(trade, _TEMPLATES["GENERAL"])
        result = {k: _fill_template(v, lead, sub_name) for k, v in tpl.items()}
        result["_source"] = "template"
        _cache[cache_key] = result
        return result

    # ── Claude Haiku ─────────────────────────────────────────────
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        user_content = f"""Generate outreach messages for a {trade.lower()} subcontractor.

Project details:
- Address: {address}
- City: {city}
- Description: {desc or ai_sum}
- Project value: ${value:,.0f}
- Owner/GC: {owner or 'Unknown'}
- Subcontractor name: {sub_name}

Make messages specific to this {trade.lower()} project."""

        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_OUTREACH,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        import json
        raw = response.content[0].text.strip()
        result = json.loads(raw)
        result["_source"] = "claude"
        _cache[cache_key] = result
        return result

    except Exception as e:
        logger.warning(f"[AI Outreach] Claude falló ({e}), usando template")
        tpl = _TEMPLATES.get(trade, _TEMPLATES["GENERAL"])
        result = {k: _fill_template(v, lead, sub_name) for k, v in tpl.items()}
        result["_source"] = "template"
        _cache[cache_key] = result
        return result


def format_outreach_for_telegram(outreach: dict, lead: dict) -> str:
    """Formatea el outreach para envío por Telegram al sub-contractor."""
    trade = lead.get("_trade", "GENERAL")
    lines = [
        f"📤 *OUTREACH LISTO — {trade}*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "📱 *SMS (copiar y enviar):*",
        f"`{outreach.get('sms', '')}`",
        "",
        "📧 *Email:*",
        f"*Asunto:* {outreach.get('email_subject', '')}",
        f"_{outreach.get('email_body', '').replace(chr(10), ' ')}_",
        "",
        "📞 *Script llamada:*",
        f"_{outreach.get('call_script', '')}_",
    ]
    if outreach.get("_source") == "claude":
        lines.append("\n_✨ Generado por IA_")
    return "\n".join(lines)
