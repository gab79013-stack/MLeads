#!/usr/bin/env node
/**
 * mleads-huly-bridge.js
 * Bridge REST API between MLeads (Python) and Huly CRM
 * 
 * MLeads calls this bridge via HTTP, and the bridge uses
 * the official Huly platform-api SDK to create contacts + deals.
 * 
 * Endpoints:
 *   POST /api/push-lead       — Push a lead from MLeads to Huly
 *   GET  /api/test            — Test Huly connection
 *   GET  /api/health          — Health check
 * 
 * Environment:
 *   HULY_URL       — Huly server URL (default: http://localhost:8080)
 *   HULY_TOKEN     — JWT token from Huly
 *   HULY_WORKSPACE — Workspace ID
 *   BRIDGE_PORT    — Port for this bridge (default: 5010)
 */

const express = require('express');
const app = express();
app.use(express.json({ limit: '1mb' }));

const HULY_URL = process.env.HULY_URL || 'http://localhost:8080';
const HULY_TOKEN = process.env.HULY_TOKEN || '';
const HULY_WORKSPACE = process.env.HULY_WORKSPACE || '';
const BRIDGE_PORT = parseInt(process.env.BRIDGE_PORT || '5010');

// ── Huly API Client ────────────────────────────────────────────────────

let hulyClient = null;
let connected = false;

async function connectHuly() {
  if (!HULY_TOKEN || !HULY_WORKSPACE) {
    console.log('[Bridge] Huly not configured — running in passthrough mode');
    return;
  }

  try {
    const { connect } = require('@hcengineering/platform-api');
    hulyClient = await connect(HULY_URL, HULY_TOKEN, HULY_WORKSPACE);
    connected = true;
    console.log(`[Bridge] Connected to Huly at ${HULY_URL}, workspace ${HULY_WORKSPACE.slice(0, 8)}...`);
  } catch (err) {
    console.error(`[Bridge] Failed to connect to Huly: ${err.message}`);
    console.log('[Bridge] Will retry on next request');
  }
}

async function ensureConnected() {
  if (!connected) {
    await connectHuly();
  }
  return connected && hulyClient;
}

// ── Lead Push Logic ────────────────────────────────────────────────────

async function pushLeadToHuly(lead, scores) {
  const client = await ensureConnected();
  if (!client) {
    return { status: 'skipped', reason: 'Huly not connected' };
  }

  try {
    const {
      contactId,
      personOps,
    } = await createContact(client, lead);

    const {
      trackerId,
      trackerOps,
    } = await createTracker(client, lead, scores, contactId);

    // Commit all operations
    await client.commit();

    return {
      status: 'pushed',
      huly_contact_id: contactId,
      huly_deal_id: trackerId,
    };
  } catch (err) {
    console.error(`[Bridge] Error pushing lead: ${err.message}`);
    return { status: 'error', error: err.message };
  }
}

async function createContact(client, lead) {
  const name = lead.contractor || lead.owner || 'Unknown Contractor';
  const nameParts = name.split(' ');
  const firstName = nameParts[0] || 'Unknown';
  const lastName = nameParts.slice(1).join(' ') || '';

  const email = lead.contact_email || '';
  const phone = lead.contact_phone || '';
  const city = lead.city || '';

  // Generate unique ID
  const contactId = `mleads_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

  // Use the Huly model API to create a Person
  const contactData = {
    _id: contactId,
    firstName,
    lastName,
    company: name !== 'Unknown Contractor' ? name : '',
    city,
    state: 'CA',
  };

  // Add channels (email/phone)
  const channels = [];
  if (email) channels.push({ channel: 'email', value: email });
  if (phone) channels.push({ channel: 'phone', value: phone });
  if (channels.length > 0) contactData.channels = channels;

  // Tags
  const tags = getLeadTags(lead);
  if (tags.length > 0) contactData.tags = tags;

  try {
    // Create via Huly's model operations
    const { contact } = client.getModel();
    await contact.create(contactData);
    return { contactId, personOps: contactData };
  } catch (err) {
    // Fallback: try direct operation
    console.log(`[Bridge] Contact creation via model failed, trying direct: ${err.message}`);
    try {
      await client.create('contact:Person', contactData);
      return { contactId, personOps: contactData };
    } catch (err2) {
      console.error(`[Bridge] Contact creation failed: ${err2.message}`);
      return { contactId: null, personOps: contactData };
    }
  }
}

async function createTracker(client, lead, scores, contactId) {
  const gcScore = scores?.gc_score || 0;
  const subScore = scores?.subcontractor_score || 0;
  const insScore = scores?.insurance_score || 0;
  const maxScore = Math.max(gcScore, subScore, insScore);

  const trackerId = `mleads_deal_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

  const title = `🏗️ ${lead.address || 'Lead'} — ${lead.city || ''}`;
  const description = buildDealDescription(lead, scores);

  const trackerData = {
    _id: trackerId,
    title,
    description,
    priority: maxScore >= 90 ? 'urgent' : maxScore >= 70 ? 'high' : 'medium',
    assignee: contactId,
  };

  const tags = getLeadTags(lead);
  if (tags.length > 0) trackerData.tags = tags;

  const value = lead.value_float || 0;
  if (value) trackerData.estimate = value;

  try {
    await client.create('tracker:Issue', trackerData);
    return { trackerId, trackerOps: trackerData };
  } catch (err) {
    console.error(`[Bridge] Tracker creation failed: ${err.message}`);
    return { trackerId: null, trackerOps: trackerData };
  }
}

