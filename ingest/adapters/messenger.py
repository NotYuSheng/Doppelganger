"""Facebook Messenger adapter.

Reads both Messenger downloads: Download Your Information (JSON format), and
the separate "Download secure storage data" for end-to-end encrypted chats.
All parsing lives in :mod:`ingest.adapters.meta`, shared with Instagram.
"""

from typing import List, Optional

from ingest.adapters.base import register
from ingest.adapters.meta import parse_export
from ingest.message import NormalizedMessage


class MessengerAdapter:
    name = "messenger"

    def parse(
        self, path: str, *, self_name: Optional[str] = None
    ) -> List[NormalizedMessage]:
        return parse_export(self.name, path, self_name=self_name)


register(MessengerAdapter())
