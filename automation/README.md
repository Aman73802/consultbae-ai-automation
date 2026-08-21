# Task 2 — n8n skill-tagging automation

## Running n8n itself locally

`npx n8n start` works out of the box on most machines. If your system
Node is very new (this happened on Node v26 here), n8n's `isolated-vm`
native dependency can fail to compile against it -- you'll see `npm
error` / `isolated-vm ... install { code: 1 }` in the log. Fix: run n8n
under a Node LTS release instead (v22/v24), without touching your
system Node:

```bash
mkdir -p .node-local && cd .node-local
curl -L -o node.tar.gz "https://nodejs.org/dist/v24.19.0/node-v24.19.0-darwin-arm64.tar.gz"
tar -xzf node.tar.gz
cd ..
export PATH="$(pwd)/.node-local/node-v24.19.0-darwin-arm64/bin:$PATH"
node --version   # should print v24.19.0, not your system version
N8N_USER_FOLDER="$(pwd)/.n8n-local" npx --yes n8n start
```

(Swap `darwin-arm64` for `linux-x64`/`darwin-x64`/etc. from
https://nodejs.org/dist/v24.19.0/ if you're not on Apple Silicon macOS.)
Once it logs `Editor is now accessible via: http://localhost:5678`,
open that URL and continue below.

**What it does:** reads every person in the Task 1 database who has skills
data but no `skill_category` yet, sends their name + skills to an LLM to
classify into `automation-heavy` / `web dev` / `data`, and writes the
result back into `persons.skill_category`.

It's a 5-node linear flow: Manual Trigger → GET people → POST to an LLM →
Code node parses the response → PATCH the result back. n8n runs the
middle three nodes once per person automatically (that's default n8n
behavior whenever a node receives multiple input items — no loop node
needed).

## Why a local API instead of an n8n MySQL node

n8n does have a MySQL node, but it needs credentials configured inside
n8n and doesn't give you a place to run the "combine + dedupe skill
tags from two different tables" logic `combined_skills()` does. Instead,
`automation/api_server.py` is a tiny Flask app that reads/writes the
exact same `consultbae` MySQL database that Task 1 built and Task 3
writes to, over plain HTTP — works against any stock n8n install (cloud
or self-hosted) with just the built-in HTTP Request node, no n8n-side
DB credentials needed at all.

## 1. Start the supporting API

From the repo root, with the venv active:

```bash
pip install -r requirements.txt      # if you haven't already
bash db/start_mysql.sh               # start the local MySQL server (see main README)
python3 pipeline/merge.py            # make sure the consultbae DB exists
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
3. As imported, the LLM node points at a local Ollama instance and
   needs no credential setup at all — just make sure Ollama is running
   (step 3 below). Only switch to OpenAI/Anthropic if you'd rather use
   those.

If import ever fails for you (schema drift between n8n versions), the
flow is simple enough to rebuild by hand in ~5 minutes: Manual Trigger →
HTTP Request (GET) → HTTP Request (POST to your LLM) → Code → HTTP
Request (PATCH). The exact parameters for each are in the JSON file and
in the description above.

## 3. The LLM step

The workflow's "Classify Skills via LLM" node is set up to call a local
**Ollama** instance by default (`http://localhost:11434/api/chat`,
model `llama3.2:1b`) — no API key, no signup, runs entirely on this
machine, no cost. This was the fallback after hitting repeated Google
OAuth session errors trying to sign up for OpenAI/Anthropic; it turned
out to be a reasonable choice on its own merits for a 3-label
classification task this simple.

Start it (same no-brew/no-sudo pattern as MySQL and Node):

```bash
mkdir -p .ollama-local && cd .ollama-local
curl -L -o ollama.tgz "https://github.com/ollama/ollama/releases/download/v0.32.15/ollama-darwin.tgz"
tar -xzf ollama.tgz
cd ..
export OLLAMA_MODELS="$(pwd)/.ollama-local/models"
./.ollama-local/ollama serve &          # starts the API on :11434
./.ollama-local/ollama pull llama3.2:1b # ~1.3GB, one-time
```

(Swap `ollama-darwin.tgz` for the right asset from
https://github.com/ollama/ollama/releases/latest if you're not on macOS.)

No credential setup needed in n8n for this node — it calls plain HTTP
with no auth required, since Ollama's local API is unauthenticated by
design (it only listens on localhost).

**If you'd rather use OpenAI or Anthropic instead** (e.g. you already
have a key handy): change the node's URL to
`https://api.openai.com/v1/chat/completions` (OpenAI) or
`https://api.anthropic.com/v1/messages` (Anthropic), set
`authentication` to `genericCredentialType` / `httpHeaderAuth` and
create a **Header Auth** credential (`Authorization: Bearer sk-...` for
OpenAI, or `x-api-key: sk-ant-...` + a second header
`anthropic-version: 2023-06-01` for Anthropic). For Anthropic, also
swap the JSON body to `{model, max_tokens, messages}` and change the
downstream Code node to read `$json.content[0].text` instead of
`$json.message.content`.

## 4. Run it

Click **Execute Workflow**. Watch each node go green; open the "Write
Back to Database" node's output to see the PATCH responses. Re-running
is safe/idempotent — anyone already tagged is excluded from the next
`GET /api/people` call.

Verify the result landed in the DB:

```bash
.mysql-local/mysql-9.1.0-macos14-arm64/bin/mysql -uconsultbae -pconsultbae_dev_pw \
  --socket=.mysql-local/mysql.sock consultbae \
  -e "SELECT person_id, full_name, skill_category FROM persons WHERE skill_category IS NOT NULL LIMIT 10;"
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
- **Ollama over OpenAI/Anthropic**: chosen after repeatedly hitting a
  Google OAuth session error (`accounts.google.co.in/accounts/SetSID`
  returning 400) trying to sign up for either provider — a local browser/
  cookie issue unrelated to this project, not something worth blocking
  on. A 1B-parameter local model is genuinely adequate here (a 3-label
  classification from a short skill list is a much easier task than
  open-ended generation), so this isn't purely a workaround; it's a
  legitimate "does this need a frontier model?" call. The tradeoff worth
  being able to defend: a real hosted model would likely be more
  consistent on edge-case skill lists, and Ollama adds a dependency
  (~1.3GB local model, a running server process) that a cloud API
  doesn't. Swapping back to OpenAI/Anthropic is a small, documented
  change (above) if that tradeoff should go the other way.
