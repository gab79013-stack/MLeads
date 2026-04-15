# Lead Qualifier

## Purpose
Califica leads entrantes, detecta duplicados y propone el siguiente paso del funnel.

## Inputs
```json
{
  "lead": {
    "email": "required|string",
    "first_name": "string",
    "last_name": "string",
    "company": "string",
    "source": "string",
    "notes": "string"
  }
}
```

## Outputs
```json
{
  "score": 0,
  "status": "new",
  "next_action": "string",
  "duplicate_of": null,
  "confidence": 0.0,
  "reasoning": "string"
}
```

## Runtime Contract
- Reads current lead state from `leads`
- Reads previous context from `lead_interactions` and `lead_scoring_history`
- Writes the latest score back to `leads`
- Registers duplicate candidates in `dedup_pool`

## Decision Rules
1. Reject leads without email.
2. Prefer corporate domains over personal inboxes.
3. Increase score when the source is referral, inbound, or event.
4. Lower confidence when data completeness is low.
5. Flag the lead when the normalized email already exists.

## Failure Modes
- Missing email or malformed payload
- DB unavailable or locked
- External model timeout

## Limits
- Single lead per invocation
- Max model latency target: 3s
- Must not create duplicate lead records

## Example Command
```bash
kortix agent run lead-qualifier --input '{"lead":{"email":"demo@company.com","company":"Company Inc"}}'
```
