# The ingestion pipeline (source-agnostic)

This document describes every transformation Doppelganger applies to turn a raw
chat export into a training-ready dataset. The pipeline is **source-agnostic**:
a per-platform *adapter* normalizes the export into a common message stream, and
**every stage after that is shared across all sources**.

The only source-specific step is stage 1 (the adapter). For how a particular
platform's export is parsed, see the per-source docs:

- [Telegram](sources/telegram.md) — supported today
- [WhatsApp](sources/whatsapp.md) — supported today
- Discord, … — planned; each drops in under [`docs/sources/`](sources/)

Entry point: [`python -m ingest`](../ingest/__main__.py) →
[`ingest/cli.py:main`](../ingest/cli.py). End-to-end flow:

```
export (platform-specific)
  │  (1) ADAPTER PARSE        ingest/adapters/<source>.py   ← source-specific
  ▼
NormalizedMessage stream     ← common interface; everything below is shared
  │  (2) BUILD SAMPLES        ingest/core.py
  │       a. split by silence gap
  │       b. stitch reply-linked splits
  │       c. assemble + merge turns
  ▼
role/text conversation samples
  │  (3) SENSITIVE-DATA SCAN  ingest/redactor.py  (+ optional LLM)
  │  (4) REDACTION APPLY      off | replace | drop
  │  (5) LLM QUALITY AUDIT    ingest/validator.py (optional)
  │  (6) SHAREGPT FORMAT      ingest/sharegpt.py
  ▼
data/chat_sharegpt.json  →  LLaMA-Factory SFT
```

---

## 1. Adapter parse — `ingest/adapters/<source>.py`

Each platform has one adapter that reads its native export and emits a common
**`NormalizedMessage`** stream ([`ingest/message.py`](../ingest/message.py)),
decoupling every downstream stage from any platform's specific schema. Whatever
the source, the adapter is responsible for:

- **identifying which sender is you** (tagging each message `sender_is_self`),
- **filtering non-messages** (system/service events, empty entries),
- **producing plain text** for each message, and
- **preserving reply + sender metadata** (`reply_to_id`, `sender_id`) for the
  sessionizing and group-chat stages below.

Output fields: `chat_id, timestamp, sender_id, sender_is_self, text,
message_id, reply_to_id`.

> Adding a new platform means writing **only** this adapter so it emits the same
> `NormalizedMessage` stream — stages 2–6 are unchanged. Document it under
> [`docs/sources/`](sources/). Telegram's specifics live in
> [sources/telegram.md](sources/telegram.md).

## 2. Build conversation samples — `ingest/core.py:build_samples`

Turns the flat message stream into multi-turn conversations. Three sub-steps:

**a. Split by silence gap** (`_split_into_conversations`)
Messages in a chat are cut into separate conversations wherever there's a
silence longer than `--conversation-gap` (default **3600s / 1h**). A quiet hour
is treated as a topic boundary.

