"""Helper for sending requests to local integrations."""

import json
from typing import Callable


class _Request:
    """
    Request to send to local integrations with
    optional callback after successful send.
    """

    body: dict
    completed_callback: Callable | None = None

    def __init__(
        self, body: dict, completed_callback: Callable | None = None
    ) -> None:
        """Initialize request with body and optional completed callback."""
        self.body = body
        self.completed_callback = completed_callback

    def get_body_str(self) -> str:
        """Get string representation of request."""
        return json.dumps(self.body)

    def get_type(self) -> str | None:
        """Get the type of the request."""
        return self.body.get("type")

    def complete_callback(self):
        """Call the completed request callback if applicable."""
        if self.completed_callback is not None:
            self.completed_callback()
