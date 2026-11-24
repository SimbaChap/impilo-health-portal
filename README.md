# Impilo Registers - local Windows setup

Runs the register API and pages on this PC against the restored Avondale database.
Everything listens on 127.0.0.1 only; nothing is reachable from other computers.

## First time (once)

1. Make sure `2026-11-02.zip` (or the extracted `Avondale_application_only.sql`) is in your
   Downloads, Documents or Desktop folder. Setup finds it automatically.
   If it is somewhere else, open a terminal in this folder and run:
   `setup.cmd -DumpPath "D:\path\to\Avondale_application_only.sql"`
2. Double-click **setup.cmd**. It will:
   - install MariaDB and Python 3.12 with winget if they are missing (Windows may ask for approval)
   - create a private database instance in `data\` on port 3307
   - import the 2.1 GB dump (10-40 minutes)
   - create the read-only `attendance_api` user, apply all grants in `sql\`, and write `api\.env`

## Every day

- **start.cmd** - starts the database, the API (http://127.0.0.1:8765) and the pages
  (http://127.0.0.1:8080), then opens your browser.
- **stop.cmd** - stops all three.
- **update.cmd** - after Claude changes the API or adds SQL scripts: applies `sql\*.sql` and restarts the API.

API docs: http://127.0.0.1:8765/docs . Logs are in `logs\`.

## Notes

- `api\.env` and `config\root-client.cnf` contain generated passwords. Don't share or commit them.
- `data\` holds patient records. Keep this folder off shared drives and out of OneDrive sync.
- Running setup again is safe: it skips the import and gives the API user a fresh password.

## Facility registry (national)

`sql\01-facility-registry.sql` builds the `registry` database from the master facility list:
`registry.province` (10), `registry.district` (69), `registry.facility_type`, and
`registry.facility_registry` (1,542 facilities: ID, name, province, district, type, latitude, longitude,
DHIS2 code). Only Avondale Clinic (ZW000A01) has clinical data in this database; every other facility
shows as registered but not yet reporting.

Facility types: Clinic, Rural / District / Provincial / Central hospital, Other hospital. The master list
has no type field, so types are derived and the source is stored per facility (`facility_type_source`):
known list of central/provincial hospitals, the facility name, or the facility code (hospital codes end in
0 + a letter). Set `facility_type_source = 'manual'` on a row to keep a hand-corrected type when the
script re-runs. Load the official MoHCC/DHIS2 classification when available.

API: `GET /api/geography`, `GET /api/facilities?province=&district=&type=&q=`, `GET /api/facilities/{id}`,
`GET /api/dashboard?province=&district=&type=&facility=&from=&to=` (`type` also accepts `HOSPITAL`).
`DATA_FACILITY_IDS` in `api\.env` (default ZW000A01) lists facilities whose data is loaded. In the future
multi-tenant database every row will carry `facility_id`, and the register queries must then filter by the
reporting facilities in scope.

The Overview tab shows `web\facility-dashboard.html`. Its source is `web-src\facility-dashboard.template.html`
plus `web-src\provinces.geoboundaries.json` (province outlines, geoBoundaries CC BY 4.0).

## Portal-wide filters

One filter bar (Province, District, Facility type, Facility, Period) sits at the top of the portal
(`web\global-filters.js`) and applies to every section that shows live data: Overview and all registers.
It is remembered across sections and visits. Embedded pages hide their own filters and follow it:
the Overview via `facility-dashboard.html`, registers via `web\register-global-filters.js`.
Opened on their own (outside the portal), pages keep their own filter controls.

## Map shading

On the Overview map, "Show on map" picks an indicator and "Shade by" picks Province, District or Facility.
Colour intensity shows the value; figures appear on hover only. District shading uses the official district
boundaries (geoBoundaries ZWE ADM2, CC BY 4.0): each facility is placed in a district by its coordinates, so
facilities without a location can't be shaded. Regenerate `web-src\districts.geoboundaries.json` with
`python web-src\build_districts.py web-src\geoBoundaries-ZWE-ADM2_simplified.geojson web-src\districts.geoboundaries.json`
(needs shapely), then rebuild `web\facility-dashboard.html` from the template.

## AI assistant (Ollama)

"Ask the data" is the chat: answers use aggregated portal figures for the current filters (never patient records).
Models run on this PC with Ollama (https://ollama.com/download). The **Configuration** section (below "Ask the data") has:
- **Models** - start/stop Ollama, download models (with progress), start/stop a model in memory, delete, set the default.
- **Optimise** - creativity, answer length, reading window, memory, CPU/GPU use; "Recommend for this PC".
- **Train** - assistants with a domain, instructions, answer style, language and example Q&As. "Build" creates a
  custom Ollama model (`impilo-<name>`). This shapes answers; it does not fine-tune the model's weights.

Code: `api\ai.py` (endpoints under /api/ai), `web\ai.html`. Settings and assistants are saved in `api\ai-data\`.
Ollama address: `OLLAMA_URL` in `api\.env` (default http://127.0.0.1:11434).

## Sign-in and users

The portal now requires a sign-in. The first time you open it you'll see **Set up the portal**: create the first administrator there.

- Administrators can add, edit and deactivate users from the menu on their name (top right), and are the only ones who can change the AI configuration.
- Viewers can use every page and chat with the assistant.
- Accounts live in `api/auth-data/users.json` (passwords are stored only as salted hashes) and sign-ins are signed with `api/auth-data/secret.key`. Keep that folder private and don't copy it to other machines. Deleting `secret.key` signs everyone out.
- If every administrator is locked out, stop the portal, delete `api/auth-data/users.json` and start again to get the setup screen back.
- Sessions last 12 hours. Five wrong passwords in 15 minutes pause sign-in for that account.

Saved chats are kept per user in the browser, and any chat can be starred to pin it under **Favourites**.
