# Source: Signal

How Doppelganger parses a **Signal** chat export into the normalized message
stream that the [shared pipeline](../data-pipeline.md) consumes. This is the
only stage that knows anything Signal-specific. Everything downstream
(sessionizing, scanning, redaction, ShareGPT formatting) is source-agnostic.

Adapter: [`ingest/adapters/signal.py`](../../ingest/adapters/signal.py).
Use it with `--source signal`.

## Exporting your data

Signal has a built-in **Export chat history** (Android, Desktop and iOS, added
during 2026):

1. **Settings** → **Chats** → **Export chat history**.
2. Confirm, and choose **without media** (media isn't used, and the export stays
   small).
3. Pick a folder. Signal writes a `signal-export-<timestamp>/` folder there.

It exports **all chats at once**; you can't export a single conversation.
Disappearing and view-once messages are left out by Signal.

```
signal-export-2026-11-07-12-00-00/
├── metadata.json
├── main.jsonl        ← the file the adapter reads
└── files/            ← only if you exported with media
```

```bash
python -m ingest --source signal --input signal-export-2026-11-07-12-00-00/
```

You can also point `--input` at `main.jsonl` itself, or at the folder that
contains the export.

> ⚠️ **The export is unencrypted.** It holds your whole message history in
> plain text, readable by other apps on the device. Delete it once you've run
> ingest.

## A documented format

Unlike most chat exports, Signal's is defined by public code. `main.jsonl` is
Signal's backup format ([`backup.proto`](https://github.com/signalapp/libsignal/blob/main/rust/message-backup/src/proto/backup.proto)
in libsignal), written one frame per line with the Protobuf JSON mapping:

```json
{"version": "1", "backupTimeMs": "1715636551000"}
{"account": {"givenName": "Your", "familyName": "Name"}}
{"recipient": {"id": "1", "self": {}}}
{"recipient": {"id": "4", "contact": {"profileGivenName": "Alice"}}}
{"chat": {"id": "1", "recipientId": "4"}}
{"chatItem": {"chatId": "1", "authorId": "4", "dateSent": "1700000000000",
              "incoming": {}, "standardMessage": {"text": {"body": "hi"}}}}
```

## What the adapter does

- **Knows who "you" are automatically.** Signal marks your own recipient and
  your outgoing messages, so `--self-name` isn't needed. If you pass it, it's
  only used as your display name in the output.
- **Names people the way the app does:** your nickname for them, then your
  contact-book name, then their Signal profile name, then username or number.
  Group chats use the group title.
- **Keeps reply threading.** Signal identifies a message by its author and
  sent time, and quotes point at that pair. The adapter sets `message_id` and
  `reply_to_id` from it, so the pipeline can stitch replies together across
  time gaps (as it does for Telegram).
- **Resolves @mentions** to `@Name` instead of leaving a placeholder character.
- **Uses the latest version of edited messages.**
- **Skips everything that isn't text you wrote:** stickers, calls, group and
  safety-number updates, deleted messages, media with no caption, payments.
- **Skips unreadable lines** with a count instead of failing.

## Limits

- **Very long messages** may be truncated: Signal stores the overflow of long
  texts as a separate file, and the adapter uses only the inline part.
- **Not checked against a real export yet.** The format comes from Signal's
  source code and libsignal's test cases rather than a real user export. The
  schema can grow new fields, which the adapter ignores.

## 1:1 vs. group chats

As with every source, **1:1 chats give the best results**. Group chats work,
and `--multi-speaker` keeps each other sender labelled. See
[#46](https://github.com/NotYuSheng/Doppelganger/issues/46).

## Output

A flat list of `NormalizedMessage`
([`ingest/message.py`](../../ingest/message.py)):

```python
NormalizedMessage(
    chat_id,         # "<contact or group name> (<chat id>)"
    timestamp,       # unix seconds (from dateSent)
    sender_id,       # display name
    sender_is_self,  # True for your messages (from the export itself)
    text,            # message body, mentions resolved
    message_id,      # "<authorId>:<dateSent>"
    reply_to_id,     # the quoted message's id, or None
)
```

## Signal-relevant flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--source` | `telegram` | Set to `signal` to select this adapter |
| `--input` | `./data/result.json` | The `signal-export-<timestamp>/` folder (or its `main.jsonl`) |
| `--self-name` | from the export | Only changes how your name is shown |
| `--multi-speaker` | off | In group chats, keep and label each non-self sender |

## Privacy

`signal-export-*/` is covered by `.gitignore` and the pre-commit hook.
