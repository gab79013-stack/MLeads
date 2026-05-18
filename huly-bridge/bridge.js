#!/usr/bin/env node
/**
 * mleads-huly-bridge.js
 * Bridge REST API between MLeads (Python) and Huly CRM
 * 
 * Uses Huly's transactor WebSocket protocol directly.
 * MLeads calls this bridge via simple HTTP POST.
 * 
 * Endpoints:
 *   POST /api/push-lead       — Push a lead from MLeads to Huly
 *   GET  /api/test            — Test Huly connection
 *   GET  /api/health          — Health check
 */

const express = require('express');
const WebSocket = require('ws');
const app = express();
app.use(express.json({ limit: '1mb' }));

const HULY_URL = process.env.HULY_URL || 'http://localhost:8080';
const HULY_TOKEN = process.env.HULY_TOKEN || '';
const HULY_WORKSPACE = process.env.HULY_WORKSPACE || '';
const BRIDGE_PORT = parseInt(process.env.BRIDGE_PORT || '5010');

// Convert HTTP URL to WS URL for transactor
const WS_URL = HULY_URL.replace(/^http/, 'ws') + '/_transactor';

let ws = null;
let requestId = 0;
let pendingRequests = {};

// ── WebSocket Connection to Huly Transactor ────────────────────────

function connectWS() {
  return new Promise((resolve, reject) => {
    if (!HULY_TOKEN || !HULY_WORKSPACE) {
      reject(new Error('HULY_TOKEN and HULY_WORKSPACE required'));
      return;
    }

    console.log(`[Bridge] Connecting to Huly transactor at ${WS_URL}...`);
    
    ws = new WebSocket(WS_URL, {
      headers: {
        'Authorization': `Bearer ${HULY_TOKEN}`,
      },
    });

    ws.on('open', () => {
      console.log('[Bridge] Connected to Huly transactor');
      // Send workspace join
      sendRequest('workspace.join', { workspace: HULY_WORKSPACE })
        .then(() => {
          console.log('[Bridge] Joined workspace');
          resolve();
        })
        .catch(reject);
    });

    ws.on('message', (data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.id && pendingRequests[msg.id]) {
          pendingRequests[msg.id](msg);
          delete pendingRequests[msg.id];
        }
      } catch (e) {
        console.error('[Bridge] WS parse error:', e.message);
      }
    });

    ws.on('error', (err) => {
      console.error('[Bridge] WS error:', err.message);
      reject(err);
    });

    ws.on('close', () => {
      console.log('[Bridge] WS disconnected');
      ws = null;
      // Reconnect after 5s
      setTimeout(() => {
        connectWS().catch(() => {});
      }, 5000);
    });
  });
}

function sendRequest(method, params) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      reject(new Error('WebSocket not connected'));
      return;
    }

    const id = String(++requestId);
    const msg = { id, method, params };

    const timeout = setTimeout(() => {
      delete pendingRequests[id];
      reject(new Error('Request timeout'));
    }, 15000);

    pendingRequests[id] = (response) => {
      clearTimeout(timeout);
      if (response.error) {
        reject(new Error(response.error.message || JSON.stringify(response.error)));
      } else {
        resolve(response.result);
      }
    };

    ws.send(JSON.stringify(msg));
  });
}

// ── Lead Push Logic ────────────────────────────────────────────────────

async function pushLeadToHuly(lead, scores) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    try {
      await connectWS();
    } catch (e) {
      return { status: 'skipped', reason: `Huly not connected: ${e.message}` };
    }
  }

  try {
    const contactId = `mleads_c_${Date.now()}`;
    const trackerId = `mleads_d_${Date.now()}`;

    const name = lead.contractor || lead.owner || 'Unknown Contractor';
    const nameParts = name.split(' ');

    // Create contact
    const contactOps = {
      _id: contactId,
      _class: 'contact:Person',
      firstName: nameParts[0] || 'Unknown',
      lastName: nameParts.slice(1).join(' ') || '',
      company: name !== 'Unknown Contractor' ? name : '',
      city: lead.city || '',
      state: 'CA',
      channels: [],
    };

    if (lead.contact_email) {
      contactOps.channels.push({ channel: 'email', value: lead.contact_email });
    }
    if (lead.contact_phone) {
      contactOps.channels.push({ channel: 'phone', value: lead.contact_phone });
    }

    // Create tracker (deal)
    const gcScore = scores?.gc_score || 0;
    const subScore = scores?.subcontractor_score || 0;
    const insScore = scores?.insurance_score || 0;
    const maxScore = Math.max(gcScore, subScore, insScore);

    const trackerOps = {
      _id: trackerId,
      _class: 'tracker:Issue',
      title: `🏗️ ${lead.address || 'Lead'} — ${lead.city || ''}`,
      description: buildDealDescription(lead, scores),
      priority: maxScore >= 90 ? 'urgent' : maxScore >= 70 ? 'high' : 'medium',
      assignee: contactId,
    };

    // Send model operations to transactor
    const result = await sendRequest('model.update', {
      operations: [
        {
          _id: contactId,
          _class: 'contact:Person',
          _op: 'create',
          ...contactOps,
        },
        {
          _id: trackerId,
          _class: 'tracker:Issue',
          _op: 'create',
          ...trackerOps,
        },
      ],
    });

    return {
      status: 'pushed',
      huly_contact_id: contactId,
      huly_deal_id: trackerId,
      result,
    };
  } catch (err) {
    console.error(`[Bridge] Push error: ${err.message}`);
    return { status: 'error', error: err.message };
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

// ── REST Endpoints ─────────────────────────────────────────────────────

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    huly_connected: ws !== null && ws.readyState === WebSocket.OPEN,
    huly_url: HULY_URL,
    workspace: HULY_WORKSPACE ? HULY_WORKSPACE.slice(0, 8) + '...' : 'not set',
  });
});

app.get('/api/test', async (req, res) => {
  try {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      await connectWS();
    }
    res.json({
      connected: ws !== null && ws.readyState === WebSocket.OPEN,
      url: HULY_URL,
      workspace: HULY_WORKSPACE ? HULY_WORKSPACE.slice(0, 8) + '...' : '',
    });
  } catch (e) {
    res.json({ connected: false, error: e.message });
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
    connectWS().catch((e) => {
      console.log(`[Bridge] Initial connection failed: ${e.message}`);
      console.log('[Bridge] Will retry on first push-lead request');
    });
  } else {
    console.log('[Bridge] Huly not configured. Set HULY_TOKEN and HULY_WORKSPACE.');
  }
});
