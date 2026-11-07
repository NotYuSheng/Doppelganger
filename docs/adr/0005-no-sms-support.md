# ADR 0005: Do not support SMS / Google Messages as a chat source

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

SMS/MMS/RCS is universal on phones, but neither Android nor Google Messages has
an **official export** to a readable file:

- The Google Messages backup goes to Google Drive and can only be restored to a
  phone. It cannot be read as a file.
- Google Takeout covers **Google Voice** texts only, not regular SMS.
- Readable exports come only from **third-party apps**, such as SMS Backup &
  Restore (XML) or browser extensions that scrape Google Messages for Web. Each
  has its own undocumented format.

(iMessage is a separate case: its messages sit in a plain local database on the
user's own Mac, and it is tracked separately.)

## Decision

Doppelganger will **not** support Android SMS / Google Messages. There will be
no adapter for third-party SMS backup formats.

## Revisit when

Google ships an official, readable SMS/RCS export (for example through Google
Takeout or Google One), or one third-party format becomes a stable, documented
de facto standard.

## Consequences

- Users cannot train on their SMS history.
- We do not tie ourselves to one third-party app's format, or recommend a
  particular app, among many competing ones.

## References

- [85ideas: How to export all text messages from Google Messages](https://85ideas.com/blog/how-to-export-all-text-messages-from-google-messages/)
- [Ultimate Systems: How to export text messages on Android](https://blog.usro.net/2024/11/how-to-export-text-messages-on-android/)
