# Source: WhatsApp

How Doppelganger parses a **WhatsApp** chat export into the normalized message
stream that the [shared pipeline](../data-pipeline.md) consumes. This is the
only stage that knows anything WhatsApp-specific; everything downstream
(sessionizing, scanning, redaction, ShareGPT formatting) is source-agnostic.

Adapter: [`ingest/adapters/whatsapp.py`](../../ingest/adapters/whatsapp.py).
Use it with `--source whatsapp`.

## Exporting your data

WhatsApp exports **one chat at a time** as a plain-text file:

- **Phone (Android):** open the chat → `⋮` → **More** → **Export chat** →
  **Without media**.
- **Phone (iPhone):** open the chat → tap the contact/group name →
  **Export Chat** → **Without Media**.

> Choose **Without media** — the media files aren't used (they'd just become
> `<Media omitted>` placeholders, which the adapter drops anyway) and the export
> stays small.

You'll get a single `.txt` file, e.g. `WhatsApp Chat with Alice.txt` (iPhone
zips it as `_chat.txt` inside a `.zip` — unzip first). It looks like this — one
message per line, each prefixed with a timestamp and the sender's name; a
message's own line breaks continue on the next line(s):

```text
26/01/2020, 4:19 pm - Messages and calls are end-to-end encrypted. ...
26/01/2020, 4:20 pm - Alice: are you coming this weekend?
26/01/2020, 4:20 pm - Alice: sis is asking
26/01/2020, 4:22 pm - Your Name: yeah, saturday
26/01/2020, 4:25 pm - Alice: <Media omitted>
```

The exact prefix varies by phone locale (date order, `/ . -` separators, 12- vs
24-hour clock, and iOS wrapping the whole prefix in `[...]`); the adapter
handles these variants — you don't need to reformat anything.

Point the pipeline at the file — and, importantly, tell it which name is you:

```bash
python -m ingest --source whatsapp \
  --input "WhatsApp Chat with Alice.txt" \
  --self-name "Your Name"
```

> **`--self-name` is effectively required for WhatsApp.** Unlike Telegram, a
> WhatsApp export never marks which participant is the owner — every message is
> just labelled with a contact name. Without `--self-name` the adapter can't
> tell which turns are yours, so it stops and lists the sender names it found so
> you can copy the right one. Use the name **exactly as it appears** in the chat.

## What the adapter does

- **Handles both export shapes.** iOS wraps the prefix in brackets
  (`[12/03/2024, 14:32:11] Alice: ...`); Android uses a dash
  (`12/03/2024, 14:32 - Alice: ...`). Both are recognised.
- **Tolerates locale timestamp formats.** Date order (D/M vs M/D), `/ . -`
  separators, 12- vs 24-hour clocks, and `AM`/`a.m.` markers are normalised
  before parsing. A prefix it genuinely can't parse is treated as a continuation
  line rather than crashing the run.
- **Reassembles multi-line messages.** A message with its own line breaks spans
  several text lines; only the first carries a timestamp, so the rest are joined
  back onto it.
- **Skips system notices.** The "Messages and calls are end-to-end encrypted"
  banner, group-created/added notices, etc. have a timestamp but no `Sender:` —
  they're not emitted as messages.
- **Drops media/omitted placeholders.** `<Media omitted>`, `This message was
  deleted`, and similar stand-ins become empty and are filtered out.
- **Tags who "you" are** from `--self-name`, so the shared pipeline knows which
  turns are yours (the ones the model learns to generate).

## What WhatsApp *doesn't* give us

- **No message ids and no reply metadata.** WhatsApp exports don't record which
  message a reply points at, so `message_id` / `reply_to_id` are always `None`
  and conversations are grouped by **time alone** — the documented fallback in
  [`ingest/message.py`](../../ingest/message.py). (Telegram, by contrast,
  provides reply links that stitch time-split conversations back together.)
- **One conversation per file.** Each export is a single chat; the file name is
  used as the `chat_id`. To train on several chats, export each and run the
  pipeline per file (or concatenate the outputs).

## 1:1 vs. group chats

Both work, but **1:1 chats give the best results** — every message from the other
person genuinely prompted your reply, so the user/assistant pairing is clean. In
a group chat the other participants collapse into a single "user" by default;
pass `--multi-speaker` to keep them labelled (`Bob: ...`). Group data is noisier
regardless (many of your messages aren't direct replies to the preceding one) —
this is a shared-pipeline limitation independent of WhatsApp, tracked in
[#46](https://github.com/NotYuSheng/Doppelganger/issues/46).

## Output

A flat list of `NormalizedMessage`
([`ingest/message.py`](../../ingest/message.py)):

```python
NormalizedMessage(
    chat_id,         # derived from the export file name
    timestamp,       # unix seconds (parsed from the line prefix)
    sender_id,       # sender display name
    sender_is_self,  # True if sender == --self-name
    text,            # plain-text content (media placeholders dropped)
    message_id,      # always None (WhatsApp has no ids)
    reply_to_id,     # always None (WhatsApp has no reply metadata)
)
```

From here the [shared pipeline](../data-pipeline.md) takes over.

## WhatsApp-relevant flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--source` | `telegram` | Set to `whatsapp` to select this adapter |
| `--input` | `./data/result.json` | Path to the exported `.txt` file |
| `--self-name` | auto | **Required for WhatsApp** — your display name exactly as it appears in the chat |
| `--multi-speaker` | off | In group chats, keep + label each non-self sender (`Bob: ...`) instead of collapsing the other side into one speaker |

All other flags (`--conversation-gap`, `--message-chain`, `--redact`, …) belong
to the shared pipeline — see [data-pipeline.md](../data-pipeline.md).
