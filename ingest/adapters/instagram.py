"""Instagram DMs adapter (Meta "Download Your Information", JSON format).

All parsing lives in :mod:`ingest.adapters.meta`, which Facebook Messenger
shares, since both use the same per-thread ``message_N.json`` layout.
"""

from typing import List, Optional

from ingest.adapters.base import register
from ingest.adapters.meta import parse_export
from ingest.message import NormalizedMessage


class InstagramAdapter:
    name = "instagram"

    def parse(
        self, path: str, *, self_name: Optional[str] = None
    ) -> List[NormalizedMessage]:
        return parse_export(self.name, path, self_name=self_name)


register(InstagramAdapter())
