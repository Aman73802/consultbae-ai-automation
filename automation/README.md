# Task 2 — n8n skill-tagging automation

**What it does:** reads every person in the Task 1 database who has skills
data but no `skill_category` yet, sends their name + skills to an LLM to
classify into `automation-heavy` / `web dev` / `data`, and writes the
result back into `persons.skill_category`.

It's a 5-node linear flow: Manual Trigger → GET people → POST to an LLM →
Code node parses the response → PATCH the result back. n8n runs the
middle three nodes once per person automatically (that's default n8n
behavior whenever a node receives multiple input items — no loop node
needed).

## Why a local API instead of an n8n SQLite node

n8n's SQLite support depends on a community node that isn't installed by
default on every n8n instance. Instead, `automation/api_server.py` is a
tiny Flask app that reads/writes the exact same `db/consultbae.db` that
Task 1 built and Task 3 writes to, over plain HTTP — works against any
stock n8n install (cloud or self-hosted) with just the built-in HTTP
Request node.

## 1. Start the supporting API

From the repo root, with the venv active:

```bash
pip install -r requirements.txt      # if you haven't already
python3 pipeline/merge.py            # make sure db/consultbae.db exists
python3 automation/api_server.py     # serves on http://localhost:5001
```

Sanity check in another terminal:

```bash
curl http://localhost:5001/api/people | head
```

You should see a JSON array of `{person_id, full_name, skills}` for
everyone who doesn't have a `skill_category` yet.

(If you'd rather inspect the data without running the server, `python3
automation/export_people.py` dumps the same shape to
`automation/people_export.json`.)

## 2. Import the workflow into n8n

1. Open n8n (self-hosted at `http://localhost:5678`, or the cloud trial).
2. **Workflows → Import from File** (or the "..." menu → Import) and
   select `automation/skill_tagging_flow.json`.
3. n8n will show the "Classify Skills via LLM" node with a missing
   credential — that's expected, hand-authored/exported workflows don't
   carry credentials across instances. Fix it in step 3 below.

If import ever fails for you (schema drift between n8n versions), the
flow is simple enough to rebuild by hand in ~5 minutes: Manual Trigger →
HTTP Request (GET) → HTTP Request (POST to your LLM) → Code → HTTP
Request (PATCH). The exact parameters for each are in the JSON file and
in the description above.

## 3. Connect your OpenAI (or Anthropic) API key

The "Classify Skills via LLM" node calls OpenAI's
`/v1/chat/completions` endpoint using a **Header Auth** credential
(n8n's generic credential type — works for any bearer-token API):

1. Click the **Classify Skills via LLM** node.
2. Under Credential, click **Create New** → choose **Header Auth**.
3. Set:
   - **Name**: `Authorization`
   - **Value**: `Bearer sk-...your OpenAI key...`
4. Save, then select that credential on the node.

**To use Anthropic instead of OpenAI:** change the node's URL to
`https://api.anthropic.com/v1/messages`, set the header credential's
Name to `x-api-key` and Value to your Anthropic key, add a second header
`anthropic-version: 2023-06-01`, and swap the JSON body for Anthropic's
Messages API shape (`model`, `max_tokens`, `messages`). The downstream
Code node would then read `$json.content[0].text` instead of
`$json.choices[0].message.content`.

## 4. Run it

Click **Execute Workflow**. Watch each node go green; open the "Write
Back to Database" node's output to see the PATCH responses. Re-running
is safe/idempotent — anyone already tagged is excluded from the next
`GET /api/people` call.

Verify the result landed in the DB:

```bash
sqlite3 db/consultbae.db "SELECT person_id, full_name, skill_category FROM persons WHERE skill_category IS NOT NULL LIMIT 10;"
```

## Design notes / judgment calls

- **Category taxonomy** (`automation-heavy`, `web dev`, `data`) is fixed
  in the system prompt and validated in the Code node (`Extract Skill
  Category`) — if the LLM ever returns something outside the three
  allowed labels, the Code node defaults it to `data` rather than
  writing garbage into the column. Worth watching in the video/live
  demo, and worth mentioning as a real production concern (real
  pipelines from LLM-classification steps need this kind of guardrail).
- **`temperature: 0`** for repeatable classifications.
- **One HTTP call per person** — fine at this dataset's size (~55
  people). At real gig-worker volume (thousands), this flow would need
  batching/rate-limit handling (a `Wait` node or n8n's built-in retry/
  backoff options) before it's production-ready; see the Task 5 stretch
  section in the main README for the same theme applied to the audio app.
