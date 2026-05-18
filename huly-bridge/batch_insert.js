#!/usr/bin/env node
/**
 * batch_insert.js — Fast batch insert of contractors into Huly
 * 
 * Reads contractor data from stdin (JSON) and batch inserts
 * into Huly's CockroachDB using multi-row INSERT.
 * 
 * Usage:
 *   python3 scripts/contacts_to_huly.py --json | node huly-bridge/batch_insert.js
 */

const { Client } = require('pg');
const crypto = require('crypto');

const DB_URL = process.env.HULY_DB_URL || 'postgresql://selfhost:cr_mleads2024@localhost:26257/defaultdb?sslmode=disable';
const HULY_WORKSPACE = process.env.HULY_WORKSPACE || '';

function generateId() {
  return crypto.randomBytes(12).toString('hex');
}

function computeHash(data) {
  return crypto.createHash('sha256').update(JSON.stringify(data)).digest('hex').substring(0, 20);
}

function getScoreColor(score) {
  if (score >= 90) return '#ff4444';
  if (score >= 70) return '#ff8800';
  if (score >= 50) return '#ffcc00';
  return '#4488ff';
}

async function batchInsert(contacts) {
  const db = new Client({ connectionString: DB_URL });
  await db.connect();
  console.error(`[Batch] Connected to DB. Inserting ${contacts.length} contacts...`);

  let inserted = 0;
  const BATCH_SIZE = 25;

  for (let i = 0; i < contacts.length; i += BATCH_SIZE) {
    const batch = contacts.slice(i, i + BATCH_SIZE);
    const now = Date.now();
    
    try {
      await db.query('BEGIN');
      
      for (const contact of batch) {
        const contactId = generateId();
        const trackerId = generateId();
        const subScore = contact.scores?.subcontractor_score || 50;
        const gcScore = contact.scores?.gc_score || 35;
        const insScore = contact.scores?.insurance_score || 30;
        const maxScore = Math.max(subScore, gcScore, insScore);

        // Insert contact
        const contactData = {
          avatarProps: { color: getScoreColor(maxScore) },
          avatarType: 'color',
          city: contact.city || 'Bay Area',
          name: contact.business_name || 'Unknown',
          socialIds: [],
        };
        if (contact.contact_email) {
          contactData.socialIds.push({ _id: generateId(), _class: 'contact:SocialId', value: contact.contact_email, type: 'email' });
        }
        if (contact.contact_phone) {
          contactData.socialIds.push({ _id: generateId(), _class: 'contact:SocialId', value: contact.contact_phone, type: 'phone' });
        }

        await db.query(`
          INSERT INTO contact ("workspaceId", _id, _class, space, "modifiedBy", "createdBy", "modifiedOn", "createdOn", "%hash%", "attachedTo", data)
          VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NULL, $10)
        `, [HULY_WORKSPACE, contactId, 'contact:class:Person', 'contact:space:Contacts',
            'core:account:ConfigUser', 'core:account:ConfigUser', now, now,
            computeHash(contactData), JSON.stringify(contactData)]);

        // Insert tracker
        const trackerData = {
          title: `${contact.business_name || 'Contractor'} — ${contact.city || 'Bay Area'}`,
          description: `${contact.description || contact.trade || 'Contractor'}\n📊 Score: Sub=${subScore} GC=${gcScore} Ins=${insScore}`,
          status: maxScore >= 70 ? 'warm' : 'qualified',
          priority: maxScore >= 70 ? 'high' : 'medium',
          number: inserted + 1,
          assignee: contactId,
        };

        await db.query(`
          INSERT INTO tracker ("workspaceId", _id, _class, space, "modifiedBy", "createdBy", "modifiedOn", "createdOn", "%hash%", "attachedTo", data)
          VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NULL, $10)
        `, [HULY_WORKSPACE, trackerId, 'tracker:class:Issue', 'tracker:space:Issues',
            'core:account:ConfigUser', 'core:account:ConfigUser', now, now,
            computeHash(trackerData), JSON.stringify(trackerData)]);

        inserted++;
      }
      
      await db.query('COMMIT');
      console.error(`[Batch] ${inserted}/${contacts.length} inserted...`);
    } catch (err) {
      await db.query('ROLLBACK');
      console.error(`[Batch] Error at ${inserted}: ${err.message}`);
    }
  }

  await db.end();
  console.log(JSON.stringify({ status: 'done', inserted, total: contacts.length }));
}

// Read JSON from stdin
let inputData = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { inputData += chunk; });
process.stdin.on('end', () => {
  try {
    const contacts = JSON.parse(inputData);
    batchInsert(contacts);
  } catch (e) {
    console.error(`Error: ${e.message}`);
    process.exit(1);
  }
});
