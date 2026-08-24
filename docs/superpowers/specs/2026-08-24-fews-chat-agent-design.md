# Design: FEWS configuration chat agent

**Date:** 2026-08-24
**Status:** Approved — all open questions resolved 2026-08-24
**Site:** https://df-docs.streamflows.org

## Goal

Add a chat panel to the Delft-FEWS Config Guide where a signed-in user can ask
questions about FEWS configuration and get answers grounded in this site's own
documentation, with links back to the relevant pages.

## Decisions already made

| Question | Decision |
|---|---|
| Who can use it | Signed-in `streamflows.org` users only. Docs stay public; only the chat is gated. |
| How the agent knows FEWS | The entire Markdown corpus goes in the system prompt behind a prompt-cache breakpoint, plus a tool for on-demand schema field lookups. No embeddings, no vector store. |
| Where the backend lives | A small Flask service on this VPS, proxied by nginx. The Astro site stays a pure static build. |
| Which group | The existing `streamflow` group. No admin-database change needed. |
| Model | `claude-sonnet-5`. |
| Spend ceiling | $2.00/day, org-wide, enforced in dollars. |

## Architecture

```
Browser (static Starlight page)
  │  chat drawer, vanilla JS in an .astro component
  │  POST /api/chat  (SSE response)
  │  cookie: streamflows_auth  (domain .streamflows.org, sent automatically)
  ▼
nginx  df-docs.streamflows.org
  │  location /api/chat  →  proxy_pass 127.0.0.1:8057, proxy_buffering off
  ▼
Flask service  fewsdocs-chat.service  (gunicorn, 127.0.0.1:8057)
  │  1. verify JWT from cookie      → 401 JSON if absent/expired/wrong group
  │  2. verify Origin header        → 403 JSON on mismatch (CSRF)
  │  3. rate limit + daily budget   → 429 JSON when exceeded
  │  4. validate client history     → 400 JSON on malformed input
  ▼
Anthropic Messages API  (streaming, with a tool loop)
  tools  = [ lookup_config_fields ]              ← renders first, must be stable
  system = [ persona,  FULL DOCS CORPUS ]        ← cache_control breakpoint here
  messages = client-supplied history + tool results
```

The service holds no conversation state. The browser keeps the transcript and
sends it back each turn, so a service restart never drops a conversation and
there is no session store to run.

### Where the code lives

The chat service lives **inside this repository** at `chat/`, not in a separate
project. That is the whole point: the deploy clone at `/home/fewsdocs/repo`
already pulls this repo, so one `git pull` updates the documentation and the
corpus the agent reads from it in the same step. A separate repo would let the
two drift apart, and a stale corpus is the failure mode that matters most here —
the agent would confidently answer from documentation the site no longer shows.

```
chat/
  app.py             create_app(), config, blueprint registration
  auth.py            require_streamflows_user decorator (JSON 401/403)
  corpus.py          walks ../src/content/docs, builds the cached corpus string
  schema_tool.py     lookup_config_fields tool definition + JSON-to-text handler
  security.py        Origin check, RateLimiter, DailyBudget
  conversation.py    validates and truncates the client-supplied history
  agent.py           Anthropic client, system prompt assembly, tool loop, streaming
  routes.py          POST /api/chat  and  GET /api/chat/status
  requirements.txt   pinned exact versions
  tests/
```

## Component detail

### 1. Auth — and the trap in `streamflows_auth`

The site reuses the existing SSO. The login service at `apps.streamflows.org`
sets a `streamflows_auth` JWT cookie scoped to `.streamflows.org`, so
`df-docs.streamflows.org` receives it with no extra work.

**Two things make `protect_app()` the wrong tool here, and both are easy to miss:**

1. `protect_app()` **exempts every path beginning with `/api/`**
   (`_EXEMPT_PREFIXES = ("/_dash-", "/assets/", "/api/")`). Mounting the chat at
   `/api/chat` and calling `protect_app()` would produce an endpoint that looks
   guarded and is in fact wide open to the internet. This is the single most
   important detail in this document.
