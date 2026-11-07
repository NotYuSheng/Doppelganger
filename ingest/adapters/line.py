"""LINE chat-export (``.txt``) adapter.

Parses the file produced by LINE's per-chat **Export chat history** (chat →
menu → Settings → Export chat history) into normalized messages. The layout,
as seen in public samples and community parsers (LINE doesn't document it)::

    [LINE] Chat history with Alice            ← header (JP: [LINE] Aliceとのトーク履歴)
    Saved on: 2026/03/08, 22:10               ← (JP: 保存日時：2026/03/08 22:10)

    2026/01/25 Sun                            ← date line (JP: 2026/01/25(日))
    07:53	Alice	[Sticker]                 ← time<TAB>sender<TAB>text
    20:07	You	"first line                   ← multi-line messages are quoted…
    second line"                              ← …and end with a closing quote
    20:09		Alice unsent a message.       ← system notice: empty sender

Like WhatsApp, the export has no message ids or reply metadata, and never
marks which sender is you. For a 1:1 chat the header names the *other* person,
so when the chat has exactly two senders the one not named in the header is
taken as you. Otherwise ``--self-name`` is required.
"""

import re
from datetime import datetime
from typing import List, Optional, Tuple

from ingest.adapters.base import register
from ingest.adapters.textexport import hour_24, normalize_line, resolve_self, to_messages
from ingest.message import NormalizedMessage

# Header: "[LINE] Chat history with Alice" / "[LINE] Aliceとのトーク履歴" /
# "[LINE] Chat history in <group>".
_HEADER_EN = re.compile(r"^\[LINE\]\s*Chat history (?:with|in) (?P<name>.+?)\s*$")
_HEADER_JP = re.compile(r"^\[LINE\]\s*(?P<name>.+?)\s*とのトーク履歴\s*$")
_HEADER_ANY = re.compile(r"^\[LINE\]")
_SAVED_ON = re.compile(r"^(Saved on|保存日時)\s*[:：]")

# Date lines: a Y/M/D date, optionally followed by a weekday in any language
# and any bracket style: "2026/01/25 Sun", "2018/07/10(火)", "2025.12.30 火曜日",
# "2026/01/25（日）". Also the month-first "Sun, 01/25/2026" seen in some
# English exports.
_DATE_YMD = re.compile(r"^(?P<y>\d{4})[./-](?P<m>\d{1,2})[./-](?P<d>\d{1,2})(?:\s*[(（]?[^\t\d]{0,12}[)）]?)?\s*$")
_DATE_MDY = re.compile(r"^(?:[A-Za-z]{3,9},?\s+)?(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})\s*$")

# Message line: time<TAB>sender<TAB>text. The time may be 24h ("07:53") or carry
# an AM/PM / 午前/午後 marker before or after it.
_TIME = r"(?P<ampm1>AM|PM|午前|午後)?\s*(?P<h>\d{1,2}):(?P<min>\d{2})\s*(?P<ampm2>AM|PM)?"
_MSG = re.compile(r"^" + _TIME + r"\t(?P<sender>[^\t]*)\t(?P<text>.*)$")
# A timestamped line with only one tab is a system notice ("20:09\tX joined").
_SYSTEM = re.compile(r"^" + _TIME + r"\t[^\t]*$")

# Bodies LINE writes for non-text content, in English and Japanese. Compared
# against the whole message.
_PLACEHOLDERS = {
    "[sticker]", "[photo]", "[video]", "[file]", "[gif]", "[voice message]",
    "[audio]", "[contact]", "[location]", "[album]", "[note]", "[poll]",
    "[スタンプ]", "[写真]", "[動画]", "[ファイル]", "[ボイスメッセージ]",
    "[連絡先]", "[位置情報]", "[アルバム]", "[ノート]", "[投票]",
    "スタンプ", "写真", "動画",
}
_SYSTEM_TEXT = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^☎",                                   # calls: "☎ Call time 0:42", "☎ 不在着信"
        r"unsent a message\.?$",
        r"メッセージの送信を取り消しました$",
        r"^\[poll\]",
        r"^\[投票\]",
    )
]


def _clean(text: str) -> str:
    t = text.strip()
    if t.lower() in _PLACEHOLDERS or any(p.search(t) for p in _SYSTEM_TEXT):
        return ""
    return t


def _parse_date(line: str) -> Optional[Tuple[int, int, int]]:
    for pat in (_DATE_YMD, _DATE_MDY):
        m = pat.match(line)
        if m:
            y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
            try:
                datetime(y, mo, d)
            except ValueError:
                return None
            return y, mo, d
    return None


def _header_partner(lines: List[str]) -> Optional[str]:
    """The chat partner's name from the header, for 1:1 chats."""
    for line in lines[:3]:
        for pat in (_HEADER_EN, _HEADER_JP):
            m = pat.match(line)
            if m:
                return m.group("name")
    return None


class LineAdapter:
    name = "line"

    def parse(
        self, path: str, *, self_name: Optional[str] = None
    ) -> List[NormalizedMessage]:
        # utf-8-sig drops a BOM if present; exports often use CRLF line ends.
        with open(path, encoding="utf-8-sig") as f:
            lines = [normalize_line(l.rstrip("\r\n")) for l in f]

        entries: List[list] = []  # [timestamp, sender, [text lines]]
        date: Optional[Tuple[int, int, int]] = None
        cur: Optional[list] = None
        in_quote = False
        for line in lines:
            if in_quote and cur is not None:
                # Inside a quoted multi-line message: everything up to the line
                # that ends with the closing quote belongs to it.
                if line.endswith('"'):
                    cur[2].append(line[:-1])
                    in_quote = False
                else:
                    cur[2].append(line)
                continue

            if _HEADER_ANY.match(line) or _SAVED_ON.match(line):
                continue
            parsed_date = _parse_date(line)
            if parsed_date:
                date, cur = parsed_date, None
                continue

            m = _MSG.match(line)
            if m and date is not None:
                ts = int(datetime(*date, hour_24(int(m.group("h")), m.group("ampm1") or m.group("ampm2")), int(m.group("min"))).timestamp())
                sender, text = m.group("sender").strip(), m.group("text")
                if not sender:
                    cur = None  # system notice with an empty sender column
                    continue
                if text.startswith('"') and not (len(text) > 1 and text.endswith('"')):
                    in_quote = True
                    text = text[1:]
                elif len(text) > 1 and text.startswith('"') and text.endswith('"'):
                    text = text[1:-1]
                cur = [ts, sender, [text]]
                entries.append(cur)
                continue
            if _SYSTEM.match(line):
                cur = None
                continue
            if line.strip() == "":
                cur = None
                continue
            if cur is not None:
                cur[2].append(line)  # unquoted wrapped line

        if not entries:
            raise ValueError(
                f"No LINE messages found in {path!r}. Expected the .txt from "
                "Export chat history (lines like '07:53<TAB>Name<TAB>text' "
                "under date lines like '2026/01/25 Sun')."
            )

        senders = sorted({e[1] for e in entries})
        me = resolve_self(self.name, senders, _header_partner(lines), self_name)
        return to_messages(self.name, path, entries, me, _clean)


register(LineAdapter())
