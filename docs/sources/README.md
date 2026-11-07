# Exporting your chats

Doppelganger trains on chat exports you download from each app yourself. Every
app below has an official export. Pick the ones you use; you can
[combine several](#combining-apps) into one dataset.

| App | How to export | Then run |
|---|---|---|
| [Telegram](telegram.md) | **Telegram Desktop** → Settings → Advanced → *Export Telegram Data*, format **JSON** | `python -m ingest --source telegram --input path/to/result.json` |
| [WhatsApp](whatsapp.md) | In a chat: ⋮ → More → *Export chat* → **Without media** (one chat per file) | `python -m ingest --source whatsapp --input "WhatsApp Chat with Alice.txt" --self-name "Your Name"` |
| [Instagram](instagram.md) | Accounts Center → Your information and permissions → *Download your information* → **Messages**, format **JSON** | `python -m ingest --source instagram --input path/to/instagram-export/` |
| [Messenger](messenger.md) | **Two** downloads: *Download your information* (Messages, JSON) **and** *Download secure storage data* for encrypted chats; unzip both into one folder | `python -m ingest --source messenger --input path/to/folder/` |
| [LINE](line.md) | In a chat: menu → Settings → *Export chat history* (one chat per file) | `python -m ingest --source line --input "[LINE] Chat history with Alice.txt"` |
| [Signal](signal.md) | Settings → Chats → *Export chat history*, **without media** | `python -m ingest --source signal --input signal-export-<timestamp>/` |
| [KakaoTalk](kakaotalk.md) | In a chat room: settings → *Export Messages* → **Send Text Only** (one chat per file) | `python -m ingest --source kakaotalk --input KakaoTalkChats.txt` |

Each app's page has the full steps, what the export looks like, and what the
adapter does with it.

## Things to know

- **Telegram is the default.** `setup.sh` and a plain `python -m ingest` look for
  `data/result.json`. Telegram exports unzip to a dated folder like
  `DataExport_2025-07-09/result.json`, so copy that file to `data/result.json`,
  or pass `--input` with its path.
- **Who "you" are.** Telegram, Signal and Instagram/Messenger usually work this
  out themselves. WhatsApp always needs `--self-name "Your Name"` (exactly as it
  appears in the chat). LINE and KakaoTalk can tell in 1:1 chats, but need it for
  group chats. If an app can't tell, ingest stops and lists the names it found.
- **Export without media.** Photos and videos aren't used; they only make the
  export bigger.
- **Keep exports out of git.** The usual export file and folder names for every
  app are already in `.gitignore` and the pre-commit hook. If you rename an
  export, keep it inside an ignored folder.
- **Group chats** make noisier training data than 1:1 chats. See
  `--group-chats` in the [pipeline docs](../data-pipeline.md#group-chats).

## No exports, or gaps in them?

Chat history covers whatever conversations happened to take place. [Reply
drills](../drills.md) fill the gaps: the app shows realistic incoming messages
(advice, disagreements, apologies, bad news, …) and you type how you'd reply.
Answers are saved to `data/elicited.jsonl` and picked up like any other export.

## Combining apps

Most people's chats are spread across apps. Pass one `--input` per export,
prefixed with its app, and set `--self-name` per app where needed:

```bash
python -m ingest \
  --input telegram:data/result.json \
  --input "whatsapp:data/WhatsApp Chat with Alice.txt" \
  --input signal:data/signal-export-2026-11-07/ \
  --self-name "whatsapp:Your Name"
```

You get one combined dataset and a summary of what each export contributed.

## Apps we don't support

Some apps have no legitimate way to export both sides of a conversation (WeChat,
QQ, Discord, Snapchat, SMS, Zalo). The reasons are recorded in
[`docs/adr/`](../adr/).
