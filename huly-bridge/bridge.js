#!/usr/bin/env node
/**
 * mleads-huly-bridge.js
 * Bridge between MLeads and Huly CRM
 * 
 * Inserts leads directly into Huly's CockroachDB.
 * This is the most reliable approach since Huly's transactor
 * WebSocket protocol is undocumented and complex.
 * 
 * Huly stores all data in a document-based schema:
 *   - contact table: people/organizations
 *   - tracker table: issues/deals/tasks
 *   - All content in JSONB `data` column
 *   - Uses _id, _class, space, workspaceId as identifiers
 */

const express = require('express');
const { Client } = require('pg');
const app = express();
app.use(express.json({ limit: '1mb' }));

const DB_URL = process.env.HULY_DB_URL || 'postgresql://selfhost:cr_mleads2024@localhost:26257/defaultdb?sslmode=disable';
const HULY_WORKSPACE = process.env.HULY_WORKSPACE || '';
const BRIDGE_PORT = parseInt(process.env.BRIDGE_PORT || '5010');

let pgClient = null;

// ── Database Connection ────────────────────────────────────────────

async function connectDB() {
  pgClient = new Client({ connectionString: DB_URL });
  await pgClient.connect();
  console.log('[Bridge] Connected to Huly CockroachDB');
  return pgClient;
}

async function ensureConnected() {
  if (!pgClient) {
    await connectDB();
  }
  return pgClient;
}

// ── Lead Push ──────────────────────────────────────────────────────

async function pushLeadToHuly(lead, scores) {
  const db = await ensureConnected();

  const contactId = generateId();
  const trackerId = generateId();
  const now = Date.now();
  const space = 'contact:space:Contacts';
  const trackerSpace = 'tracker:space:Issues';

  const name = lead.contractor || lead.owner || 'Unknown';
  const gcScore = scores?.gc_score || 0;
  const subScore = scores?.subcontractor_score || 0;
  const insScore = scores?.insurance_score || 0;
  const maxScore = Math.max(gcScore, subScore, insScore);

  try {
    // 1. Insert contact
    const contactData = {
      avatarProps: { color: getScoreColor(maxScore) },
      avatarType: 'color',
      city: lead.city || '',
      name: name,
      socialIds: [],
    };

    if (lead.contact_email) {
      contactData.socialIds.push({
        _id: generateId(),
        _class: 'contact:SocialId',
        value: lead.contact_email,
        type: 'email',
      });
    }
    if (lead.contact_phone) {
      contactData.socialIds.push({
        _id: generateId(),
        _class: 'contact:SocialId',
        value: lead.contact_phone,
        type: 'phone',
      });
    }

    await db.query(`
      INSERT INTO contact (workspaceId, _id, _class, space, modifiedBy, createdBy, modifiedOn, createdOn, "data")
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    `, [
      HULY_WORKSPACE,
      contactId,
      'contact:class:Person',
      space,
      'core:account:ConfigUser',
      'core:account:ConfigUser',
      now,
      now,
      JSON.stringify(contactData),
    ]);

    // 2. Insert tracker (deal/issue)
    const trackerData = {
      title: `${lead.address || 'Lead'} — ${lead.city || ''}`,
      description: buildDescription(lead, scores),
      status: maxScore >= 90 ? 'hot' : maxScore >= 70 ? 'warm' : 'qualified',
      priority: maxScore >= 90 ? 'urgent' : maxScore >= 70 ? 'high' : 'medium',
      number: await getNextTrackerNumber(db),
      assignee: contactId,
      estimation: lead.value_float || 0,
    };

    await db.query(`
      INSERT INTO tracker (workspaceId, _id, _class, space, modifiedBy, createdBy, modifiedOn, createdOn, "data")
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    `, [
      HULY_WORKSPACE,
      trackerId,
      'tracker:class:Issue',
      trackerSpace,
      'core:account:ConfigUser',
      'core:account:ConfigUser',
      now,
      now,
      JSON.stringify(trackerData),
    ]);

    console.log(`[Bridge] Pushed lead: contact=${contactId} tracker=${trackerId}`);

    return {
      status: 'pushed',
      huly_contact_id: contactId,
      huly_deal_id: trackerId,
    };

  } catch (err) {
    console.error(`[Bridge] Push error: ${err.message}`);
    return { status: 'error', error: err.message };
  }
}

