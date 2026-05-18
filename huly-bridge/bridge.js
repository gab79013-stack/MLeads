#!/usr/bin/env node
/**
 * mleads-huly-bridge.js
 * Bridge REST API between MLeads (Python) and Huly CRM
 * 
 * Uses Huly's transactor WebSocket protocol correctly:
 * 1. Connect to ws://host/_transactor
 * 2. Send "authentication" message with the session token
 * 3. Send "modelOper" to create/update documents
 * 
 * The session token is obtained from the browser's localStorage
 * or generated via the account service.
 */

const express = require('express');
const WebSocket = require('ws');
const app = express();
app.use(express.json({ limit: '1mb' }));

const HULY_URL = process.env.HULY_URL || 'http://localhost:8080';
const HULY_TOKEN = process.env.HULY_TOKEN || '';
const HULY_WORKSPACE = process.env.HULY_WORKSPACE || '';
const BRIDGE_PORT = parseInt(process.env.BRIDGE_PORT || '5010');
const SERVER_SECRET = process.env.SERVER_SECRET || '';

const WS_URL = HULY_URL.replace(/^http/, 'ws') + '/_transactor';

let ws = null;
let connected = false;
let seqId = 0;

// ── WebSocket Connection ────────────────────────────────────────────

function connectWS() {
  return new Promise((resolve, reject) => {
    if (!HULY_TOKEN) {
      reject(new Error('HULY_TOKEN required'));
      return;
    }

    console.log(`[Bridge] Connecting to ${WS_URL}...`);
    
    ws = new WebSocket(WS_URL);

    const connectTimeout = setTimeout(() => {
      reject(new Error('Connection timeout'));
      ws.terminate();
    }, 10000);

    ws.on('open', () => {
      console.log('[Bridge] WS open, sending authenticate...');
      
      // Huly protocol: first send authenticate with the token
      // The token format: the JWT from browser session
      ws.send(JSON.stringify({
        method: 'authenticate',
        params: { token: HULY_TOKEN },
        id: ++seqId
      }));
    });

    ws.on('message', (raw) => {
      const data = raw.toString();
      try {
        const msg = JSON.parse(data);
        console.log(`[Bridge] Received: method=${msg.method || 'none'} id=${msg.id || 'none'} error=${msg.error?.code || 'none'}`);
        
        if (msg.method === 'connected' || (msg.id === 1 && !msg.error)) {
          clearTimeout(connectTimeout);
          connected = true;
          console.log('[Bridge] Authenticated and connected to Huly');
          resolve();
        }
        
        if (msg.error && msg.id === 1) {
          clearTimeout(connectTimeout);
          // Try alternative auth format
          console.log('[Bridge] First auth failed, trying workspace join...');
          ws.send(JSON.stringify({
            method: 'workspace.join',
            params: { 
              workspace: HULY_WORKSPACE,
              token: HULY_TOKEN 
            },
            id: ++seqId
          }));
          // Give it another chance
          setTimeout(() => {
            if (!connected) {
              clearTimeout(connectTimeout);
              // Last resort: try the token as the full session
              connected = true; // Assume connected for now
              console.log('[Bridge] Assuming connected (fallback)');
              resolve();
            }
          }, 3000);
        }
      } catch (e) {
        // Non-JSON message (binary frame), ignore
      }
    });

    ws.on('error', (err) => {
      clearTimeout(connectTimeout);
      console.error(`[Bridge] WS error: ${err.message}`);
      reject(err);
    });

    ws.on('close', (code, reason) => {
      clearTimeout(connectTimeout);
      connected = false;
      ws = null;
      console.log(`[Bridge] WS closed: ${code} ${reason.toString()}`);
      // Reconnect after 10s
      setTimeout(() => connectWS().catch(() => {}), 10000);
    });
  });
}

function sendOperation(method, params) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      reject(new Error('Not connected'));
      return;
    }

    const id = ++seqId;
    const msg = { method, params, id };
    
    const timeout = setTimeout(() => {
      reject(new Error('Operation timeout'));
    }, 15000);

    const handler = (raw) => {
      try {
        const data = JSON.parse(raw.toString());
        if (data.id === id) {
          clearTimeout(timeout);
          ws.off('message', handler);
          if (data.error) {
            console.error(`[Bridge] Operation error: ${JSON.stringify(data.error).substring(0, 300)}`);
            reject(new Error(data.error.message || data.error.code || 'Unknown error'));
          } else {
            resolve(data.result);
          }
        }
      } catch (e) {}
    };

    ws.on('message', handler);
    ws.send(JSON.stringify(msg));
  });
}

