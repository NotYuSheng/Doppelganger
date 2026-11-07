"""KakaoTalk chat-export adapter.

Parses the file from KakaoTalk's per-chat **Export Messages** (chat room →
settings → Export Messages → Send Text Only). The layout depends on the app
and its language; Kakao doesn't document any of them. These variants are
recognised (modelled on public parsers and their sample exports):

**Android / iOS** (``KakaoTalkChats.txt``): each message line starts with a
timestamp, then ``, Sender : text``::

    KakaoTalk Chats with Alice                 ← header (KO: Alice 님과 카카오톡 대화)
    Date Saved : Nov 7, 2026 at 10:00 AM       ← (KO: 저장한 날짜 : …)

    October 12, 2020 at 6:49 AM                ← date divider (timestamp alone)
    October 12, 2020 at 6:49 AM, Alice : hi    ← EN (also "October 12, 2020, 6:49 AM")
    2020년 10월 12일 오전 6:49, Alice : 안녕     ← Android KO
    2020. 10. 12. 오전 6:49, Alice : 안녕        ← iOS KO
    오전 6:49, 2020년 10월 12일, Alice : 안녕     ← older exports (time first)
    2020년 10월 12일 월요일                      ← KO date divider
    October 12, 2020 at 6:50 AM, Alice left this chatroom.   ← system (no " : ")

**Windows PC** (``.txt``): date dividers between dashes, then
``[Sender] [오후 2:30] text``.

**macOS** (``.csv``): ``Date,User,Message`` with ``2020-10-12 06:49:00`` dates.

Lines that match none of these continue the previous message (multi-line
messages). There are no message ids or reply links, and "you" isn't marked:
in a 1:1 chat the header names the other person, so with exactly two senders
the other one is taken as you; otherwise ``--self-name`` is required.
"""

import csv
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple

from ingest.adapters.base import register
from ingest.adapters.textexport import hour_24, normalize_line, resolve_self, to_messages
from ingest.message import NormalizedMessage

