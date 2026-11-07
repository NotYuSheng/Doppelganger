# Source: KakaoTalk

How Doppelganger parses a **KakaoTalk** chat export into the normalized message
stream that the [shared pipeline](../data-pipeline.md) consumes. This is the
only stage that knows anything KakaoTalk-specific. Everything downstream
(sessionizing, scanning, redaction, ShareGPT formatting) is source-agnostic.

Adapter: [`ingest/adapters/kakaotalk.py`](../../ingest/adapters/kakaotalk.py).
Use it with `--source kakaotalk`.

## Exporting your data

KakaoTalk exports **one chat room at a time** with its built-in **Export
Messages** feature:

- **Phone (Android / iPhone):** open the chat room → menu (≡) → settings (⚙) →
  **Export Messages** → **Send Text Only**. You get `KakaoTalkChats.txt`
  (sometimes inside a zip; unzip it first).
- **PC (Windows):** chat room → menu → **Save chat** (대화 내보내기). You get a
  `.txt`.
- **Mac:** chat room → **Export Chats** (대화 내보내기). You get a `.csv`.

Choose **text only**. The "save all" option stores media without linking it to
messages, and the adapter doesn't use media anyway.

```bash
python -m ingest --source kakaotalk --input KakaoTalkChats.txt
```

## Supported layouts

Kakao doesn't document the export format, and it differs by app and language.
The adapter recognises:

| App / language | Message line |
|---|---|
| Android/iOS, English | `October 12, 2020 at 6:49 AM, Alice : hi` (also `October 12, 2020, 6:49 AM, …`) |
| Android, Korean | `2020년 10월 12일 오전 6:49, Alice : 안녕` |
| iOS, Korean | `2020. 10. 12. 오전 6:49, Alice : 안녕` |
| Older exports, Korean | `오전 6:49, 2020년 10월 12일, Alice : 안녕` |
| Windows PC | `[Alice] [오전 6:49] 안녕` under `----- 2020년 10월 12일 월요일 -----` |
| Mac | CSV with `Date,User,Message` columns |

The header (`KakaoTalk Chats with Alice` / `Alice 님과 카카오톡 대화`) and the
"Date Saved" (`저장한 날짜`) line are skipped.

## What the adapter does

- **Reassembles multi-line messages.** Lines that don't start a new message are
  joined onto the previous one.
- **Skips system notices:** joins, leaves, invites (timestamped lines with no
  `Name : ` part) and date dividers.
- **Drops placeholders:** `Photo`/`사진`, `Emoticon`/`이모티콘`, videos, voice
  notes, files, `3 photos`/`사진 3장`, calls (보이스톡/페이스톡), deleted
  messages, and bare attachment file names (`사진.jpg`).
- **Copes with** CRLF line endings, a byte-order mark and the narrow no-break
  spaces some phones put before `AM`/`PM`.
- **Works out who "you" are** for 1:1 chats: the header names the other person,
  so when the chat has exactly two senders, the one not named is you. For group
  chats (whose header lists several names), the Mac CSV (no header), or if that
  doesn't work, pass `--self-name` with your name exactly as it appears in the
  chat. The adapter stops and lists the sender names if it can't tell.

## What KakaoTalk *doesn't* give us

- **No message ids or reply metadata.** `message_id` / `reply_to_id` are always
  `None`, so conversations are grouped by **time alone**.
- **Minute precision only** in the `.txt` layouts.
- **One chat room per file.** The file name is used as the `chat_id`.
- **An undocumented, varying format.** The layouts above come from community
  parsers and their sample exports. Exports in other languages (e.g. Japanese
  or Vietnamese app settings) may not be recognised; if no messages are found,
  the adapter says so instead of writing an empty dataset.

## 1:1 vs. group chats

As with every source, **1:1 chats give the best results**. In a group chat
most of your messages aren't replies to the message just before them, so the
samples are noisier. `--group-chats focus` keeps only the stretches where you and
one other person talk directly, and `--group-chats skip` leaves group chats
out. `--multi-speaker` keeps each other sender labelled (`Bob: ...`). Group chats need `--self-name`. See
[group chats](../data-pipeline.md#group-chats).

## Output

A flat list of `NormalizedMessage`
([`ingest/message.py`](../../ingest/message.py)):

```python
NormalizedMessage(
    chat_id,         # the export's file name
    timestamp,       # unix seconds
    sender_id,       # sender display name
    sender_is_self,  # True if sender is you
    text,            # plain-text content (placeholders dropped)
    message_id,      # always None
    reply_to_id,     # always None
)
```

## KakaoTalk-relevant flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--source` | `telegram` | Set to `kakaotalk` to select this adapter |
| `--input` | `./data/result.json` | The exported `.txt` (or Mac `.csv`) |
| `--self-name` | auto (1:1 chats) | Your display name; required for groups and the Mac CSV |
| `--multi-speaker` | off | In group chats, keep and label each non-self sender |

## Privacy

`KakaoTalkChats*.txt`, `KakaoTalk_*.txt`, `KakaoTalk_*.csv` and
`Kakaotalk_Chat_*` are covered by `.gitignore` and the pre-commit hook. Keep the
original file name, or store renamed exports somewhere ignored.