function buildDealDescription(lead, scores) {
  const lines = [];

  if (lead.description) lines.push(lead.description);

  const gc = scores?.gc_score || 0;
  const sub = scores?.subcontractor_score || 0;
  const ins = scores?.insurance_score || 0;
  lines.push(`👷 Sub: ${sub} | 🏗️ GC: ${gc} | 🏢 Ins: ${ins}`);

  if (lead.property_year_built || lead.property_roof_material) {
    lines.push(`🏠 Property: Year ${lead.property_year_built || '?'} | Roof: ${lead.property_roof_material || 'unknown'}`);
  }

  if (lead._disaster_type) {
    lines.push(`🚨 Disaster: ${lead._disaster_type.toUpperCase()}`);
  }

  if (lead.contractor) lines.push(`👷 GC: ${lead.contractor}`);
  if (lead.contact_phone) lines.push(`📞 ${lead.contact_phone}`);
  if (lead.contact_email) lines.push(`✉️ ${lead.contact_email}`);

  if (lead.agent_sources || lead._agent_key) {
    lines.push(`📡 Source: ${lead.agent_sources || lead._agent_key}`);
  }

  lines.push(`🆔 MLeads ID: ${lead.id || 'unknown'}`);

  return lines.join('\n');
}

function getLeadTags(lead) {
  const tags = [];

  const trade = (lead._trade || '').toLowerCase();
  if (trade) tags.push(trade);

  const disaster = lead._disaster_type || '';
  if (disaster) tags.push(`disaster-${disaster}`);

  const scores = lead._tripartite || {};
  const maxScore = Math.max(
    scores.gc_score || 0,
    scores.subcontractor_score || 0,
    scores.insurance_score || 0
  );
  if (maxScore >= 90) tags.push('hot-lead');
  else if (maxScore >= 70) tags.push('warm-lead');

  const source = lead._agent_key || '';
  if (source) tags.push(`source-${source}`);

  const city = (lead.city || '').toLowerCase().replace(/\s+/g, '-');
  if (city) tags.push(city);

  return tags;
}

// ── REST Endpoints ─────────────────────────────────────────────────────

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    huly_connected: connected,
    huly_url: HULY_URL,
    workspace: HULY_WORKSPACE ? HULY_WORKSPACE.slice(0, 8) + '...' : 'not set',
  });
});

app.get('/api/test', async (req, res) => {
  const client = await ensureConnected();
  if (client) {
    res.json({ connected: true, url: HULY_URL, workspace: HULY_WORKSPACE.slice(0, 8) + '...' });
  } else {
    res.json({ connected: false, error: 'Could not connect to Huly' });
  }
});

app.post('/api/push-lead', async (req, res) => {
  const { lead, scores } = req.body;

  if (!lead) {
    return res.status(400).json({ error: 'lead is required' });
  }

  const result = await pushLeadToHuly(lead, scores || lead._tripartite || {});
  res.json(result);
});

// ── Start ──────────────────────────────────────────────────────────────

app.listen(BRIDGE_PORT, '0.0.0.0', () => {
  console.log(`[Bridge] MLeads-Huly bridge running on port ${BRIDGE_PORT}`);
  if (HULY_TOKEN && HULY_WORKSPACE) {
    connectHuly();
  } else {
    console.log('[Bridge] Huly not configured. Set HULY_TOKEN and HULY_WORKSPACE to enable.');
  }
});
