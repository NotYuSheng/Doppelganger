# Source: LINE

How Doppelganger parses a **LINE** chat export into the normalized message
stream that the [shared pipeline](../data-pipeline.md) consumes. This is the
only stage that knows anything LINE-specific. Everything downstream
(sessionizing, scanning, redaction, ShareGPT formatting) is source-agnostic.

Adapter: [`ingest/adapters/line.py`](../../ingest/adapters/line.py).
Use it with `--source line`.

## Exporting your data

LINE exports **one chat at a time** as a plain-text file, using its built-in
[Export chat history](https://help.line.me/line/android/?contentId=20007388):

- **Phone:** open the chat → menu (≡ or ⌄) → **Settings** → **Export chat
  history**, then save or share the `.txt` file.
- **PC/Mac:** the equivalent is **Save chat**, but it only saves the messages
  currently loaded in the window. Scroll to the top first, or use the phone.

The export is text only: photos, stickers and files become placeholders like
`[Photo]`, which the adapter drops. It looks like this:

```text
[LINE] Chat history with Alice
Saved on: 2026/03/08, 22:10

2026/01/25 Sun
07:53	Alice	morning!
07:54	Alice	[Sticker]
08:10	Your Name	"good morning
how did you sleep?"
08:13		Alice unsent a message.
```

Japanese-language exports use the same structure with a Japanese header and
date lines (`[LINE] Aliceとのトーク履歴`, `保存日時：…`, `2026/01/25(日)`).

```bash
python -m ingest --source line --input "[LINE] Chat history with Alice.txt"
```

## What the adapter does

- **Parses the tab-separated layout:** date lines set the day, and each
  `time<TAB>sender<TAB>text` line is one message.
- **Handles date and time variants:** `2026/01/25 Sun`, `2018/07/10(火)`,
  `2025.12.30 火曜日`, `Sun, 01/25/2026`; 24-hour times and 12-hour ones
  marked with `AM`/`PM` or `午前`/`午後`. It also copes with CRLF line endings
  and a byte-order mark.
- **Reassembles multi-line messages.** LINE wraps them in double quotes; the
  quotes are removed and the lines joined.
- **Skips system notices** (empty sender column, joins/leaves, unsent
  messages) and **drops placeholders** (`[Sticker]`, `[Photo]`, `[スタンプ]`,
  calls `☎ …`, polls).
- **Works out who "you" are** for 1:1 chats: the header names the other
  person, so when the chat has exactly two senders, the one not named is you.
  For group chats (or if that doesn't work), pass `--self-name` with your
  name exactly as it appears in the chat. The adapter stops and lists the
  sender names if it can't tell.

## What LINE *doesn't* give us

- **No message ids or reply metadata.** `message_id` / `reply_to_id` are always
  `None`, so conversations are grouped by **time alone**.
- **Minute precision only.** Timestamps have no seconds.
- **One conversation per file.** The file name is used as the `chat_id`.
- **An undocumented format.** LINE doesn't publish the layout. It was modelled
  on public samples and community parsers, and may vary by app version and
  language. If no messages are found, the adapter says so instead of writing an
  empty dataset.

## 1:1 vs. group chats

As with every source, **1:1 chats give the best results**. Group chats work
with `--self-name`, and `--multi-speaker` keeps each other sender labelled.
See [#46](https://github.com/NotYuSheng/Doppelganger/issues/46).

## Output

A flat list of `NormalizedMessage`
([`ingest/message.py`](../../ingest/message.py)):

```python
NormalizedMessage(
    chat_id,         # the export's file name
    timestamp,       # unix seconds (minute precision)
    sender_id,       # sender display name
    sender_is_self,  # True if sender is you
    text,            # plain-text content (placeholders dropped)
    message_id,      # always None
    reply_to_id,     # always None
)
```

## LINE-relevant flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--source` | `telegram` | Set to `line` to select this adapter |
| `--input` | `./data/result.json` | Path to the exported `.txt` file |
| `--self-name` | auto (1:1 chats) | Your display name; required for group chats |
| `--multi-speaker` | off | In group chats, keep and label each non-self sender |

## Privacy

Files named `[LINE]*` are covered by `.gitignore` and the pre-commit hook.
**Keep that prefix if you rename the export**: other `.txt` names aren't
ignored.
