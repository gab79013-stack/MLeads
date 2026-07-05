# Post-Sale Remodel Radar Design

## Goal

Add a new 0brix lead channel that detects recently sold properties with strong remodel, flip, investor, or GC-intent signals. The product should not sell "recently sold homes" as raw data. It should sell early remodel intent: properties where the new owner is likely to need a General Contractor, design-build firm, roofing contractor, HVAC contractor, electrical contractor, plumbing contractor, or renovation partner.

## Product Name

**Post-Sale Remodel Radar**

Alternative labels for UI and sales material:

- Recently Sold Remodel Signals
- Investor Remodel Opportunities
- Flip Intent Leads
- Post-Sale GC Opportunities

Recommended public label: **Post-Sale Remodel Radar**.

## Positioning

0brix already finds demand from permits, weather, homeowner planning forms, and GC-opportunity signals. Post-Sale Remodel Radar adds a new demand moment: the period immediately after a property changes hands, before the new owner has finished selecting vendors.

Sales language:

> 0brix detects buyers who just purchased properties likely to need renovation. We cross-check sale records, buyer profile, property age, distress language, cash/LLC signals, permit activity, and contact enrichment to deliver remodel opportunities before the owner starts collecting bids.

## Lead Types

The channel should publish leads with `primary_service_type = "post_sale_remodel"` and a more specific subtype in `lead_data`:

- `post_sale_investor`: buyer appears to be an investor, LLC, trust, holding company, or acquisition entity.
- `post_sale_cash_buyer`: sale appears cash-funded or no mortgage is detected near the transfer.
- `post_sale_distress`: foreclosure, tax lien, probate, estate, short sale, REO, auction, or similar signal.
- `post_sale_old_home`: older property likely to need roof, electrical, plumbing, HVAC, kitchen, bath, or envelope upgrades.
- `post_sale_flip_candidate`: investor/cash/distress/discount signals suggest the property may be renovated for resale.
- `post_sale_permit_followup`: new permit activity appears shortly after sale, especially planning, architectural, structural, remodel, addition, ADU, roofing, HVAC, electrical, or plumbing.

## Target Buyers

Best buyers inside 0brix:

- General Contractors
- Design-build firms
- Remodelers
- ADU/addition contractors
- Roofing contractors
- HVAC contractors
- Electrical contractors
- Plumbing contractors
- Investor-friendly subcontractors

The best initial segment is GC and design-build because they can buy multi-trade remodel intent and then coordinate subs.

## Signals

### Buyer Profile Signals

- Buyer name contains: `LLC`, `L.L.C.`, `INC`, `CORP`, `HOLDINGS`, `INVESTMENTS`, `CAPITAL`, `PROPERTIES`, `VENTURES`, `TRUST`, `REVOCABLE TRUST`, `LAND TRUST`.
- Mailing address is different from property address.
- Mailing state is different from property state.
- Buyer has multiple recent purchases in the same market.
- Buyer appears to be an entity rather than an owner-occupant.

### Transaction Signals

- Sale date within last 7, 15, 30, or 60 days.
- Cash sale or no mortgage recorded near sale date.
- Sale type indicates foreclosure, REO, tax deed, probate, estate, sheriff sale, auction, short sale, or distress.
- Sale price shows meaningful discount compared with assessed value, prior sale, or nearby comparable homes.
- Price band is high enough to support paid contractor acquisition. Default floor: `$150,000`, configurable by market.

### Property Signals

- Year built before 1980.
- Long ownership period before resale.
- Vacant property flag where available.
- Low improvement quality, low condition rating, or deferred maintenance code where available.
- Square footage and lot size suggest addition, ADU, expansion, or whole-home remodel potential.

### Listing Text Signals

Use listing descriptions only as enrichment when lawful and available. Prefer public/partner feeds over fragile scraping. High-signal words:

- `as-is`
- `TLC`
- `contractor special`
- `handyman special`
- `needs updating`
- `outdated`
- `gut job`
- `down to the studs`
- `investor special`
- `fix and flip`
- `cash only`
- `bring your contractor`
- `rehab`
- `needs work`
- `deferred maintenance`

### Permit Follow-Up Signals

Look for permits after sale:

- Architectural drawings
- Structural engineering
- Residential remodel
- Addition
- ADU
- Garage conversion
- Kitchen remodel
- Bathroom remodel
- Roofing
- HVAC
- Electrical panel
- Plumbing
- Demolition

Highest-value case:

> Property sold recently, buyer appears investor/cash/LLC, and a permit or planning record appears after sale, but no GC is clearly confirmed.

## Scoring

Add a `post_sale_remodel_score` from 0 to 100.

Suggested scoring:

- Recent sale within 15 days: `+15`
- Recent sale within 30 days: `+10`
- Buyer is LLC/entity/trust: `+20`
- Cash sale or no mortgage detected: `+20`
- Mailing address out of state: `+12`
- Property built before 1980: `+12`
- Distress sale: `+18`
- Listing text has direct remodel keywords: `+18`
- Post-sale permit activity: `+25`
- No confirmed GC on related permit: `+10`
- Sale price above market-specific floor: `+8`
- Phone/contact available: `+10`
- Verified public source URL: `+8`

Caps:

- Max score: `100`
- HOT threshold: `>= 80`
- Elite candidate threshold: `>= 90` plus verified source, contact, fresh sale, and at least two major intent signals.

