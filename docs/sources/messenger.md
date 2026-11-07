# Source: Facebook Messenger

How Doppelganger parses **Facebook Messenger** exports into the normalized
message stream that the [shared pipeline](../data-pipeline.md) consumes. This is
the only stage that knows anything Messenger-specific. Everything downstream
(sessionizing, scanning, redaction, ShareGPT formatting) is source-agnostic.

Adapter: [`ingest/adapters/messenger.py`](../../ingest/adapters/messenger.py),
which delegates to the shared Meta parser in
[`ingest/adapters/meta.py`](../../ingest/adapters/meta.py) (Instagram uses it
too). Use it with `--source messenger`.

## Exporting your data: you need two downloads

Since late 2023, most Messenger chats are **end-to-end encrypted** (you'll see a
lock icon or an "end-to-end encrypted" banner in the chat). Those chats are
**not** included in Facebook's normal data download, so getting your full
history takes two downloads:

**1. Download Your Information** (older, non-encrypted chats)

1. Facebook → **Settings & privacy** → **Settings** → **Accounts Center** →
   **Your information and permissions** → **Download your information**.
2. Choose **Some of your information** → tick **Messages** only.
3. Choose **Download to device**, date range **All time**, format **JSON**
   (not HTML). Media quality can be **Low**.
4. Facebook notifies you when it's ready. Download the zip.

**2. Download secure storage data** (end-to-end encrypted chats)

1. On a **computer**, open Messenger or facebook.com/messages.
2. **Settings** → **Privacy & safety** → **End-to-end encrypted chats** →
   **Message storage** → **Download secure storage data**.
3. This only works if **secure storage** is turned on for your encrypted chats.
   Chats from before you turned it on may not be available.

**Unzip both into one folder** and point the pipeline at it:

```
messenger-export/
├── facebook-yourname-2026-11-07-abc123/          ← download 1
│   └── your_facebook_activity/messages/
│       ├── inbox/alice_1234567890/message_1.json
│       ├── archived_threads/…
│       └── e2ee_cutover/…                       ← chats from before they switched to encryption
└── secure-storage/                              ← download 2
    └── your_activity_across_facebook/messages/
        ├── alice_1.json                         ← one file per encrypted chat
        └── media/
```

```bash
python -m ingest --source messenger --input messenger-export/
```

You can also point `--input` at just one of the two downloads, any folder
inside them, or a single conversation file. If you only use download 1, **most
recent chats will be missing**.

## What the adapter does

- **Reads both export shapes.** Download 1 uses per-conversation folders of
  `message_N.json` (`sender_name`, `timestamp_ms`, `content`). Download 2 uses
  one flat file per chat (`senderName`, `timestamp`, `text`, `isUnsent`).
  Other JSON files in the exports (settings, profile) are ignored.
- **Includes inbox, archived and e2ee_cutover threads;** skips message requests
  and spam-filtered threads.
- **Removes the overlap.** A chat that switched to encryption can appear in both
  downloads, so exact duplicates (same sender, millisecond and text) are kept
  only once.
- **Restores chronological order,** merging split threads (`message_1.json`,
  `message_2.json`, …, `message_10.json`).
- **Repairs garbled text.** Meta stores emoji and non-English text as mojibake
  (`ðŸ‘` for 👍); every message and name is decoded back.
- **Drops non-text events:** stickers, photos/videos with no caption, calls,
  shared links with no message, unsent messages, reactions, people joining or
  leaving the chat.
- **Skips unreadable files** with a warning instead of failing the whole run.
- **Works out who "you" are,** in this order:
  1. `--self-name`, if given;
  2. your name from `profile_information.json` (download 1);
  3. the one participant who appears in every conversation.

  If none of these settles it, the adapter stops and lists the sender names it
  found, so you can pass `--self-name`.

## What Messenger *doesn't* give us

- **No message ids or reply links.** `message_id` / `reply_to_id` are always
  `None`, so conversations are grouped by **time alone**.
- **Display names only.** Two people with the same name in one chat can't be
  told apart.
- **An undocumented format.** Meta publishes no schema. The field names come
  from community parsers and public sample exports. Entries in a shape the
  adapter doesn't recognise are counted and skipped.
- **Possibly incomplete encrypted history.** Secure storage only holds what was
  backed up, and Meta is changing how encrypted-chat backups are restored
  (moving from Google Drive keys to a PIN after December 2026).

## 1:1 vs. group chats

As with every source, **1:1 chats give the best results**. In a group chat
most of your messages aren't replies to the message just before them, so the
samples are noisier. `--group-chats focus` keeps only the stretches where you and
one other person talk directly, and `--group-chats skip` leaves group chats
out. `--multi-speaker` keeps each other sender labelled (`Bob: ...`). See
[group chats](../data-pipeline.md#group-chats).

## Output

A flat list of `NormalizedMessage`
([`ingest/message.py`](../../ingest/message.py)):

```python
NormalizedMessage(
    chat_id,         # DYI folder name ("alice_1234567890") or secure-storage file name ("alice_1")
    timestamp,       # unix seconds
    sender_id,       # sender display name
    sender_is_self,  # True if sender is you
    text,            # plain-text content, mojibake repaired
    message_id,      # always None
    reply_to_id,     # always None
)
```

The two downloads use different chat ids for the same person, so one
conversation that spans the switch to encryption is treated as two chats. Only
a conversation gap right at the switch is affected.

## Messenger-relevant flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--source` | `telegram` | Set to `messenger` to select this adapter |
| `--input` | `./data/result.json` | Folder containing one or both unzipped downloads |
| `--self-name` | auto | Your display name, if auto-detection can't work it out |
| `--multi-speaker` | off | In group chats, keep and label each non-self sender |

## Privacy

`facebook-*/`, `facebook-*.zip`, `your_facebook_activity/` and
`your_activity_across_facebook/` are covered by `.gitignore` and the pre-commit
hook. The secure-storage download holds your **decrypted** encrypted chats, so
delete it once you've ingested it.
