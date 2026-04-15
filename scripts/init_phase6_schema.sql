-- Phase 6: City data and lead enrichment schema
-- Adds cities reference table, enriches leads with location/property fields

-- ── Cities reference table ──
CREATE TABLE IF NOT EXISTS cities (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  country TEXT NOT NULL DEFAULT 'US',
  jurisdiction TEXT,
  population INTEGER,
  avg_home_value REAL,
  active INTEGER NOT NULL DEFAULT 1,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cities_name ON cities(name);
CREATE INDEX IF NOT EXISTS idx_cities_state ON cities(state);
CREATE INDEX IF NOT EXISTS idx_cities_active ON cities(active);

-- ── Enrich leads table with location and property fields ──
ALTER TABLE leads ADD COLUMN city TEXT;
ALTER TABLE leads ADD COLUMN city_id TEXT REFERENCES cities(id);
ALTER TABLE leads ADD COLUMN state TEXT;
ALTER TABLE leads ADD COLUMN zip TEXT;
ALTER TABLE leads ADD COLUMN country TEXT DEFAULT 'US';
ALTER TABLE leads ADD COLUMN address TEXT;
ALTER TABLE leads ADD COLUMN estimated_value REAL;
ALTER TABLE leads ADD COLUMN service_type TEXT;
ALTER TABLE leads ADD COLUMN agent_id TEXT;
ALTER TABLE leads ADD COLUMN first_seen TEXT;
ALTER TABLE leads ADD COLUMN next_inspection TEXT;

CREATE INDEX IF NOT EXISTS idx_leads_city ON leads(city_id);
CREATE INDEX IF NOT EXISTS idx_leads_state ON leads(state);
CREATE INDEX IF NOT EXISTS idx_leads_service_type ON leads(service_type);

-- ── Bot users table (Telegram bot subscribers) ──
CREATE TABLE IF NOT EXISTS bot_users (
  id TEXT PRIMARY KEY,
  telegram_id TEXT UNIQUE,
  name TEXT NOT NULL,
  username TEXT,
  chat_id TEXT NOT NULL,
  city TEXT,
  city_id TEXT REFERENCES cities(id),
  services TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'trial' CHECK(status IN ('trial', 'active', 'expired', 'cancelled')),
  trial_ends_at TEXT,
  paid_until TEXT,
  leads_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bot_users_status ON bot_users(status);
CREATE INDEX IF NOT EXISTS idx_bot_users_city ON bot_users(city_id);

-- ── Inspections table ──
CREATE TABLE IF NOT EXISTS inspections (
  id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  jurisdiction TEXT NOT NULL,
  inspection_type TEXT NOT NULL,
  inspection_date TEXT NOT NULL,
  gc_probability REAL NOT NULL DEFAULT 0,
  lead_id TEXT REFERENCES leads(id),
  status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'completed', 'cancelled', 'rescheduled')),
  notes TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inspections_date ON inspections(inspection_date);
CREATE INDEX IF NOT EXISTS idx_inspections_jurisdiction ON inspections(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_inspections_lead ON inspections(lead_id);

-- ── Export history table ──
CREATE TABLE IF NOT EXISTS export_history (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_users(id),
  format TEXT NOT NULL DEFAULT 'csv',
  filters TEXT NOT NULL DEFAULT '{}',
  row_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'completed' CHECK(status IN ('pending', 'completed', 'failed')),
  file_path TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_export_history_user ON export_history(user_id);
CREATE INDEX IF NOT EXISTS idx_export_history_created ON export_history(created_at DESC);

-- ── Feedback table ──
CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_users(id),
  type TEXT NOT NULL DEFAULT 'general' CHECK(type IN ('general', 'bug', 'feature', 'rating')),
  rating INTEGER,
  message TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback(type);

-- ── Seed cities data ──
INSERT OR IGNORE INTO cities (id, name, state, country, jurisdiction, population, avg_home_value, active) VALUES
  ('city_berkeley', 'Berkeley', 'CA', 'US', 'berkeley', 124321, 1250000.00, 1),
  ('city_oakland', 'Oakland', 'CA', 'US', 'oakland', 440646, 850000.00, 1),
  ('city_san_francisco', 'San Francisco', 'CA', 'US', 'san_francisco', 873965, 1400000.00, 1),
  ('city_san_jose', 'San Jose', 'CA', 'US', 'san_jose', 1013240, 1200000.00, 1),
  ('city_los_angeles', 'Los Angeles', 'CA', 'US', 'los_angeles', 3898747, 900000.00, 1),
  ('city_san_diego', 'San Diego', 'CA', 'US', 'san_diego', 1386932, 800000.00, 1),
  ('city_sacramento', 'Sacramento', 'CA', 'US', 'sacramento', 524943, 500000.00, 1),
  ('city_fremont', 'Fremont', 'CA', 'US', 'fremont', 230504, 1100000.00, 1),
  ('city_hayward', 'Hayward', 'CA', 'US', 'hayward', 162954, 750000.00, 1),
  ('city_richmond', 'Richmond', 'CA', 'US', 'richmond', 110567, 600000.00, 1),
  ('city_alameda', 'Alameda', 'CA', 'US', 'alameda', 79177, 950000.00, 1),
  ('city_palo_alto', 'Palo Alto', 'CA', 'US', 'palo_alto', 68572, 2800000.00, 1),
  ('city_santa_clara', 'Santa Clara', 'CA', 'US', 'santa_clara', 127134, 1300000.00, 1),
  ('city_sunnyvale', 'Sunnyvale', 'CA', 'US', 'sunnyvale', 155805, 1600000.00, 1),
  ('city_mountain_view', 'Mountain View', 'CA', 'US', 'mountain_view', 82376, 1900000.00, 1),
  ('city_emeryville', 'Emeryville', 'CA', 'US', 'emeryville', 12905, 700000.00, 1),
  ('city_dublin', 'Dublin', 'CA', 'US', 'dublin', 72595, 1100000.00, 1),
  ('city_pleasanton', 'Pleasanton', 'CA', 'US', 'pleasanton', 79871, 1200000.00, 1),
  ('city_livermore', 'Livermore', 'CA', 'US', 'livermore', 90189, 850000.00, 1),
  ('city_concord', 'Concord', 'CA', 'US', 'concord', 125922, 650000.00, 1);

-- ── Update existing leads with city data ──
UPDATE leads SET
  city = 'Berkeley',
  city_id = 'city_berkeley',
  state = 'CA',
  zip = '94704',
  address = '2100 Milvia St',
  estimated_value = 1250000.00,
  service_type = 'roofing',
  agent_id = 'agent-a',
  first_seen = '2026-04-10T08:00:00Z',
  next_inspection = '2026-04-20T10:00:00Z'
WHERE id = 'lead_phase4_001';

UPDATE leads SET
  city = 'Oakland',
  city_id = 'city_oakland',
  state = 'CA',
  zip = '94612',
  address = '1500 Broadway',
  estimated_value = 850000.00,
  service_type = 'electrical',
  agent_id = 'agent-b',
  first_seen = '2026-04-12T14:30:00Z',
  next_inspection = '2026-04-25T14:00:00Z'
WHERE id = 'lead_phase4_002';

-- ── Seed bot users ──
INSERT OR IGNORE INTO bot_users (id, telegram_id, name, username, chat_id, city, city_id, services, status, trial_ends_at, leads_count) VALUES
  ('bot_user_001', '123456789', 'Carlos Mendez', '@carlosm', '123456789', 'Berkeley', 'city_berkeley', '["roofing", "electrical"]', 'active', NULL, 15),
  ('bot_user_002', '987654321', 'Maria Garcia', '@mariag', '987654321', 'Oakland', 'city_oakland', '["plumbing"]', 'trial', '2026-05-01T00:00:00Z', 3),
  ('bot_user_003', '555123456', 'John Smith', '@johns', '555123456', 'San Francisco', 'city_san_francisco', '["roofing", "plumbing", "electrical"]', 'active', NULL, 42),
  ('bot_user_004', '777888999', 'Ana Lopez', '@anal', '777888999', 'San Jose', 'city_san_jose', '["hvac"]', 'expired', '2026-03-15T00:00:00Z', 8);

-- ── Seed inspections ──
INSERT OR IGNORE INTO inspections (id, address, jurisdiction, inspection_type, inspection_date, gc_probability, lead_id, status) VALUES
  ('insp_001', '2100 Milvia St', 'berkeley', 'Roof - Final', '2026-04-20T10:00:00Z', 0.85, 'lead_phase4_001', 'scheduled'),
  ('insp_002', '1500 Broadway', 'oakland', 'Electrical - Rough', '2026-04-25T14:00:00Z', 0.72, 'lead_phase4_002', 'scheduled'),
  ('insp_003', '3500 Shattuck Ave', 'berkeley', 'Plumbing - Final', '2026-04-18T09:00:00Z', 0.90, NULL, 'scheduled'),
  ('insp_004', '800 Harrison St', 'oakland', 'HVAC - Rough', '2026-04-22T11:00:00Z', 0.65, NULL, 'scheduled'),
  ('insp_005', '1200 University Ave', 'berkeley', 'Foundation - Final', '2026-04-28T15:00:00Z', 0.78, NULL, 'scheduled'),
  ('insp_006', '500 Market St', 'san_francisco', 'Roof - Rough', '2026-05-01T10:00:00Z', 0.82, NULL, 'scheduled'),
  ('insp_007', '2000 Embarcadero', 'oakland', 'Electrical - Final', '2026-05-05T13:00:00Z', 0.70, NULL, 'scheduled'),
  ('insp_008', '4500 Telegraph Ave', 'emeryville', 'Plumbing - Rough', '2026-05-08T09:30:00Z', 0.68, NULL, 'scheduled');