async function getNextTrackerNumber(db) {
  try {
    const res = await db.query(`SELECT COUNT(*) FROM tracker WHERE "workspaceId" = $1`, [HULY_WORKSPACE]);
    return parseInt(res.rows[0].count) + 1;
  } catch {
    return 1;
  }
}

function getScoreColor(score) {
  if (score >= 90) return '#ff4444';  // Red for hot
  if (score >= 70) return '#ff8800';  // Orange for warm
  if (score >= 50) return '#ffcc00';  // Yellow for qualified
  return '#4488ff';                   // Blue for new
}

function generateId() {
  const chars = '0123456789abcdef';
  let id = '';
  for (let i = 0; i < 24; i++) {
    id += chars[Math.floor(Math.random() * chars.length)];
  }
  return id;
}

function buildDescription(lead, scores) {
  const lines = [];
  if (lead.description) lines.push(lead.description);
  const gc = scores?.gc_score || 0;
  const sub = scores?.subcontractor_score || 0;
  const ins = scores?.insurance_score || 0;
  lines.push(`📊 Scoring: Sub=${sub} GC=${gc} Ins=${ins}`);
  if (lead.property_year_built || lead.property_roof_material) {
    lines.push(`🏠 Property: Year ${lead.property_year_built || '?'} | Roof: ${lead.property_roof_material || 'unknown'}`);
  }
  if (lead._disaster_type) lines.push(`🚨 Disaster: ${lead._disaster_type.toUpperCase()}`);
  if (lead.contractor) lines.push(`👷 Contractor: ${lead.contractor}`);
  if (lead.contact_phone) lines.push(`📞 ${lead.contact_phone}`);
  if (lead.contact_email) lines.push(`✉️ ${lead.contact_email}`);
  if (lead.agent_sources) lines.push(`📡 Source: ${lead.agent_sources}`);
  lines.push(`🆔 MLeads: ${lead.id || 'unknown'}`);
  return lines.join('\n');
}

// ── REST Endpoints ─────────────────────────────────────────────────

app.get('/api/health', async (req, res) => {
  try {
    const db = await ensureConnected();
    const r = await db.query('SELECT 1');
    res.json({ status: 'ok', db_connected: true, workspace: HULY_WORKSPACE ? HULY_WORKSPACE.slice(0, 8) + '...' : '' });
  } catch (e) {
    res.json({ status: 'ok', db_connected: false, error: e.message });
  }
});

app.get('/api/test', async (req, res) => {
  try {
    const db = await ensureConnected();
    const contacts = await db.query('SELECT COUNT(*) FROM contact WHERE "workspaceId" = $1', [HULY_WORKSPACE]);
    const trackers = await db.query('SELECT COUNT(*) FROM tracker WHERE "workspaceId" = $1', [HULY_WORKSPACE]);
    res.json({
      connected: true,
      contacts: parseInt(contacts.rows[0].count),
      trackers: parseInt(trackers.rows[0].count),
    });
  } catch (e) {
    res.json({ connected: false, error: e.message });
  }
});

app.post('/api/push-lead', async (req, res) => {
  const { lead, scores } = req.body;
  if (!lead) return res.status(400).json({ error: 'lead required' });
  const result = await pushLeadToHuly(lead, scores || lead._tripartite || {});
  res.json(result);
});

// ── Start ──────────────────────────────────────────────────────────

app.listen(BRIDGE_PORT, '0.0.0.0', async () => {
  console.log(`[Bridge] Running on port ${BRIDGE_PORT}`);
  try {
    await connectDB();
    console.log('[Bridge] Ready to push leads to Huly');
  } catch (e) {
    console.error(`[Bridge] DB connection failed: ${e.message}`);
  }
});
