# ADR 0006: Do not support QQ as a chat source

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

QQ is Tencent's other large messenger, with roughly 550M monthly users, mostly
in China. We found **no official chat export** that produces a readable file.
Community exporters work the same way as the WeChat tools covered in
[ADR 0001](0001-no-wechat-support.md): they decrypt the client's local database.
Tencent's DMCA notices against WeChat export tools have also hit combined
QQ/WeChat projects.

## Decision

Doppelganger will **not** support QQ, for the same reasons as WeChat:

- no adapter,
- no key extraction or decryption code,
- no documentation pointing to third-party decryption tools.

## Revisit when

Tencent ships an official, readable QQ chat export, or an exporter appears that
Tencent sanctions or does not dispute and that needs no circumvention.

## Consequences

The same as ADR 0001: QQ users are not covered, and the repository stays clear
of DMCA risk.

## References

- [ADR 0001: Do not support WeChat](0001-no-wechat-support.md)
- [GitHub DMCA notice: Tencent, 2026-01-08](https://github.com/github/dmca/blob/master/2026/01/2026-01-08-tencent.md)
