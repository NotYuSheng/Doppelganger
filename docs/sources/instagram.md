# Source: Instagram

How Doppelganger parses an **Instagram DM** export into the normalized message
stream that the [shared pipeline](../data-pipeline.md) consumes. This is the
only stage that knows anything Instagram-specific. Everything downstream
(sessionizing, scanning, redaction, ShareGPT formatting) is source-agnostic.

Adapter: [`ingest/adapters/instagram.py`](../../ingest/adapters/instagram.py),
which delegates to the shared Meta parser in
[`ingest/adapters/meta.py`](../../ingest/adapters/meta.py) (Facebook Messenger
uses the same export format). Use it with `--source instagram`.

## Exporting your data

Instagram's official **Download your information** tool exports your DMs, with
both sides of every conversation:

1. Instagram → **Settings** → **Accounts Center** → **Your information and
   permissions** → **Download your information**.
2. Choose **Some of your information** → tick **Messages** only.
3. Choose **Download to device**, date range **All time**, format **JSON**
   (not HTML, which is often the default). Media quality can be **Low**, since
   media isn't used.
4. Instagram emails you when it's ready, usually within hours and at most about
   48h. Download the zip and **unzip it**.

You'll get a folder like this (names vary slightly between export versions):

```
instagram-yourname-2026-11-07-abc123/
├── your_instagram_activity/messages/inbox/
│   ├── alice_1234567890/
│   │   └── message_1.json      ← one folder per conversation
│   └── bob_9876543210/
│       ├── message_1.json      ← long threads are split into several files
│       └── message_2.json
└── personal_information/personal_information/personal_information.json
```

Point the pipeline at the **unzipped folder**:

```bash
python -m ingest --source instagram --input instagram-yourname-2026-11-07-abc123/
```

You can also point `--input` at any folder inside it (e.g. `.../messages/inbox`),
one conversation folder, or a single `message_1.json`.

## What the adapter does

- **Finds every conversation** by walking for `message_N.json` files, and merges
  split threads (`message_1.json`, `message_2.json`, …) back into one.
- **Skips message requests and spam-filtered threads** (`message_requests/`,
  `filtered_threads/`). These are usually strangers, not conversations you took
  part in.
- **Restores chronological order.** Meta lists messages newest first.
- **Repairs garbled text.** Meta's JSON stores non-ASCII text (emoji, accents,
  CJK) as mojibake, e.g. `ðŸ‘` for 👍. Every message and name is decoded back
  to the real characters.
- **Drops non-text events:** reactions, likes, "sent an attachment", shared
  posts/stories with no caption, unsent messages, calls, and group-membership
  notices.
- **Works out who "you" are,** in this order:
  1. `--self-name`, if given;
  2. your Name or Username from `personal_information.json`;
  3. the one participant who appears in every conversation.

  If none of these settles it (for example, an export with a single
  conversation and no profile file), the adapter stops and lists the sender
  names it found, so you can pass `--self-name`.

## What Instagram *doesn't* give us

- **No message ids or reply links.** `message_id` / `reply_to_id` are always
  `None`, so conversations are grouped by **time alone**, the documented
  fallback in [`ingest/message.py`](../../ingest/message.py).
- **Display names only.** Senders are identified by display name, so two
  people with the same name in one thread can't be told apart.
- **An undocumented format.** Meta doesn't publish a schema. The field names
  the adapter relies on (`participants`, `messages`, `sender_name`,
  `timestamp_ms`, `content`, `is_unsent`) come from community parsers. Entries
  in a shape the adapter doesn't recognise are counted and skipped, not guessed
  at.

## 1:1 vs. group chats

As with every source, **1:1 chats give the best results**. Group threads work,
and `--multi-speaker` keeps each other sender labelled. See
[#46](https://github.com/NotYuSheng/Doppelganger/issues/46) for the group-chat
data-quality caveat.

## Output

A flat list of `NormalizedMessage`
([`ingest/message.py`](../../ingest/message.py)):

```python
NormalizedMessage(
    chat_id,         # conversation folder name, e.g. "alice_1234567890"
    timestamp,       # unix seconds (from timestamp_ms)
    sender_id,       # sender display name
    sender_is_self,  # True if sender is you (see "Works out who 'you' are")
    text,            # plain-text content, mojibake repaired
    message_id,      # always None
    reply_to_id,     # always None
)
```

From here the [shared pipeline](../data-pipeline.md) takes over.

## Instagram-relevant flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--source` | `telegram` | Set to `instagram` to select this adapter |
| `--input` | `./data/result.json` | The unzipped export folder (or a folder/file inside it) |
| `--self-name` | auto | Your display name, if auto-detection can't work it out |
| `--multi-speaker` | off | In group chats, keep and label each non-self sender |

## Privacy

The unzipped export, its zip, and `your_instagram_activity/` are covered by
`.gitignore` and the pre-commit hook, so they can't be committed by accident.