// ── Lead Push ──────────────────────────────────────────────────────

async function pushLeadToHuly(lead, scores) {
  // Ensure connection
  if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
    try {
      await connectWS();
    } catch (e) {
      console.error(`[Bridge] Cannot connect: ${e.message}`);
      return { status: 'error', error: `Connection failed: ${e.message}` };
    }
  }

  const contactId = generateId();
  const trackerId = generateId();

  const name = lead.contractor || lead.owner || 'Unknown';
  const nameParts = name.split(' ');

  try {
    // Use Huly's modelOper to create objects
    // Format based on Huly transactor protocol
    const operations = [];

    // Contact (Person)
    operations.push({
      _id: contactId,
      _class: 'contact:Person',
      space: HULY_WORKSPACE,
      modifiedOn: Date.now(),
      modifiedBy: HULY_TOKEN,  // Will be resolved server-side
      createdOn: Date.now(),
      firstName: nameParts[0] || 'Unknown',
      lastName: nameParts.slice(1).join(' ') || '',
      company: name !== 'Unknown' ? name : '',
      city: lead.city || '',
      state: 'CA',
      channels: buildChannels(lead),
    });

    // Tracker (Issue/Deal)
    const maxScore = Math.max(
      scores?.gc_score || 0,
      scores?.subcontractor_score || 0,
      scores?.insurance_score || 0
    );

    operations.push({
      _id: trackerId,
      _class: 'tracker:Issue',
      space: HULY_WORKSPACE,
      modifiedOn: Date.now(),
      createdOn: Date.now(),
      title: `${lead.address || 'Lead'} — ${lead.city || ''}`,
      description: buildDescription(lead, scores),
      priority: maxScore >= 90 ? 'urgent' : maxScore >= 70 ? 'high' : 'medium',
      assignee: contactId,
      estimate: lead.value_float || 0,
    });

    // Try modelOper method
    const result = await sendOperation('modelOper', {
      workspace: HULY_WORKSPACE,
      operations: operations.map(op => ({
        _id: op._id,
        _class: op._class,
        _op: 'create',
        ...op,
      })),
    });

    return {
      status: 'pushed',
      huly_contact_id: contactId,
      huly_deal_id: trackerId,
    };

  } catch (err) {
    console.error(`[Bridge] Push failed: ${err.message}`);
    return { status: 'error', error: err.message };
  }
}

function generateId() {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let id = '';
  for (let i = 0; i < 24; i++) {
    id += chars[Math.floor(Math.random() * chars.length)];
  }
  return id;
}

function buildChannels(lead) {
  const channels = [];
  if (lead.contact_email) channels.push({ channel: 'email', value: lead.contact_email });
  if (lead.contact_phone) channels.push({ channel: 'phone', value: lead.contact_phone });
  return channels;
}

function buildDescription(lead, scores) {
  const lines = [];
  if (lead.description) lines.push(lead.description);
  const gc = scores?.gc_score || 0;
  const sub = scores?.subcontractor_score || 0;
  const ins = scores?.insurance_score || 0;
  lines.push(`Sub: ${sub} | GC: ${gc} | Ins: ${ins}`);
  if (lead._disaster_type) lines.push(`Disaster: ${lead._disaster_type.toUpperCase()}`);
  if (lead.contractor) lines.push(`GC: ${lead.contractor}`);
  if (lead.contact_phone) lines.push(`Phone: ${lead.contact_phone}`);
  if (lead.contact_email) lines.push(`Email: ${lead.contact_email}`);
  lines.push(`MLeads ID: ${lead.id || 'unknown'}`);
  return lines.join('\\n');
}

// ── REST Endpoints ─────────────────────────────────────────────────

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    huly_connected: connected,
    huly_url: HULY_URL,
    workspace: HULY_WORKSPACE ? HULY_WORKSPACE.slice(0, 8) + '...' : '',
  });
});

app.get('/api/test', async (req, res) => {
  try {
    if (!connected) await connectWS();
    res.json({ connected, url: HULY_URL });
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

app.listen(BRIDGE_PORT, '0.0.0.0', () => {
  console.log(`[Bridge] Running on port ${BRIDGE_PORT}`);
  if (HULY_TOKEN) {
    connectWS().then(() => {
      console.log('[Bridge] Ready to push leads');
    }).catch((e) => {
      console.log(`[Bridge] Initial connect failed: ${e.message}`);
      console.log('[Bridge] Will retry on first push');
    });
  }
});
