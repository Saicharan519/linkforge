"""User-agent classification (task P3.4): sample strings, bot rules, device types."""

import pytest

from app.worker.ua import UAInfo, parse_user_agent

DESKTOP = "desktop"
MOBILE = "mobile"
TABLET = "tablet"
OTHER = "other"

# (name, user agent, device_type, browser, os, is_bot)
HUMANS = [
    (
        "chrome-windows",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36",
        DESKTOP,
        "Chrome",
        "Windows",
    ),
    (
        "chrome-mac",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36",
        DESKTOP,
        "Chrome",
        "macOS",
    ),
    (
        "firefox-linux",
        "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
        DESKTOP,
        "Firefox",
        "Linux",
    ),
    (
        "firefox-ubuntu-is-linux",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
        DESKTOP,
        "Firefox",
        "Linux",
    ),
    (
        "safari-mac",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15",
        DESKTOP,
        "Safari",
        "macOS",
    ),
    (
        "edge-windows",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
        DESKTOP,
        "Edge",
        "Windows",
    ),
    (
        "iphone-safari",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        MOBILE,
        "Safari",
        "iOS",
    ),
    (
        "iphone-chrome",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) CriOS/130.0.0.0 Mobile/15E148 Safari/604.1",
        MOBILE,
        "Chrome",
        "iOS",
    ),
    (
        "ipad-safari",
        "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Mobile/15E148 Safari/604.1",
        TABLET,
        "Safari",
        "iOS",
    ),
    (
        "android-phone-chrome",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Mobile Safari/537.36",
        MOBILE,
        "Chrome",
        "Android",
    ),
    (
        "android-tablet-has-no-mobile-token",
        "Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36",
        TABLET,
        "Chrome",
        "Android",
    ),
    (
        # A KNOWN phone model whose UA has no "Mobile" token: the usual rule says tablet.
        "android-known-model-without-mobile-token",
        "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
        "SamsungBrowser/25.0 Chrome/121.0.0.0 Safari/537.36",
        TABLET,
        "Samsung Internet",
        "Android",
    ),
    (
        "samsung-internet",
        "Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
        "SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36",
        MOBILE,
        "Samsung Internet",
        "Android",
    ),
    (
        "firefox-android",
        "Mozilla/5.0 (Android 14; Mobile; rv:130.0) Gecko/130.0 Firefox/130.0",
        MOBILE,
        "Firefox",
        "Android",
    ),
    (
        # "bot" hides inside the phone brand CUBOT: it must NOT make a person a bot.
        "cubot-phone-is-not-a-bot",
        "Mozilla/5.0 (Linux; Android 12; CUBOT X30) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/110.0.0.0 Mobile Safari/537.36",
        MOBILE,
        "Chrome",
        "Android",
    ),
    (
        "playstation-is-other",
        "Mozilla/5.0 (PlayStation; PlayStation 5/2.26) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/13.0 Safari/605.1.15",
        OTHER,
        "Safari",
        "Other",
    ),
    (
        "smart-tv-is-other",
        "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) "
        "85.0.4183.93/6.5 TV Safari/537.36",
        OTHER,
        "Safari",
        "Tizen",
    ),
]

BOTS = [
    ("googlebot", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    (
        "googlebot-smartphone",
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; "
        "+http://www.google.com/bot.html)",
    ),
    ("bingbot", "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"),
    (
        "facebook-preview",
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    ),
    ("slack-link-expander", "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)"),
    ("unknown-crawler-by-convention", "SomeNewEngine/1.0 (+https://example.org/about-our-crawler)"),
    ("standalone-word-bot", "Mozilla/5.0 (compatible; bot)"),
    # Known to ua-parser as spiders, invisible to our own regexes (no bot word, no URL):
    ("baiduspider-by-database-only", "Baiduspider/2.0"),
    ("ia-archiver-by-database-only", "ia_archiver"),
    ("seznambot-by-database-only", "SeznamBot"),
    # Unknown to the database, caught by our contact-URL convention:
    (
        "contact-url-convention-only",
        "Mozilla/5.0 (compatible; MyThing; +https://mything.example/info)",
    ),
    ("adsense-crawler", "Mediapartners-Google"),
    ("yandex-images", "YandexImages/3.0"),
    ("curl", "curl/8.5.0"),
    ("wget", "Wget/1.21.4"),
    ("python-requests", "python-requests/2.32.3"),
    ("python-urllib", "Python-urllib/3.12"),
    ("go-http-client", "Go-http-client/2.0"),
    ("java", "Java/17.0.2"),
    ("okhttp", "okhttp/4.12.0"),
    ("k6", "k6/0.54.0 (https://k6.io/)"),
    ("axios", "axios/1.7.2"),
    (
        "headless-chrome",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "HeadlessChrome/130.0.0.0 Safari/537.36",
    ),
    ("lighthouse", "Mozilla/5.0 Chrome-Lighthouse"),
    ("uptime-monitor", "Mozilla/5.0 (compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)"),
]


@pytest.mark.parametrize(
    ("name", "ua", "device", "browser", "os"), HUMANS, ids=[h[0] for h in HUMANS]
)
def test_people_are_classified_and_never_flagged_as_bots(
    name: str, ua: str, device: str, browser: str, os: str
) -> None:
    info = parse_user_agent(ua)
    assert info == UAInfo(device, browser, os, False), name


@pytest.mark.parametrize(("name", "ua"), BOTS, ids=[b[0] for b in BOTS])
def test_bots_and_tools_are_flagged(name: str, ua: str) -> None:
    info = parse_user_agent(ua)
    assert info.is_bot is True, name
    assert info.device_type == OTHER  # a bot is never counted as a desktop or phone


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
def test_no_user_agent_at_all_is_treated_as_a_bot(raw: str | None) -> None:
    # Browsers always send one; a missing header is a script.
    assert parse_user_agent(raw) == UAInfo(OTHER, None, None, True)


def test_an_unknown_but_harmless_string_is_a_non_bot_with_unknown_parts() -> None:
    info = parse_user_agent("zzz-not-a-user-agent")
    assert info == UAInfo(OTHER, "Other", "Other", False)


def test_browser_names_ignore_the_mobile_variant() -> None:
    # "Mobile Safari", "Chrome Mobile", "Chrome Mobile iOS", "Firefox Mobile" read as one
    # browser each in a chart.
    mobile_safari = parse_user_agent(HUMANS[6][1]).browser
    desktop_safari = parse_user_agent(HUMANS[4][1]).browser
    assert mobile_safari == desktop_safari == "Safari"
    assert parse_user_agent(HUMANS[7][1]).browser == "Chrome"


def test_a_long_user_agent_is_still_classified() -> None:
    ua = HUMANS[0][1] + " " + "x" * 400  # the API truncates at 512 characters
    assert parse_user_agent(ua).is_bot is False


def test_the_same_string_gives_the_same_answer_every_time() -> None:
    ua = HUMANS[2][1]
    assert parse_user_agent(ua) is parse_user_agent(ua)  # cached: repeats are free
