"""User-agent classification: device type, browser, OS and the bot flag (PRD F4).

Parsing uses `ua-parser` (the maintained regex database; the older `user-agents` package was last
released in 2020, so it is not used). On top of it sit two small rules of ours:

BOT DETECTION is best effort and says so. A client is a bot if
  - ua-parser files it as a "Spider" (Googlebot, bingbot, Slackbot, facebookexternalhit, ...), or
  - the string follows the bot conventions the database misses: a standalone word `bot`,
    `crawler`, `spider` or `scraper`, `xxxbot/1.0`, or a contact URL `+http://...`; or
  - it is an HTTP library or automation tool rather than a browser (curl, python-requests, Go,
    Java, okhttp, headless Chrome, uptime monitors, ...); or
  - there is no User-Agent at all (browsers always send one).
Bots are STORED (`is_bot = true`) but never counted. Detection is imperfect by nature: a bot that
pretends to be Chrome counts as a visitor, and an odd browser can be flagged.
The regexes are deliberately anchored on whole words: `bot` inside "CUBOT" (a phone brand) must
not make a phone a bot.

DEVICE TYPE (D29): desktop | mobile | tablet | other.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from ua_parser import parse


@dataclass(frozen=True, slots=True)
class UAInfo:
    device_type: str | None  # desktop | mobile | tablet | other (D29)
    browser: str | None
    os: str | None
    is_bot: bool


_BOT_WORDS = re.compile(
    r"""
    \b(?:bot|crawler|crawl|spider|scraper|slurp)\b   # a standalone word
    | [a-z]bot/\d                                    # Xbot/1.0, Googlebot/2.1
    | \(\s*\+?https?://|;\s*\+https?://              # the contact-URL convention of crawlers
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Clients that are tools, not people. Anchored at the start where the tool IS the whole UA.
_TOOLS = re.compile(
    r"""
    ^(?:curl|wget|python-requests|python-urllib|python/|httpx|aiohttp|go-http-client|java/|
        okhttp|libwww-perl|scrapy|postman|insomnia|node-fetch|axios|k6/|apache-httpclient|
        php/|ruby)
    | headlesschrome | phantomjs | puppeteer | playwright | selenium | lighthouse
    | pingdom | uptimerobot | statuscake | datadog | newrelic | uptime
    | mediapartners | yandex(?!browser)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DESKTOP_OS = {"Windows", "Mac OS X", "Linux", "Chrome OS"}
_LINUX_FAMILIES = {
    "Ubuntu",
    "Fedora",
    "Debian",
    "CentOS",
    "Red Hat",
    "Gentoo",
    "Arch Linux",
    "Linux Mint",
}


def _is_bot(raw: str, device_family: str | None) -> bool:
    return device_family == "Spider" or bool(_BOT_WORDS.search(raw)) or bool(_TOOLS.search(raw))


def _browser(family: str | None) -> str:
    """ "Mobile Safari" and "Safari" are the same browser to a person reading a chart."""
    if not family or family == "Other":
        return "Other"
    cleaned = re.sub(r"\b(?:Mobile|iOS|WebView)\b", " ", family)
    return " ".join(cleaned.split()) or "Other"


def _os(family: str | None) -> str:
    if not family or family == "Other":
        return "Other"
    if family == "Mac OS X":
        return "macOS"
    if family in _LINUX_FAMILIES:
        return "Linux"
    return family


def _device_type(
    raw: str, os_family: str | None, device_family: str | None, brand: str | None
) -> str:
    device = (device_family or "").lower()
    if device in {"iphone", "ipod"} or device == "generic smartphone":
        return "mobile"
    if device == "ipad" or "tablet" in device or "tablet" in (brand or "").lower():
        return "tablet"
    if os_family == "Android":
        # Android phones say "Mobile" in the UA; Android tablets do not.
        return "mobile" if re.search(r"\bMobile\b", raw) else "tablet"
    if os_family == "iOS":
        return "mobile"
    if os_family in _DESKTOP_OS or os_family in _LINUX_FAMILIES:
        return "desktop"
    return "other"  # consoles, TVs, unknown clients


@lru_cache(maxsize=4096)
def _classify(raw: str) -> UAInfo:
    parsed = parse(raw)
    os_family = parsed.os.family if parsed.os else None
    device_family = parsed.device.family if parsed.device else None
    brand = parsed.device.brand if parsed.device else None
    browser_family = parsed.user_agent.family if parsed.user_agent else None

    if _is_bot(raw, device_family):
        return UAInfo("other", _browser(browser_family), _os(os_family), True)
    return UAInfo(
        _device_type(raw, os_family, device_family, brand),
        _browser(browser_family),
        _os(os_family),
        False,
    )


def parse_user_agent(raw: str | None) -> UAInfo:
    """Classify one User-Agent string. The same strings repeat constantly, hence the cache."""
    if raw is None or not raw.strip():
        return UAInfo("other", None, None, True)  # no User-Agent: not a browser
    return _classify(raw)
