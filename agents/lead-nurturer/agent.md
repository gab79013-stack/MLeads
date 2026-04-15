# Lead Nurturer

## Purpose
Gestiona follow-ups de leads activos con mensajes adaptados al estado del funnel.

## Inputs
```json
{
  "lead_id": "required|string",
  "action": "follow_up|sequence|check_in|re_engage",
  "channel": "email|phone|meeting"
}
```

## Outputs
```json
{
  "lead_id": "string",
  "message": "string",
  "channel": "email",
  "requires_human_review": false,
  "next_touch_at": "ISO-8601"
}
```

## Runtime Contract
- Reads lead profile and score from `leads`
- Reads timeline from `lead_interactions`
- Writes generated contact events back to `lead_interactions`

## Sequencing Rules
1. `new`: day 1, 3, 7.
2. `contacted`: week 1, 2, 4.
3. `qualified`: send value proposition and demo CTA.
4. `proposal`: compress cadence and escalate to human owner.

## Failure Modes
- Lead does not exist
- Missing valid channel
- Duplicate message within the cooldown window

## Limits
- One active cadence per lead
- Human review required for proposals or compliance-sensitive content

## Example Command
```bash
kortix agent run lead-nurturer --input '{"lead_id":"lead_123","action":"sequence","channel":"email"}'
```