2. `protect_app()` **redirects** to the login page on failure. A `fetch()` that
   receives a 302 to an HTML login form cannot show the user anything useful.

So the chat service does **not** call `protect_app()`. It defines its own
decorator that reuses the same token logic — `decode_token()` from
`streamflows_auth.tokens` stays the single source of truth for JWT verification,
so there is no second copy of the secret handling to drift.

```python
# chat/auth.py
from functools import wraps
import jwt
from flask import request, jsonify, g
from streamflows_auth.tokens import decode_token

REQUIRED_GROUP = "streamflow"
ADMIN_GROUP = "admin"


def require_streamflows_user(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = request.cookies.get("streamflows_auth")
        if not token:
            return jsonify({"error": "not_authenticated"}), 401
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "session_expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "not_authenticated"}), 401
        groups = payload.get("groups", [])
        if REQUIRED_GROUP not in groups and ADMIN_GROUP not in groups:
            return jsonify({"error": "not_authorized"}), 403
        g.current_user = payload.get("sub", "")
        return view(*args, **kwargs)
    return wrapped
```

The gate is the existing `streamflow` group — the same one the synopsis tool
uses — so there is no admin-database change and no new group to administer.
Anyone who can already reach the forecasting apps can use the chat. Members of
`admin` get in automatically, matching every other app on the box.

`GET /api/chat/status` is the same check with no body — the widget calls it on
page load to decide whether to render the chat input or a "Sign in to ask"
link pointing at
`https://apps.streamflows.org/login?next=<current page URL>`.

### 2. CSRF

`POST /api/chat` changes state (it spends money) and authenticates from a
cookie, so it needs CSRF protection. The synopsis tool's signed-session-token
approach does not fit here, because that pattern requires a server-rendered page
to seed the token and these pages are static HTML built by Astro.

The fit for a static front end is an **Origin check**: reject the request unless
the `Origin` header exactly matches the site's own origin. Combined with the
cookie's existing `SameSite=Lax` (which already blocks cross-site POSTs) and a
required `Content-Type: application/json` (which forces a preflight for any
cross-origin attempt), this gives layered protection with no token plumbing.

### 3. Corpus

`corpus.py` walks `src/content/docs/**/*.{md,mdx}` at service startup, strips
each file's frontmatter, and concatenates the pages into one string. Each page is
prefixed with its title and its live URL so the agent can cite real links:

```
=== Define locations & location sets ===
URL: https://df-docs.streamflows.org/tasks/locations/
<page body>
```

Current size is 297 KB across 53 pages, roughly 75–85k tokens — comfortably
inside the context window with room to grow. Ordering is deterministic (sorted
by path) because a reordered corpus is a different byte prefix and would
silently destroy the prompt cache.

The base URL for citations comes from the service's own config, not from
`astro.config.mjs`, whose `site` field is still `http://localhost:4321` and would
produce dead links.

The corpus is read once at startup and held in memory, so **the service must be
restarted after any content deploy** or it will keep answering from the previous
build. This is added to the redeploy runbook below.

The 33 generated XSD field-reference JSON files under `src/data/schema/` are
**not** included in the corpus. They are reached through a tool instead — see the
next section, which explains why.

### 4. The schema lookup tool

The reference pages under `src/content/docs/reference/` do not contain their field
tables. Each one is prose plus a `<FieldReference data={data} />` component, and
the tables are injected at build time from `src/data/schema/*.json`. A corpus built
from Markdown alone therefore carries the explanations and none of the specifics:
the agent could describe what a Locations file is for and would be unable to say
what attributes `<location>` accepts. On a configuration reference site that is a
large and obvious class of question to fail.

Bulk inclusion does not solve it. Rendered to compact text the schema data is
~351k tokens, taking the corpus to ~425k. At a 1-hour TTL that is **$2.55 per cache
write** — a single cold message would exceed the entire daily budget.

So the agent gets a tool:

```python
{
    "name": "lookup_config_fields",
    "description": (
        "Look up the complete field and attribute reference for one Delft-FEWS "
        "configuration file, generated directly from its XSD schema. Call this "
        "whenever the user asks which fields, attributes, elements, child types, "
        "or enum values a config file supports, or whether a particular field "
        "exists. The documentation pages in your context explain concepts but do "
        "NOT contain these tables — this tool is the only way to see them."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "config_file": {"type": "string", "enum": SCHEMA_NAMES},
        },
        "required": ["config_file"],
        "additionalProperties": False,
    },
}
```

`SCHEMA_NAMES` is the sorted list of the 33 `src/data/schema/*.json` stems, and
`strict: True` pins the argument to that enum so the agent cannot invent a config
file name. The handler renders one JSON file to compact text — type name, doc
string, attributes, then fields with type, required/repeatable flags, and enum
values.

This is cheap because it is selective. The median file is ~6,200 tokens (about 2
cents at base input rate) and only loads when a question actually needs it. Even
the largest, `transformDataQuality` at ~45k tokens, costs about 14 cents — and
only when someone asks about data-quality transformations specifically.

Because tool definitions render *before* `system` in the cached prefix, the tool
list must be byte-stable across requests or it would invalidate the corpus cache
on every call. Building `SCHEMA_NAMES` by sorting the directory listing keeps it
deterministic; that ordering is not cosmetic and there is a test for it.

Adding a tool means the request becomes a loop rather than a single call: stream,
and if the turn ends with `stop_reason == "tool_use"`, run the lookup, append the
result, and continue streaming into the same SSE response. The user sees one
continuous answer. The loop is capped at 3 tool calls per message to bound both
latency and spend.

### 5. The agent call

```python
client.messages.stream(
    model="claude-sonnet-5",
    max_tokens=8000,
    system=[
        {"type": "text", "text": PERSONA},
        {"type": "text", "text": corpus,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ],
    output_config={"effort": "medium"},
    messages=history,
)
```

Notes on each choice:

- **`claude-sonnet-5`**, chosen for cost. It is strong on exactly this shape of
  work — explaining and synthesising from supplied reference material.
- **1-hour cache TTL**, not the 5-minute default. The reason is the rhythm of a
  real conversation rather than raw arithmetic: someone asks how ID mapping works,
  reads the answer, tries it in FEWS, then asks a follow-up. That gap is routinely
  longer than five minutes. With the default TTL the cache would expire *between
  turns of the same conversation*, so every single message would pay a full cache
  write — $0.30 a message instead of $0.04. The 1-hour write costs 2× base rather
  than 1.25×, and that premium buys the whole conversation staying warm.
- **`effort: "medium"`** — this is retrieval and explanation over supplied context,
  not deep reasoning. Medium keeps latency and token spend down. Tunable in config.
- **Thinking stays on** (the Opus 5 default). Disabling it is the wrong trade here:
  it saves little on a task this shaped, and it can leak internal `<thinking>` tags
  into the visible answer. Streaming solves the perceived-latency problem instead.
- **Streaming** is what makes the panel feel responsive. It requires
  `proxy_buffering off` on the nginx location, or nginx will hold the whole
  response and deliver it in one lump.

The persona prompt instructs the agent to answer only from the supplied
documentation, to link to the relevant page, and to say plainly when the docs do
not cover something rather than inventing FEWS behaviour. Given that the audience
is people configuring a live forecasting system, a confident wrong answer is worse
than an admission of a gap.

### 6. Cost and abuse controls

Authentication removes anonymous abuse, but an authenticated user can still hold
the enter key. Three independent limits:

| Control | Value | Behaviour when hit |
|---|---|---|
| Per-user rate limit | 20 messages / 5 min | 429 with a friendly retry message |
| Conversation history cap | last 12 turns, 24 KB | Older turns dropped silently |
| Daily spend budget | $2.00/day, org-wide | 429, panel shows "chat is resting until tomorrow" |

