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

No auth needed for local use (the `API_KEY` env var is unset, so `_require_api_key()` is a no-op). Deploying this somewhere publicly reachable? Set `API_KEY` first -- see DEPLOYMENT.md -- otherwise anyone who finds the URL can read every person's name/skills and overwrite `skill_category` values.

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
3. That's it — no credentials to create. The three HTTP nodes read their
   auth headers from n8n's own environment via `{{ $env.NEXORA_API_KEY }}`
   and `{{ $env.GROQ_API_KEY }}`, so start n8n with both set:

   ```bash
   NEXORA_API_KEY="$API_KEY" GROQ_API_KEY=gsk_... npx n8n start
   ```

   (`NEXORA_API_KEY` is whatever `API_KEY` the Flask API is running with —
   leave both unset for a purely local run where the API has no auth.)

   **Why env vars and not n8n credentials**: n8n credentials live in n8n's
   own database and are deliberately never included in an exported
   workflow — only the credential *name* is. So a fresh import, or a
   redeployed n8n whose database was replaced, loses every credential and
   each node fails with "Credentials not found" until you recreate them by
   hand. Env vars survive both, keep the secrets out of the committed JSON
   exactly the same way, and let the imported workflow run untouched.

   In the editor, an expression preview may read
   `[ERROR: access to env vars denied]` even when it works at runtime;
   that's a known n8n UI quirk, not a misconfiguration. If it genuinely
   fails when executed, set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`
   explicitly (it is the default, but can be flipped by a host).

   Prefer no external LLM API at all? See "Local alternative: Ollama" in
   step 3.

If import ever fails for you (schema drift between n8n versions), the
flow is simple enough to rebuild by hand in ~5 minutes: Manual Trigger →
HTTP Request (GET) → HTTP Request (POST to your LLM) → Code → HTTP
Request (PATCH). The exact parameters for each are in the JSON file and
in the description above.

## 3. The LLM step

The workflow's "Classify Skills via LLM" node calls **Groq**'s hosted,
OpenAI-compatible API (`https://api.groq.com/openai/v1/chat/completions`,
model `openai/gpt-oss-20b`) — free tier, no card required, and nothing
to run yourself. Get a key from console.groq.com and hand it to n8n as
`GROQ_API_KEY` (step 2); the node builds its own
`Authorization: Bearer ...` header from it.

If Groq retires that model (it did retire the `llama-3.1-*` line this
workflow used to point at), you'll get "The model ... does not exist";
check https://console.groq.com/docs/models for the current ID and change
just the `model` field in the node's JSON body.

Same reasoning as before, just aimed at deployment instead of a local
machine: this is a 3-label classification task from a short skill list,
not open-ended generation, so a small model is genuinely adequate —
running that model yourself (Ollama, below) costs nothing per request
but needs an always-on server; a free-tier hosted API needs no server
at all.

### Local alternative: Ollama

Prefer running the LLM fully locally, no external API/account at all?
Point the node back at Ollama instead:

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

In the node: URL back to `http://localhost:11434/api/chat`,
`authentication` back to `none`, JSON body back to Ollama's shape
(`{model, stream: false, options: {temperature: 0}, messages}` instead
of OpenAI's `{model, temperature, messages}`), and the downstream Code
node's response parsing back to `$json.message.content` instead of
`$json.choices[0].message.content`. No credential needed — Ollama's
local API is unauthenticated by design (only listens on localhost).

**OpenAI or Anthropic instead**: change the node's URL to
`https://api.openai.com/v1/chat/completions` (OpenAI, same body/response
shape as Groq — just swap the credential and model name) or
`https://api.anthropic.com/v1/messages` (Anthropic — needs
`x-api-key: sk-ant-...` + a second header `anthropic-version:
2023-06-01`, body shape `{model, max_tokens, messages}`, and the Code
node reading `$json.content[0].text`).

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
- **One HTTP call per person, throttled.** This was originally listed
  here as a "fine at ~55 people, would need rate-limit handling at real
  volume" caveat -- it turned out to bite at 55, not at thousands: fired
  back-to-back, the run died partway through with Groq's free-tier
  tokens-per-minute limit (`Rate limit reached ... TPM: Limit 8000`).
  So the node now uses n8n's built-in options rather than leaving it as
  a known-future-problem: **batching** (1 item per batch, 4000ms
  interval, ~15 calls/min -- comfortably under an 8000 TPM ceiling at
  ~450 tokens/call) plus **retryOnFail** (3 tries, 5s apart) so a
  transient 429 retries instead of failing the whole run. Cost: ~55
  people × 4s ≈ 4 minutes per full pass, which is the right trade for a
  batch job nobody is watching live. At genuinely large volume the
  interval should come from the provider's published limits rather than
  this hardcoded number, and a paid tier removes the constraint entirely.
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
  change (above) if that tradeoff should go the other way. It did, once
  deployment entered the picture: self-hosting Ollama on a server costs
  real money for an always-on instance with enough RAM, so the shipped
  workflow now defaults to Groq's free hosted API instead (step 3) --
  Ollama is still there as the fully-local, no-external-account option.
