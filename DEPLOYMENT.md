# Deploying Nexora (all free tier)

Five accounts, all free, $0/month:

| Service | What it's for | Free tier |
|---|---|---|
| [Render](https://render.com) | Hosts the Streamlit app, the Flask API, and n8n | 3 free web services |
| [TiDB Cloud](https://tidbcloud.com) | The app's MySQL database | Starter (free) plan: 5GB storage, 50M request units/month, no card required |
| [Neon](https://neon.tech) | Postgres backing n8n's own workflow/credential storage | Always-on free tier (unlike Render's own free Postgres, which expires after 90 days) |
| [Groq](https://console.groq.com) | Runs the Task 2 skill-classification LLM call | Free tier, no card required |
| GitHub | Already set up — Render deploys straight from this repo | — |

I can't create these accounts or click "Deploy" for you — that needs your login and, in some cases, payment-method-free signup. This is the exact sequence to follow.

## Why not just "Render for everything"

Render has no managed MySQL (Postgres only), and self-hosting Ollama for Task 2's LLM step needs an always-on ~$25/mo instance for enough RAM — not free. TiDB Cloud + Neon + Groq fill exactly those two gaps at $0/mo.

(Earlier drafts of this doc pointed at PlanetScale — it discontinued its free/Hobby tier in 2024. TiDB Cloud's Starter plan is the current free, no-card MySQL-wire-protocol-compatible replacement.)

---

## 1. TiDB Cloud — the app's database

1. Sign up at [tidbcloud.com](https://tidbcloud.com) (no card required for the Starter plan).
2. Create a new cluster on the **Starter** plan. TiDB speaks the MySQL wire protocol, so `PyMySQL` and this app's MySQL-specific SQL work against it without changes.
3. Once the cluster is ready, open its **Connect** panel and select the **General** (or "Connect with" → generic MySQL) option — it shows plain `host`, `port`, `user`, `password`, and a default database name.
4. **FK support**: TiDB officially supports `FOREIGN KEY` constraints (enabled by default, `foreign_key_checks=ON`) as of recent TiDB versions, which TiDB Cloud runs — so `db/schema.sql` should apply as-is. If the app's first startup still fails with a foreign-key-related SQL error on an older cluster version, the fix is safe and mechanical: open `db/schema.sql`, delete the 4 lines reading `FOREIGN KEY (person_id) REFERENCES persons(person_id)` (one each in `applicant_details`, `gig_worker_details`, `cbnexus_contacts`, `audio_submissions`), commit, redeploy. Nothing in the app relies on FK-enforced behavior (no cascading deletes anywhere) — this is purely a compatibility fix, not a functional change.

You do **not** need to manually load the schema — the app creates it automatically on first request (see step 6).

## 2. Neon — n8n's own storage

1. Sign up at neon.tech, create a project (any name, e.g. `nexora-n8n`).
2. From the project dashboard, copy the connection details (host, database name, user, password — Neon's connection string has all of these).

## 3. Groq — the LLM for Task 2

1. Sign up at console.groq.com (no card needed).
2. **API Keys → Create API Key**. Copy it now — you can't view it again later.

## 4. Push this repo to GitHub

Already done if you're reading this from the deployed repo. If not: `git push origin main`.

## 5. Deploy the Render Blueprint

1. Sign up at render.com, connect your GitHub account.
2. **New → Blueprint**, select this repo. Render reads `render.yaml` and shows all 3 services it's about to create: `nexora-app`, `nexora-api`, `nexora-n8n`.
3. Before clicking **Apply**, Render will prompt for every `sync: false` env var. Fill them in:

   **nexora-app** and **nexora-api** (same MySQL values for both, from TiDB Cloud step 1):
   - `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` — from TiDB Cloud
   - `ADMIN_USERNAME` / `ADMIN_PASSWORD` (nexora-app only) — pick real values, not the repo's documented defaults
   - `SESSION_SECRET` / `API_KEY` — leave these alone, `generateValue: true` in render.yaml makes Render generate strong random values automatically

   **nexora-n8n**:
   - `DB_POSTGRESDB_HOST`, `DB_POSTGRESDB_DATABASE`, `DB_POSTGRESDB_USER`, `DB_POSTGRESDB_PASSWORD` — from Neon

4. Click **Apply**. All 3 services build and deploy (first build takes a few minutes each, especially `nexora-n8n` pulling the official n8n image).

## 6. Verify each service

- **nexora-app**: visit its `.onrender.com` URL. First load triggers automatic schema creation on TiDB Cloud (see step 1's FK note if this fails) and seeds your admin account from `ADMIN_USERNAME`/`ADMIN_PASSWORD`. Log in, confirm the Data Merge Engine page loads.
- **nexora-api**: visit `<url>/health` — should return `{"status": "ok", ...}`.
- **nexora-n8n**: visit its URL — n8n shows an **owner account setup screen** on first visit. **Complete this immediately** — that account is what gates the editor (n8n has no other auth by default, and the editor's Code nodes are effectively arbitrary code execution if left open).

## 7. Wire up the actual URLs

Render assigns each service a URL like `https://nexora-app-xxxx.onrender.com` — the `xxxx` suffix only appears if your exact chosen name collided with someone else's. If your 3 services got the plain names (`nexora-app`, `nexora-api`, `nexora-n8n`), the URLs already baked into `render.yaml` and `automation/skill_tagging_flow.json` are correct as-is. If not:

- Update `nexora-app`'s `N8N_BASE_URL` env var (Render dashboard) to your real n8n URL.
- You'll fix the API URLs in the n8n workflow itself in the next step anyway.

## 8. Import the n8n workflow

1. In your deployed n8n, **Workflows → Import from File**, select `automation/skill_tagging_flow.json`.
2. Create two credentials (**Credentials → New**, type **Header Auth**):
   - **"Groq API Key"**: Name = `Authorization`, Value = `Bearer <your Groq key from step 3>`
   - **"Nexora API Key"**: Name = `X-API-Key`, Value = `<the API_KEY Render generated for nexora-api — find it in that service's Environment tab>`
3. Open the imported workflow's 3 HTTP Request nodes (**Fetch People Needing Tags**, **Classify Skills via LLM**, **Write Back to Database**) and select the matching credential in each (the import can't auto-attach credentials across n8n instances — this is normal, expected n8n behavior, not a sign anything's broken).
4. If your `nexora-api` URL didn't come out as plain `nexora-api.onrender.com` (see step 7), edit the URL in **Fetch People Needing Tags** and **Write Back to Database** to match.
5. Run `pipeline/merge.py` once against the TiDB Cloud DB (or use the Data Merge Engine page) so there's actual data to tag, then click **Execute Workflow**.

---

## Known limitations of this setup

- **Uploaded CSVs and audio recordings are not reliably persistent.** Render's free web services have no persistent disk — files written to `data/uploads/`/`data/audio/` during a request may not survive a redeploy or restart. Fine for trying the app out; for real use, this needs object storage (S3/R2) — a bigger change, intentionally out of scope here.
- **Free-tier cold starts.** Render free services spin down after 15 minutes of inactivity and take ~30-60s to wake back up on the next request. Expect the first visit after a quiet period to be slow.
- **TiDB Cloud FK support on older cluster versions** — see step 1.