The rate limiter is the sliding-window `RateLimiter` already written in
`streamflow_synopsis_tool/web/security.py` — the same in-process approach is
correct here, since this runs as a single gunicorn worker.

The budget is denominated in **dollars, not tokens**. A token count cannot express
this ceiling, because the four token classes are priced an order of magnitude
apart: a cached read costs a tenth of base input while a 1-hour cache write costs
double it, a 20× spread. Counting raw tokens would let a handful of cache writes
blow through a cap that looked generous. So after each response the service reads
all four `usage` fields — `input_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`, `output_tokens` — multiplies each by its own rate, and
adds the result to a running daily total in a small JSON file that survives
restarts. Rates live in config alongside the model name, so changing model means
changing both together.

The check runs **before** the request is dispatched, using the previous message's
cost as the estimate. A budget that only notices after the fact would always allow
one final overshoot.

### Cost model

Measured against the current 80k-token corpus, at Sonnet 5's standing $3/$15 rate
(the introductory $2/$10 rate ends 2026-08-31, a week from now, so the standing
rate is what to plan against):

| | Cost |
|---|---|
| First message of a cold conversation (pays the cache write) | ~$0.50 |
| Each follow-up within the hour | ~$0.04 |
| One schema lookup, median config file (~6,200 tokens) | ~$0.02 |
| One schema lookup, largest config file (~45,000 tokens) | ~$0.14 |
| A five-turn conversation with two median lookups | ~$0.70 |

**$2.00/day is therefore about three five-turn conversations.** That is a real
constraint and worth knowing before the panel goes live rather than discovering it
from a "chat is resting" message on the first afternoon. It is a deliberate
ceiling, not an estimate, so the failure mode is a friendly message rather than a
surprise invoice.

Three levers if it proves too tight in practice, in order of how little they cost
you elsewhere: raise the ceiling; switch the model to `claude-haiku-4-5`, which is
a one-line config change and buys roughly three times as many conversations for
the same $2; or trim the corpus, which is the only one of the three that costs
answer quality. The dominant term in every conversation is the single cache write,
so cutting corpus size is what moves the number most — but it is also what makes
the agent dumber, which is why it is listed last.

Per your standing rule, every one of these surfaces as a clear, friendly message
in the panel rather than a silent failure or a spinner that never resolves. The
same applies to a failed Anthropic call: the panel says the assistant is
unreachable and offers a retry.

### 7. Front end

A new `src/components/ChatPanel.astro` wired in through Starlight's component
override system, which this version supports:

```js
starlight({
  components: {
    Footer: './src/components/ChatPanelFooter.astro',
  },
})
```