**b. Stitch reply-linked splits** (`_merge_by_reply`)
A gap-split is undone when a later message *replies to* an earlier one (via the
adapter's `reply_to_id` metadata): those conversations are unioned back together
and re-sorted. This recovers slow threads that a pure time-gap would wrongly
split. (Sources without reply metadata simply skip this — it's a no-op.)

**c. Assemble + merge turns** (`_assemble_turns`)
Each message becomes a turn with role `user` (other people) or `assistant`
(you). Consecutive messages from the **same role** within `--message-chain`
(default **30s**) are merged into one turn (people send several quick texts as
one "turn"). Conversations with only one side are dropped — you need both a
`user` and an `assistant` turn to train on.

Group chats: by default the other side is collapsed into a single `user`
speaker. With **`--multi-speaker`**, each non-self sender keeps their identity
and their turns are labelled (`Bob: ...`); your own turns are never labelled.

**Output:** `Sample = List[{"role": "user"|"assistant", "text": str}]`.

## 3. Sensitive-data scan — `ingest/redactor.py`

A **non-destructive** pass that finds (but does not remove) personal/secret data
so you can review it before training. See [privacy](#privacy-notes).

- **Regex detectors** ([`ingest/redaction/`](../ingest/redaction/)): emails,
  phone numbers, payment cards (Luhn-checked), IP/MAC, API keys/tokens, plus
  pluggable country ID packs (`--redact-locales`, default `SG`). Universal
  patterns always run.
- **Writes `data/redaction_report.json`** — every finding with `conversation`,
  `turn`, `role`, `category`, `detector`, `severity`, and a masked `preview`.
  A summary table is printed to the terminal.
- **Optional LLM redaction** (`--llm-redact`): an OpenAI-compatible model flags
  context-dependent PII (names, secrets) that regex misses. **Local-first** —
  it refuses a hosted API unless `--allow-cloud-redaction` is set, so chat text
  never leaves your machine by default.
- Skip with `--skip-redact-scan` (or `--no-audit` to skip scan *and* validation).

## 4. Apply redaction — `ingest/redactor.py:apply`

Acts on the findings according to `--redact`:

| Mode | Effect |
|------|--------|
| `off` *(default)* | Scan + report only. Nothing changed. |
| `replace` | Swap each detected span for a `[CATEGORY]` placeholder. Keeps every conversation; removes the secret. |
| `drop` | Remove any conversation containing a detection. Smaller, more conservative dataset. |

`--redact` is honoured even if the scan was skipped, so the dataset can't
silently retain sensitive data you asked to remove.

## 5. LLM quality audit — `ingest/validator.py` (optional)

When enabled (`LLM_VALIDATE=true`, an OpenAI-compatible endpoint configured),
an LLM scores each conversation for coherence, quality, and human/assistant
pairing. It **drops weak samples** and can **split over-merged** ones into
cleaner conversations. Disable with `--skip-validation` or `--no-audit`.

## 6. ShareGPT format — `ingest/sharegpt.py:to_sharegpt`

Converts role/text samples into the exact ShareGPT shape LLaMA-Factory consumes
and writes `data/chat_sharegpt.json` (registered in
[`configs/dataset_info.json`](../configs/dataset_info.json)). Roles map
`user → human`, `assistant → gpt`.

**Crucially, each conversation is coerced into the structure LLaMA-Factory's
converter requires** (`_coerce_alternating`): it must **start with `human`**,
**strictly alternate** `human/gpt`, and **end with `gpt`** (even number of
turns). Raw chats break these rules all the time, so the converter:

- **merges consecutive same-speaker turns** (multi-speaker labels stay in the
  text), so alternation holds;
- **drops a leading `gpt` turn** (the other person messaged first — very common);
- **drops a trailing `human` turn** (so the sample ends on a trainable response).

Without this, LLaMA-Factory silently discards every non-conforming conversation
at train time (logging only `Invalid role tag` / `Invalid message count`
warnings) — on one real export that quietly cut **3,527 samples down to 997**.
With it, ~3,300 of those samples survive and the dataset's reported count matches
what actually trains. See issue
[#42](https://github.com/NotYuSheng/Doppelganger/issues/42).

> Loss is masked to your (`gpt`) turns only during SFT (`train_on_prompt: false`),
> so `human` turns — including multi-speaker labels — condition the model but are
> never themselves generated.

### Alternate output: JSONL

`--format jsonl` writes the intermediate role/text samples
(`data/chat_dataset.jsonl`, one conversation per line) instead — useful for
inspection or custom downstream processing. It does **not** apply the ShareGPT
coercion.

---

## Useful flags (quick reference)

| Flag | Default | Stage | Purpose |
|------|---------|-------|---------|
| `--source` | `telegram` | 1 | Which adapter parses the export |
| `--input` | `./data/result.json` | 1 | Path to the raw export |
| `--self-name` | auto | 1 | Override "which sender is you" |
| `--conversation-gap` | `3600` | 2a | Silence (s) that starts a new conversation |
| `--message-chain` | `30` | 2c | Max gap (s) to merge same-sender messages into one turn |
| `--multi-speaker` | off | 2c | Keep + label individual senders in group chats |
| `--redact` | `off` | 4 | `off` / `replace` / `drop` |
| `--redact-locales` | `SG` | 3 | Country ID packs for the scan |
| `--llm-redact` | off | 3 | LLM-assisted PII detection (local-first) |
| `--skip-redact-scan` | off | 3 | Skip the sensitive-data scan |
| `--skip-validation` | off | 5 | Skip the LLM quality audit |
| `--no-audit` | off | 3+5 | Skip scan *and* validation |
| `--format` | `sharegpt` | 6 | `sharegpt` (training) or `jsonl` (intermediate) |

## Privacy notes

The scan is a **safety net, not a guarantee** — regex and LLM detection both
miss real cases and raise false positives. Before training or sharing anything:
review `data/redaction_report.json` yourself, get consent from others in group
chats, and treat the dataset, `redaction_report.json` (which contains raw
values), trained adapters, and merged checkpoints all as sensitive. They are
gitignored by default; keep them that way.
