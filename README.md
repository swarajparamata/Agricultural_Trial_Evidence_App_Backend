# AgriEvidence API (backend)

FastAPI backend of AgriEvidence, the agricultural trial evidence platform. It:
- imports trial spreadsheets and free-text reports;
- maps spelling variants, country codes and units to one vocabulary;
- reconciles every trial across its sources, keeping each source's values and any conflicts visible;
- serves the React app (`Agricultural_Trial_Evidence_App_Frontend`).

The assistant runs in the separate LLM service (`Agricultural_Trial_Evidence_App_LLM`). This API sends it the reconciled trials and relays its answers.

> **Part of AgriEvidence.** To run the whole app with one command on macOS or Windows, clone [Agricultural_Trial_Evidence_App](https://github.com/swarajparamata/Agricultural_Trial_Evidence_App) with `git clone --recursive`. Its README also covers the model configuration and the demo data.

**Stack:** Python 3.10+ · FastAPI · SQLAlchemy 2 (SQLite by default) · Pydantic 2 · PyJWT · argon2 (pwdlib)

## Getting started

Requires Python 3.10 or newer.

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # optional: every setting has a working default
uvicorn app.main:app --reload --port 8000
```

```powershell
# Windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

- API: <http://127.0.0.1:8000/api>.
- Interactive docs: <http://127.0.0.1:8000/docs>. Use **Authorize** with a demo account.
- `GET /api/health` reports the database and LLM service status.

The first start does the following:
- creates `data/agritrial.db`;
- generates a signing key in `data/.secret_key` (unless `SECRET_KEY` is set);
- in demo mode, creates the demo accounts and imports the sample data from the workspace's `../sample-data` folder, or from the copy in `demo_data/` when this repo stands alone.

To start over, delete the `data/` folder.

Start the LLM service as well, so the assistant can answer (see its README). Without it, the rest of the app works and the assistant reports that the service is unreachable.

## Demo mode (presentations)

`DEMO_MODE=true` is the default.

| Account | Password | Role |
| --- | --- | --- |
| `admin@agrievidence.demo` | `admin123` | admin |
| `viewer@agrievidence.demo` | `viewer123` | viewer |

- The login page lists both accounts (from `GET /api/meta`); a click fills in the form.
- **Settings → Trials & data → Reset demo** (`POST /api/demo/reset`) returns the app to its first-run state, so every presentation starts clean:
  - deletes all trials, imports, edits and settings, and the activity log;
  - re-imports the sample data;
  - restores the example custom fields and the demo passwords and roles.

  Accounts you added stay; delete them under Users & access if needed.
- For real use, set `DEMO_MODE=false`, a long random `SECRET_KEY`, and `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD` for the first admin.

## Roles

| | Admin | Viewer |
| --- | --- | --- |
| Browse, filter, compare, open sources, ask the assistant | ✓ | ✓ |
| Own profile and password | ✓ | ✓ |
| Add users, change roles, deactivate, reset passwords | ✓ | – |
| Add, edit, delete and restore trials; resolve conflicts | ✓ | – |
| Import or remove files, rebuild, reset demo | ✓ | – |
| Change settings, view the activity log | ✓ | – |

Notes on accounts and access:
- Passwords are hashed with argon2. Login returns a JWT bearer token (HS256), valid for `ACCESS_TOKEN_EXPIRE_MINUTES`.
- Every request re-checks the user, so a deactivated account stops working at once.
- Admins can't demote, deactivate or delete themselves, and the last active admin is protected.
- Self sign-up (viewers only) is off by default. It's under Settings → Users & access.
- Admin actions are written to an audit log (`GET /api/audit`).

## From files to trials

1. **Parse.**
   - **CSV/TSV:** the delimiter is detected, and headers are matched to fields through configurable aliases. Spacing, case and units are ignored, so `Treated yield kg per ha` maps to the treated yield in kg/ha.
   - **Reports (.txt/.md):** `Label: value.` sentences, headings and notes are read.
   - **LLM fallback:** a report that misses required fields is sent to the LLM service's extraction loop, if *Import rules → Read difficult reports with the LLM* is on.
2. **Normalize.**
   - Vocabulary: names, aliases, plurals and codes, e.g. `HARVESTPLUS` → Harvest Plus, `potatoes` → Potato, `DE` → Germany.
   - Yields are converted to the display unit through kg/ha (`7800 kg/ha` → `7.8 t/ha`).
3. **Reconcile** each trial across its sources:
   - Values within `numeric_tolerance_pct` count as equal.
   - On a disagreement, the trial is marked `conflict`. Both values are kept, and the field is filled by the conflict strategy:
     - highest-priority source (manual → csv → report by default);
     - mean;
     - left open for an admin.
   - Uplift is `(treated − control) ÷ control × 100`, rounded to 0.1 %. A conflict also gives the **range** of uplift the sources allow.
   - The status is one of `consistent`, `single_source`, `incomplete` (a core value such as a yield is missing), `conflict` or `manual`.
   - Caveat rules turn sentences in the notes into caveats, e.g. "Replication details … are unavailable" → *Replication not documented*.
4. **Overrides.** Admin edits, conflict resolutions and deletions are stored apart from the source data, with the admin and the reason. A rebuild or re-import keeps them. Removing an override reverts the field to its source value.

The tables and their JSON columns are documented in [`docs/schema.sql`](docs/schema.sql). Source files keep their original text (`GET /api/sources/{filename}`), and every trial lists the values each source reported. Importing a file with the same name again replaces the earlier version. `dry_run` previews an import without saving it.

The sample data reconciles to:

| Trial | Product / crop | Yields (t/ha) | Uplift | Status | Sources |
| --- | --- | --- | --- | --- | --- |
| T01 | Harvest Plus / Wheat | 8.4 vs 8.0 | +5.0 % | consistent | trials_a.csv, trials_b.csv (8400 vs 8000 kg/ha) |
| T02 | Harvest Plus / Wheat | 7.8 vs 7.5 | +4.0 % | consistent | trials_a.csv (kg/ha, `HARVESTPLUS`, `DE`), T02_field_report.txt |
| T03 | Root Boost / Potato | 42 vs 40 | +5.0 % (range +5.0 to +10.0 %) | **conflict**: 42 t/ha in trials_a.csv vs 44 t/ha in T03_summary.txt | trials_a.csv, T03_summary.txt |
| T04 | Root Boost / Potato | 45 vs 45 | 0.0 % | consistent | trials_b.csv, T04_scientific_report.txt |
| T05 | Harvest Plus / Wheat | 8.1 vs – | – | incomplete (no control yield) | trials_b.csv |
| T06 | Harvest Plus / Wheat | 6.6 vs 6.0 | +10.0 % | single source | T06_archive_note.txt |

## Settings

All settings are stored in the database and edited on the app's Settings page. Each section has its own endpoint (`PUT /api/settings/{section}`, `POST /api/settings/{section}/reset`). Saving a section marked *rebuild* re-parses the stored files and reconciles every trial again, so the change applies to existing data.

| Section | What it configures | Rebuild |
| --- | --- | --- |
| `display` | App name, organization, display yield unit, decimals, table columns and their order | – |
| `reference_data` | Crops, products, countries (with codes), trial types (with badge colors), units (factor to kg/ha), strict vocabulary (reject unknown values in manual entries) | ✓ |
| `custom_fields` | Extra trial fields (text, long text, number, select, yes/no, date) with unit, options, required flag, where to show them (table, filters, compare), whether to chart them, and the CSV headers that hold them | ✓ |
| `comparison` | Maximum trials to compare, which charts to show, highlighting of differing rows, compared fields | – |
| `ingestion` | Header and label aliases per field, default yield unit, LLM extraction of unreadable reports | ✓ |
| `reconciliation` | Tolerance %, conflict strategy, source priority, caveat rules | ✓ |
| `assistant` | On/off, Ollama model and address, temperature, context window, thinking mode, tool-loop and reflection-loop limits, offline fallback, "always offline", conversation memory, extra instructions, suggested questions | – |
| `access` | Self sign-up | – |

## The assistant

`POST /api/assistant/chat/stream` builds the dataset (reconciled trials, reference data, custom fields) and the agent options from the `assistant` settings. It sends them to the LLM service and relays the agent's server-sent events: step logs, then the answer and `done`.

`GET /api/assistant/status` tells the app whether answers come from the LLM (`llm`), the offline planner (`offline`), or nothing (`unavailable`). Set `LLM_SERVICE_API_KEY` here and in the LLM service to require a shared key.

## API overview

All routes are under `/api`; the full reference is at `/docs`.

| Area | Endpoints |
| --- | --- |
| Meta | `GET /health`, `GET /meta` (app name, demo accounts, sign-up) |
| Auth | `POST /auth/login`, `POST /auth/token` (OAuth2 form, for the docs), `POST /auth/signup`, `GET`/`PATCH /auth/me`, `POST /auth/change-password` |
| Users (admin) | `GET`/`POST /users`, `PATCH`/`DELETE /users/{id}` |
| Trials | `GET /trials` (`crop`, `product`, `country`, `year`, `trial_type`, `status`, free text `q`, custom fields as `cf_<key>`; spelling variants accepted), `GET /trials/{id}` (with source records and overrides) |
| Trials (admin) | `POST /trials`, `PATCH`/`DELETE /trials/{id}`, `GET /trials/deleted`, `POST /trials/{id}/restore`, `POST /trials/{id}/conflicts/{field}/resolve`, `DELETE /trials/{id}/overrides/{field}` |
| Analysis | `POST /compare` (rows, chart data, summary with the best and worst uplift, ties included, and warnings), `GET /analytics/overview` (KPIs and uplift by product, crop, country, year, type) |
| Settings | `GET /settings`; admin: `PUT /settings/{section}`, `POST /settings/{section}/reset` |
| Data (admin) | `POST /data/import` (multipart, `dry_run`), `GET /data/files`, `DELETE /data/files/{id}`, `POST /data/rebuild`, `POST /data/import-sample` |
| Sources | `GET /sources/{filename}` (original file content) |
| Assistant | `GET /assistant/status`, `POST /assistant/chat`, `POST /assistant/chat/stream`, `GET /assistant/graph`; admin: `POST /assistant/test` |
| Admin | `GET /audit`, `POST /demo/reset` (demo mode only) |

## Configuration

Settings come from environment variables or `.env` in this folder (see `.env.example`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///<repo>/data/agritrial.db` | Any SQLAlchemy URL. |
| `SECRET_KEY` | generated into `data/.secret_key` | Signs login tokens. Set a long random value in production. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `720` | Session length. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated origins of the React app. |
| `DEMO_MODE` | `true` | Demo accounts, sample data, the login-page account list and Reset demo. |
| `SAMPLE_DATA_DIR` | `../sample-data`, else `./demo_data` | Folder imported in demo mode. |
| `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD` | empty | First admin, created when the database has no users. |
| `LLM_SERVICE_URL` | `http://127.0.0.1:8100` | The LLM service. |
| `LLM_SERVICE_API_KEY` | empty | Shared key; must match the LLM service. |
| `LLM_SERVICE_TIMEOUT_S` | `300` | Longest wait for an answer. Answers from a large model on a CPU can take minutes. |
| `UPLOAD_MAX_BYTES` | `5000000` | Largest accepted upload. |

## Tests

```powershell
python -m pytest
```

The 65 tests run against an in-memory database with a fake LLM service, so they don't need the LLM service or Ollama. They cover:
- import and reconciliation of the sample data, including the conflict, the provenance, the caveats, re-imports, dry runs and the LLM extraction fallback;
- auth and roles, including the admin guards;
- trial edits, overrides, conflict resolution, deletion and restore;
- comparison and analytics;
- settings that rebuild the data;
- the assistant relay;
- demo reset.

## Project layout

```
app/
├── main.py            # create_app(): routers, CORS, startup seeding
├── config.py          # Settings (.env)
├── database.py        # engine, sessions
├── models.py          # User, SourceFile, SourceRecord, Trial, TrialOverride, AppSetting, AuditEvent
├── security.py        # password hashing, JWT
├── deps.py            # current user, admin guard, settings, LLM client
├── routers/           # meta, auth, users, trials, analysis, settings, data, assistant, admin
├── schemas/           # Pydantic models, including every settings section with its defaults
└── services/
    ├── parsers.py     # CSV and report parsers, LLM extraction merge
    ├── normalizer.py  # vocabulary and unit conversion
    ├── reconciler.py  # per-trial reconciliation, conflicts, uplift range, caveats
    ├── ingestion.py   # store files, import, rebuild
    ├── trials.py      # trial view, filters, edits, overrides, conflict resolution, delete/restore
    ├── analysis.py    # comparison with chart data, analytics
    ├── llm_client.py  # HTTP client of the LLM service (incl. SSE)
    ├── assistant.py   # dataset and options sent to the agent
    ├── seed.py        # demo accounts, example custom fields, sample import, demo reset
    └── users.py, settings_store.py, audit.py
demo_data/             # copy of the sample data, used when ../sample-data is absent
tests/
```