## Elite Qualification

A Post-Sale Remodel Radar lead can become Elite when all are true:

- Fresh transaction or post-sale permit signal.
- Buyer profile or distress signal is verified.
- Phone or direct contact path exists.
- Property value supports contractor acquisition.
- Source URL is auditable.
- Lead has a reason why the buyer likely needs a GC now.

Example Elite certificate bullets:

- Recently sold property.
- Buyer appears to be investor/entity.
- Cash or no-mortgage signal detected.
- Built before 1980.
- Post-sale permit or remodel keyword detected.
- Contact available.
- Source verified.

## Swipe UI

Add filters:

- Recently sold
- Investor buyer
- Cash buyer
- Old home
- Likely flip
- Distress sale
- Post-sale permit
- No GC detected
- Cross-data verified

Card copy example:

```text
HOT 91 · Post-Sale Remodel Radar

LLC purchased a 1958 home 9 days ago.

Why this matters:
- Buyer appears to be an investor
- Cash/no-mortgage signal
- Older property likely needs systems or remodel work
- Mailing address is out of state
- High chance of contractor need before resale

Best buyer:
GC / design-build / roofing / HVAC / electrical
```

## Data Model

Use existing `consolidated_leads` where possible.

Recommended `lead_data` fields:

```json
{
  "_channel": "post_sale_remodel",
  "_subtype": "post_sale_investor",
  "_post_sale_remodel_score": 91,
  "_sale_date": "2026-06-20",
  "_sale_price": 425000,
  "_buyer_name": "Example Holdings LLC",
  "_buyer_entity_type": "llc",
  "_buyer_mailing_state": "CA",
  "_property_state": "FL",
  "_cash_sale_signal": true,
  "_mortgage_detected": false,
  "_year_built": 1958,
  "_distress_signal": "",
  "_listing_keywords": ["as-is", "needs updating"],
  "_post_sale_permit_detected": true,
  "_confirmed_gc": false,
  "_source_url": "https://county.example.gov/record/123",
  "_source_label": "County recorder",
  "_best_buyer_roles": ["GENERAL_CONTRACTOR", "DESIGN_BUILD", "ROOFING", "HVAC"],
  "_ai_summary": "Investor LLC purchased an older property in cash; post-sale remodel intent is likely."
}
```

Recommended `consolidated_leads` mapping:

- `address_key`: stable property address or parcel transfer key.
- `address`: property address.
- `city`: city/market.
- `state`: property state.
- `primary_service_type`: `post_sale_remodel`.
- `lead_data`: full structured payload.
- `first_seen`: ingestion timestamp.

## Source Strategy

Recommended source order:

1. County recorder / transfer records.
2. County assessor / parcel records.
3. Socrata or open data portals where available.
4. Permit data after sale.
5. Paid property data provider only for markets where public sources are poor.
6. Listing text enrichment only when lawful, stable, and compliant with source terms.

Avoid making Zillow/Redfin scraping the base of the product. Use listing descriptions as optional enrichment rather than the system of record.

## Enrichment Waterfall

Only pay for enrichment after the lead passes a score gate.

Recommended gates:

- Score `< 60`: store as low-priority inventory; do not enrich.
- Score `60-79`: free/public enrichment only.
- Score `80-89`: attempt low-cost phone/entity enrichment.
- Score `>= 90`: allow paid skip tracing if market has buyer demand.

## Monetization

Suggested packaging:

- Basic lead: `$10-$25`
  - Address, buyer type, sale signal, source.
- Quality lead: `$30-$75`
  - Adds phone/contact path, stronger score, and reason to call.
- Elite lead: `$100-$250`
  - Fresh sale, investor/cash/distress or permit signal, contact, verified source, and high-value project reason.

Subscription packaging:

- Quality plan: limited access to post-sale opportunities and filters.
- Elite plan: exclusive or early access to high-score post-sale remodel leads.

## MVP Scope

First implementation should be narrow:

1. Pick one market with accessible transfer/assessor/permit records.
2. Ingest recent sales from the last 30 days.
3. Detect buyer entity keywords and property age.
4. Cross-check post-sale permits when possible.
5. Publish leads into `consolidated_leads`.
6. Add service category/filter label in Swipe.
7. Add scoring reasons and `Post-Sale Remodel Radar` card language.

Do not build paid enrichment or portal scraping in the first MVP.

## Risks

- Listing portals may restrict scraping. Mitigation: use public records and partner/public feeds first.
- County data varies by market. Mitigation: market-by-market adapters.
- Cash sale inference can be wrong if mortgage record lags. Mitigation: label as signal, not fact, and refresh after several days.
- LLC buyer does not always mean remodel intent. Mitigation: combine with age, distress, discount, post-sale permit, and contactability.
- Contact enrichment has compliance requirements. Mitigation: log source, suppression, and user terms before selling contact data.

## Success Metrics

- Number of post-sale leads found per market per week.
- Percent with at least two strong intent signals.
- Percent with contact path.
- Swipe like rate compared with existing permit/weather leads.
- Pipeline add rate.
- Paid lead replacement/refund rate.
- Contractor reply rate or booked estimate rate where tracked.

## Recommended Next Step

Implement a small `post_sale_remodel` MVP in one market and show it as a new Swipe filter. The first deliverable should prove that recent sale signals can become useful GC leads inside the existing 0brix flow before adding paid enrichment or broader market coverage.