_MONTHS = ("January|February|March|April|May|June|July|August|September|"
           "October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
_MONTH_INDEX = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

# Timestamp prefixes; each captures y, mo, d, ampm, h, mi and the rest of the line.
_EN = re.compile(
    rf"^(?P<mon>{_MONTHS}) (?P<d>\d{{1,2}}), (?P<y>\d{{4}})(?: at|,) (?P<h>\d{{1,2}}):(?P<mi>\d{{2}})\s?(?P<ampm>AM|PM)?(?P<rest>.*)$"
)
_KO_ANDROID = re.compile(
    r"^(?P<y>\d{4})년 (?P<mo>\d{1,2})월 (?P<d>\d{1,2})일 (?P<ampm>오전|오후)? ?(?P<h>\d{1,2}):(?P<mi>\d{2})(?P<rest>.*)$"
)
_KO_IOS = re.compile(
    r"^(?P<y>\d{4})\. (?P<mo>\d{1,2})\. (?P<d>\d{1,2})\.? (?P<ampm>오전|오후)? ?(?P<h>\d{1,2}):(?P<mi>\d{2})(?P<rest>.*)$"
)
# Older exports put the time first: "오후 10:50, 2011년 11월 6일, Alice : 안녕".
_KO_TIME_FIRST = re.compile(
    r"^(?P<ampm>오전|오후) (?P<h>\d{1,2}):(?P<mi>\d{2}), (?P<y>\d{4})년 (?P<mo>\d{1,2})월 (?P<d>\d{1,2})일(?P<rest>.*)$"
)
_KO_DATE_DIVIDER = re.compile(r"^\d{4}년 \d{1,2}월 \d{1,2}일 [가-힣]+요일$")
# English date dividers: "Monday, October 12, 2020" (weekday optional).
_EN_DATE_DIVIDER = re.compile(rf"^(?:[A-Za-z]+, )?(?:{_MONTHS}) \d{{1,2}}, \d{{4}}$")
_PC_DATE = re.compile(r"^-{5,}\s*(?P<y>\d{4})년 (?P<mo>\d{1,2})월 (?P<d>\d{1,2})일.*-{5,}$")
_PC_DATE_EN = re.compile(
    rf"^-{{5,}}\s*(?:[A-Za-z]+, )?(?P<mon>{_MONTHS}) (?P<d>\d{{1,2}}), (?P<y>\d{{4}})\s*-{{5,}}$"
)
_PC_MSG = re.compile(
    r"^\[(?P<sender>.+?)\] \[(?P<ampm>오전|오후)? ?(?P<h>\d{1,2}):(?P<mi>\d{2})\s?(?P<ampm2>AM|PM)?\] (?P<text>.*)$"
)

_HEADERS = [
    re.compile(r"^KakaoTalk Chats with (?P<name>.+?)\s*$"),
    re.compile(r"^(?P<name>.+?) 님과 카카오톡 대화\s*$"),
    re.compile(r"^KakaoTalk Chats (?P<name>.+?)(?:\s*\(\d+\))?\s*$"),
]
_DATE_SAVED = re.compile(r"^(Date Saved|저장한 날짜)\s*:")

# Bodies KakaoTalk writes for non-text content (whole-message match).
_PLACEHOLDERS = {
    "photo", "video", "emoticon", "emoticons", "voice note", "file", "audio",
    "contact", "map", "live talk", "sticker",
    "사진", "이모티콘", "동영상", "음성메시지", "음성 메시지", "파일", "연락처",
    "지도", "보이스톡", "페이스톡", "라이브톡",
    "the message has been deleted.", "this message was deleted.",
    "삭제된 메시지입니다.", "메시지가 삭제되었습니다.",
}
_MEDIA_COUNT = re.compile(r"^(\d+ (photos|videos|files)|사진 \d+장|동영상 \d+개)$", re.IGNORECASE)
# Attachments exported "with media" appear as a bare file name.
_ATTACHMENT = re.compile(r"^\S+\.(jpe?g|png|gif|webp|heic|mp4|mov|m4a|aac|opus|pdf)$", re.IGNORECASE)
_CALLS = re.compile(r"^(voice ?talk|face ?talk|보이스톡|페이스톡).*(ended|cancel|missed|해요|종료|취소|부재중)", re.IGNORECASE)


def _prefix(line: str) -> Optional[Tuple[int, str]]:
    """``(timestamp, rest)`` if the line starts with an Android/iOS timestamp."""
    for pat in (_EN, _KO_ANDROID, _KO_IOS, _KO_TIME_FIRST):
        m = pat.match(line)
        if not m:
            continue
        g = m.groupdict()
        month = _MONTH_INDEX[g["mon"][:3].lower()] if g.get("mon") else int(g["mo"])
        try:
            dt = datetime(int(g["y"]), month, int(g["d"]), hour_24(int(g["h"]), g["ampm"]), int(g["mi"]))
        except ValueError:
            return None
        return int(dt.timestamp()), g["rest"]
    return None


def _clean(text: str) -> str:
    t = text.strip()
    if t.lower() in _PLACEHOLDERS or _MEDIA_COUNT.match(t) or _CALLS.match(t) or _ATTACHMENT.match(t):
        return ""
    return t


def _read_lines(path: str) -> List[str]:
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [normalize_line(l) for l in text.split("\n")]


def _parse_txt(lines: List[str]) -> Tuple[List[list], Optional[str]]:
    """Return ``(entries, header partner)`` where an entry is [ts, sender, [lines]]."""
    entries: List[list] = []
    partner: Optional[str] = None
    cur: Optional[list] = None
    pc_date: Optional[Tuple[int, int, int]] = None
    for line in lines:
        if not line.strip():
            if cur is not None:
                cur[2].append("")  # blank line inside a multi-line message
            continue
        if not entries:
            header = next((m for m in (h.match(line) for h in _HEADERS) if m), None)
            if header:
                partner = partner or header.group("name")
                continue
            if _DATE_SAVED.match(line):
                continue

        pre = _prefix(line)
        if pre:
            ts, rest = pre
            if not rest.strip():
                cur = None
                continue  # date divider
            if rest.startswith(","):
                body = rest[1:].lstrip()
                cur = None
                if " : " in body:
                    sender, text = body.split(" : ", 1)
                    cur = [ts, sender.strip(), [text]]
                    entries.append(cur)
                continue  # with no " : ", it's a system notice (joined, left, ...)
            # A date/time followed by something other than ", " isn't a new
            # entry: it's a wrapped line of the current message that happens
            # to start with a date ("2020년 10월 12일 6:49에 만나자").

        if _KO_DATE_DIVIDER.match(line) or _EN_DATE_DIVIDER.match(line):
            cur = None
            continue
        m = _PC_DATE.match(line) or _PC_DATE_EN.match(line)
        if m:
            g = m.groupdict()
            month = _MONTH_INDEX[g["mon"][:3].lower()] if g.get("mon") else int(g["mo"])
            pc_date, cur = (int(g["y"]), month, int(g["d"])), None
            continue
        m = _PC_MSG.match(line)
        if m and pc_date:
            try:
                hour = hour_24(int(m["h"]), m["ampm"] or m["ampm2"])
                ts = int(datetime(*pc_date, hour, int(m["mi"])).timestamp())
            except ValueError:
                ts = None  # malformed time: keep the line as text instead of crashing
            if ts is not None:
                cur = [ts, m["sender"].strip(), [m["text"]]]
                entries.append(cur)
                continue

        if cur is not None:
            cur[2].append(line)  # continuation of a multi-line message
    return entries, partner


def _parse_csv(path: str) -> List[list]:
    entries: List[list] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            date, user, text = (row.get("Date") or "").strip(), (row.get("User") or "").strip(), row.get("Message") or ""
            if not date or not user:
                continue
            try:
                ts = int(datetime.fromisoformat(date.replace("T", " ")).timestamp())
            except ValueError:
                continue
            entries.append([ts, user, [text]])
    return entries


class KakaoTalkAdapter:
    name = "kakaotalk"

    def parse(
        self, path: str, *, self_name: Optional[str] = None
    ) -> List[NormalizedMessage]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{path!r} not found. Point --input at the exported .txt (or macOS .csv).")
        partner = None
        if path.lower().endswith(".csv"):
            entries = _parse_csv(path)
        else:
            entries, partner = _parse_txt(_read_lines(path))
        if not entries:
            raise ValueError(
                f"No KakaoTalk messages found in {path!r}. Expected the file from "
                "Export Messages → Send Text Only (KakaoTalkChats.txt), or the "
                "macOS .csv export."
            )

        senders = sorted({e[1] for e in entries})
        me = resolve_self(self.name, senders, partner, self_name)
        return to_messages(self.name, path, entries, me, _clean)


register(KakaoTalkAdapter())
