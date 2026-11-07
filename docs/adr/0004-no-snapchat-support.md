# ADR 0004: Do not support Snapchat as a chat source

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Snapchat has roughly 900M monthly users. Its official **My Data** export
(Settings → My Data) includes a `chat_history.json`, but that file only holds
messages that **you or the other person explicitly saved** in the chat. Snapchat
deletes unsaved chats after they are viewed, so they cannot be exported by any
legitimate means. Some sources also report that the file shows mostly your own
side of the conversation.

Under the bar set in [ADR 0003](0003-support-whatsapp.md) (a legitimate export
path **and** both sides of the conversation), the export path is fine, but the
data is a small, self-selected sample of saved messages. It cannot be relied on
to give the back-and-forth that training pairs need.

## Decision

Doppelganger will **not** support Snapchat. There will be no adapter for My Data
`chat_history.json`.

## Revisit when

Snapchat's official export includes ordinary (unsaved) conversation history with
both participants' messages.

## Consequences

- Snapchat users cannot use their Snapchat history.
- We avoid producing a dataset that looks complete but is really a sparse,
  biased sample of saved messages.

## References

- [Redact: How to download your Snapchat data](https://redact.dev/blog/snapchat-data-download)
- [Mosaic: How to back up & export Snapchat chats](https://www.mosaicchats.com/blog/how-to-export-snapchat-chat)