`Footer` is the injection point because it renders inside every documentation
page. The panel itself is `position: fixed` against the right edge with its own
stacking context, so it reads as a docked sidebar rather than page content:
docked open on wide viewports (≥1400px, where Starlight's grid leaves room), and
a collapsible overlay drawer below that. A floating button toggles it, and the
open/closed state persists in `localStorage`.

I considered overriding `PageSidebar` instead, which is the literal right-hand
rail. I am not recommending it: it would displace or compete with the table of
contents, and that rail is hidden entirely on narrower screens, which would make
the chat vanish on laptops and tablets.

The client script is plain vanilla JavaScript in the component's `<script>` tag —
no framework, no build pipeline, consistent with the rest of the site. It reads
the SSE stream and appends text deltas as they arrive.

## Testing

Tests are written before the implementation and by a separate agent invocation,
per the project's testing constraints. The Anthropic client is mocked throughout;
no test spends money.

**Unit** — corpus assembly (frontmatter stripped, URLs correct, deterministic
ordering); the schema tool (every one of the 33 files renders without raising, the
enum list is sorted and byte-stable across calls, an unknown config file name
returns an error result rather than throwing); history validation (over-long
transcripts truncated, `system` roles rejected, malformed payloads produce 400);
rate limiter and budget accounting at their boundaries; SSE frame formatting.

**Integration** — the full request path per auth state: no cookie → 401;
expired token → 401; valid token without the `streamflow` group → 403; valid token
with the group → 200 and a stream. Mismatched `Origin` → 403. Rate limit and
budget exhaustion → 429. An Anthropic API error → a clean error frame, not a 500.

One integration test earns a specific mention: **a request arriving at `/api/chat`
with no cookie must be rejected.** That is the regression test for the
`protect_app()` `/api/` exemption described above. If a future refactor reaches for
`protect_app()`, this test is what catches it before the endpoint goes public with
an API key behind it.

The tool loop gets integration coverage against a mocked API that returns
`stop_reason: "tool_use"`: the lookup runs, its result is appended, the loop
continues, and the client sees one uninterrupted stream. A mocked API that asks
for tools forever must stop at the 3-call cap rather than spinning.

Budget accounting gets its own unit coverage against a synthetic `usage` object:
each of the four token classes must be multiplied by its own rate, so a response
dominated by `cache_creation_input_tokens` costs roughly twenty times one dominated
by `cache_read_input_tokens`. A test that only exercised cached reads would pass
against an implementation that summed raw tokens and let cache writes through
unpriced.

**Property** — arbitrary client-supplied history always yields either a valid
request to the mocked API or a 400, never an unhandled exception.

**Mutation** — run by an independent evaluator, not by the implementing agent.

**UIX** — panel opens and closes, state persists across navigation, the signed-out
state shows the sign-in link, streaming text renders incrementally, and every
error state shows a readable message.

## Deployment

New systemd unit `fewsdocs-chat.service` running gunicorn on `127.0.0.1:8057`
(verified free — 8050–8056 are taken) as the `fewsdocs` user, with the virtualenv
at `/home/fewsdocs/repo/chat/venv` per the site-directory convention — that path
sits inside the repository, so `chat/venv/` and `chat/__pycache__/` are added to
`.gitignore`. Secrets
(`ANTHROPIC_API_KEY`, `JWT_SECRET`) come from an `EnvironmentFile` at
`/home/fewsdocs/chat.env`, mode 600, owned by `fewsdocs`. Any value containing
`$` must be quoted or systemd will mangle it.

nginx gains one location block in the site's vhost:

```nginx
location /api/chat {
    proxy_pass http://127.0.0.1:8057;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

**This must be added through the CloudPanel vhost editor**, not by hand-editing
the file. CloudPanel regenerates the vhost whenever the site is saved in its UI,
which is exactly what happened to the two `error_page` lines already documented
in `CLAUDE.md`. Adding it in the UI is what makes it survive.

No new cron jobs, so no scheduling conflicts to resolve.

Updated redeploy runbook:

```bash
sudo -u fewsdocs git -C /home/fewsdocs/repo pull
sudo -u fewsdocs bash -c 'cd /home/fewsdocs/repo && npm ci && npm run build'
sudo -u fewsdocs bash -c 'cd /home/fewsdocs/repo/chat && venv/bin/pip install -r requirements.txt'
sudo systemctl restart fewsdocs-chat   # picks up the new corpus
```

## Blast radius

Worth stating plainly, since this is the first thing on the box that spends money
per request.

The service reads Markdown files and calls one external HTTPS API. It executes no
shell commands, spawns no subprocesses, writes only its own budget file, and
touches no database. A compromise of the service would expose the Anthropic API
key and the JWT signing secret — the latter is the more serious of the two, since
it would allow minting session tokens for every app on `streamflows.org`. That
risk already exists for every SSO-protected app here and is not increased by this
one, but it is the reason the environment file is mode 600 and the reason this
service must never gain the ability to run shell commands.

No user data is stored. Conversations live in the browser only. Usernames from the
JWT are used for rate-limit keying and are never written to logs, consistent with
the no-PII-in-logs rule.

## Out of scope

Multi-user conversation history or persistence; feedback and rating capture;
making the chat available to anonymous visitors; anything touching FEWS itself.
Each is a separate spec if wanted later.
