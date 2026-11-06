"""User-agent classification: device type, browser, OS and the bot flag.

PLACEHOLDER (task P3.2): this only fixes the interface the worker depends on. It classifies
nothing yet, so every click is stored as "not a bot" with no device, browser or OS. The real
parser (ua-parser plus a documented bot regex) arrives in task P3.4 and replaces the body of
`parse_user_agent`; nothing else has to change.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UAInfo:
    device_type: str | None  # desktop | mobile | tablet | other (D29)
    browser: str | None
    os: str | None
    is_bot: bool


def parse_user_agent(raw: str | None) -> UAInfo:
    return UAInfo(device_type=None, browser=None, os=None, is_bot=False)
