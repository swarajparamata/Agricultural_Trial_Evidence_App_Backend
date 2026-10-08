-- =====================================================================================================
-- AgriEvidence API – database schema (SQLite)
-- =====================================================================================================
--
-- The source of truth is app/models.py (SQLAlchemy). The app creates these tables itself on startup
-- (Base.metadata.create_all), so you never need to run this file; it documents the schema and can
-- build an empty database by hand:
--
--     sqlite3 data/empty.db < docs/schema.sql
--
-- The definitions below are generated from the models for SQLite, the default database
-- (data/agritrial.db), and match what the app creates. With another database (DATABASE_URL), the
-- types map to that database's equivalents.
--
-- How the tables relate
-- ---------------------
--
--   source_files 1 ──< source_records ─┐  trial_id  ┌── trial_overrides
--   (the evidence)      (one trial as   ├────────────┤   (admin edits, conflict choices,
--                        one source     │            │    deletion markers)
--                        wrote it)      ▼            ▼
--                                      trials  (the reconciled view, rebuilt from the records,
--                                               the overrides and the current settings)
--
--   users          accounts and roles            app_settings   one JSON document per section
--   audit_events   who changed what, and when
--
-- source_records.trial_id and trial_overrides.trial_id refer to trials.id by value, not by foreign
-- key, on purpose: `trials` is derived data that is deleted and rebuilt, while records and overrides
-- must outlive it (a deleted trial keeps its "__excluded__" override so a re-import can't revive it).
--
-- Conventions
-- -----------
-- * JSON columns are stored as text; their shapes are described next to each column.
-- * DATETIME values are UTC. BOOLEAN is 0/1.
-- * Yields are stored in kg/ha (trials.*_kg_ha) and converted to the display unit by the API.
-- * Values in source_records are kept exactly as written ("7800", "HARVESTPLUS", "DE"); mapping
--   them to the vocabulary and units happens during reconciliation, driven by the settings.
-- =====================================================================================================


-- -----------------------------------------------------------------------------------------------------
-- users – people who can sign in. Passwords are argon2 hashes; roles decide what the API allows.
-- -----------------------------------------------------------------------------------------------------
CREATE TABLE users (
	id INTEGER NOT NULL,
	email VARCHAR(255) NOT NULL,              -- lower-case, unique (ix_users_email)
	full_name VARCHAR(255) NOT NULL,
	hashed_password VARCHAR(255) NOT NULL,    -- argon2 hash (pwdlib); never the password
	role VARCHAR(20) NOT NULL,                -- 'admin' | 'viewer'
	is_active BOOLEAN NOT NULL,               -- deactivated users' tokens stop working at once
	created_at DATETIME NOT NULL,
	last_login_at DATETIME,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_email ON users (email);


-- -----------------------------------------------------------------------------------------------------
-- source_files – every imported CSV or report. The original text is kept, so all files can be
-- re-parsed when the import rules, vocabulary or units change (POST /api/data/rebuild), and shown
-- in the app's source viewer (GET /api/sources/{filename}). Re-importing a file name replaces it.
-- -----------------------------------------------------------------------------------------------------
CREATE TABLE source_files (
	id INTEGER NOT NULL,
	filename VARCHAR(255) NOT NULL,           -- e.g. 'trials_a.csv', 'T03_summary.txt'
	kind VARCHAR(20) NOT NULL,                -- 'csv' | 'report'
	parser VARCHAR(30) NOT NULL,              -- 'csv' | 'report-rules' | 'report-llm' (LLM extraction loop)
	sha256 VARCHAR(64) NOT NULL,              -- hash of the content
	size_bytes INTEGER NOT NULL,
	content TEXT NOT NULL,                    -- the file as uploaded (decoded to text)
	record_count INTEGER NOT NULL,            -- rows in source_records from this file
	warnings JSON NOT NULL,                   -- ["Row 5 has no trial ID and was skipped.", …]
	uploaded_at DATETIME NOT NULL,
	uploaded_by VARCHAR(255) NOT NULL,        -- user email, or 'system' for the demo import
	PRIMARY KEY (id),
	UNIQUE (filename)
);


-- -----------------------------------------------------------------------------------------------------
-- source_records – one trial as reported by one source: a CSV row, a report, or a manual entry.
-- This is the raw evidence that reconciliation reads; nothing in it is ever normalized in place.
-- -----------------------------------------------------------------------------------------------------
CREATE TABLE source_records (
	id INTEGER NOT NULL,
	file_id INTEGER,                          -- NULL for a manual entry (no file)
	trial_id VARCHAR(64) NOT NULL,            -- e.g. 'T03' (cleaned: spaces removed, upper case)
	kind VARCHAR(20) NOT NULL,                -- 'csv' | 'report' | 'manual'
	source_name VARCHAR(255) NOT NULL,        -- file name, or 'Manual entry'
	locator VARCHAR(255) NOT NULL,            -- 'row 4', 'report', 'entered by admin@…'
	position INTEGER NOT NULL,                -- order within the file (row number)
	fields JSON NOT NULL,                     -- canonical field -> value as written:
	                                          --   {"crop": "wheat", "product": "HARVESTPLUS", "country": "DE",
	                                          --    "year": "2024", "treatment_yield": "7800", …}
	                                          -- custom fields use "custom:<key>", e.g. "custom:soil_type"
	units JSON NOT NULL,                      -- yield field -> unit found beside the value, in the header
	                                          -- or a unit column: {"treatment_yield": "kg/ha", …}
	raw JSON NOT NULL,                        -- the original row / parsed report; '_llm_extraction' holds
	                                          -- the LLM service's fields when that loop read the report
	notes TEXT NOT NULL,                      -- free-text notes; caveat rules read them sentence by sentence
	extraction VARCHAR(20) NOT NULL,          -- 'rules' | 'llm' | 'manual'
	created_at DATETIME NOT NULL,
	created_by VARCHAR(255) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(file_id) REFERENCES source_files (id) ON DELETE CASCADE
);
CREATE INDEX ix_source_records_file_id ON source_records (file_id);
CREATE INDEX ix_source_records_trial_id ON source_records (trial_id);


-- -----------------------------------------------------------------------------------------------------
-- trials – the reconciled record per trial: what the dashboard, comparison and assistant use.
-- Rebuilt from source_records + trial_overrides + the current settings whenever any of them change.
-- -----------------------------------------------------------------------------------------------------
CREATE TABLE trials (
	id VARCHAR(64) NOT NULL,                  -- trial ID, e.g. 'T03'
	crop VARCHAR(80),                         -- canonical names from the reference data
	product VARCHAR(80),                      --   ('Harvest Plus', 'Potato', 'Spain', …)
	country VARCHAR(80),
	year INTEGER,
	trial_type VARCHAR(80),                   -- e.g. 'Scientific', 'Demonstration'
	treatment_kg_ha FLOAT,                    -- treated yield in kg/ha (NULL when missing)
	control_kg_ha FLOAT,                      -- untreated control yield in kg/ha (NULL when missing)
	custom_fields JSON NOT NULL,              -- {"soil_type": "Loam", "application_rate": 1.5}
	status VARCHAR(20) NOT NULL,              -- 'consistent' | 'single_source' | 'incomplete' | 'conflict' | 'manual'
	caveats JSON NOT NULL,                    -- ["Replication not documented", "Reported by a single source", …]
	conflicts JSON NOT NULL,                  -- one entry per field the sources disagree on:
	                                          --   [{"field": "treatment_yield", "label": "Treated yield",
	                                          --     "status": "open" | "resolved",
	                                          --     "candidates": [{"value": 42000.0, "sources": ["trials_a.csv"]},
	                                          --                    {"value": 44000.0, "sources": ["T03_summary.txt"]}],
	                                          --     "chosen": 42000.0, "resolved_by": null, "note": ""}]
	missing_fields JSON NOT NULL,             -- ["control_yield"]
	uplift_range JSON,                        -- [lowest, highest] uplift % the conflicting yields allow,
	                                          -- e.g. [5.0, 10.0]; NULL without a yield conflict
	sources JSON NOT NULL,                    -- provenance shown in the app: [{"file", "kind", "locator",
	                                          --   "values": {field: value as written}, "extraction"}]
	notes JSON NOT NULL,                      -- [{"source": "T03_summary.txt", "text": "…"}]
	overridden_fields JSON NOT NULL,          -- fields whose value comes from trial_overrides
	origin VARCHAR(20) NOT NULL,              -- 'ingested' (from files) | 'manual' (added by an admin)
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);
-- Uplift is not stored: the API derives it from the two yields,
-- (treated - control) / control * 100, rounded to 0.1 %.


-- -----------------------------------------------------------------------------------------------------
-- trial_overrides – an admin's decision about one field of one trial. Applied on top of the sources
-- on every rebuild, so re-imports never undo it. Deleting an override reverts the field.
--   * an edit of an imported trial:        field = 'control_yield', value = 7900.0, note = 'Corrected …'
--   * a conflict resolution:               field = 'treatment_yield', value = 44000.0
--   * a deleted trial (restorable):        field = '__excluded__', value = true
-- -----------------------------------------------------------------------------------------------------
CREATE TABLE trial_overrides (
	trial_id VARCHAR(64) NOT NULL,
	field VARCHAR(80) NOT NULL,               -- canonical field, 'custom:<key>', or '__excluded__'
	value JSON NOT NULL,                      -- the chosen value (yields in kg/ha)
	note TEXT NOT NULL,                       -- the admin's reason
	updated_by VARCHAR(255) NOT NULL,         -- admin email
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (trial_id, field)
);


-- -----------------------------------------------------------------------------------------------------
-- app_settings – one row per settings section an admin has saved; a missing row means "use the
-- defaults" (app/schemas/settings.py), and resetting a section deletes its row. Keys: 'display', 'reference_data',
-- 'custom_fields', 'comparison', 'ingestion', 'reconciliation', 'assistant', 'access'.
-- Saving 'reference_data', 'custom_fields', 'ingestion' or 'reconciliation' triggers a rebuild.
-- -----------------------------------------------------------------------------------------------------
CREATE TABLE app_settings (
	"key" VARCHAR(64) NOT NULL,               -- section name
	value JSON NOT NULL,                      -- the whole section, e.g. {"fields": [{"key": "soil_type", …}]}
	updated_at DATETIME NOT NULL,
	updated_by VARCHAR(255) NOT NULL,
	PRIMARY KEY ("key")
);


-- -----------------------------------------------------------------------------------------------------
-- audit_events – the admin activity log (Settings → Activity, GET /api/audit).
-- -----------------------------------------------------------------------------------------------------
CREATE TABLE audit_events (
	id INTEGER NOT NULL,
	at DATETIME NOT NULL,                     -- indexed: the log is read newest first
	actor VARCHAR(255) NOT NULL,              -- admin email, or 'system'
	action VARCHAR(80) NOT NULL,              -- e.g. 'user.create', 'trial.update', 'data.import', 'demo.reset'
	target VARCHAR(255) NOT NULL,             -- e.g. a trial ID, an email or a file name
	detail TEXT NOT NULL,                     -- short human-readable summary
	PRIMARY KEY (id)
);
CREATE INDEX ix_audit_events_at ON audit_events (at);
