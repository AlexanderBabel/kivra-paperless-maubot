"""Pure helpers, stdlib only, so they can be tested without maubot installed."""
import base64
import hashlib
import json
import re
from html.parser import HTMLParser


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verifying it (we only read our own user id)."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def safe_name(s: str) -> str:
    return re.sub(r"[^\w.\- ]+", "_", s).strip(" _") or "unknown"


def doc_filename(date: str, sender: str, key: str, part: int | None, ext: str) -> str:
    # The Kivra key in the filename is what dedupe searches for (original_filename).
    suffix = f"_part{part}" if part is not None else ""
    return f"{date}_{safe_name(sender)}_{key}{suffix}.{ext}"


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    # ponytail: Paperless without Tika can't ingest HTML, so HTML-only letters become
    # plain text (searchable, layout lost). Enable Tika/Gotenberg and upload .html to keep it.
    p = _Text()
    p.feed(html)
    lines = (" ".join(l.split()) for l in "".join(p.out).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def retry_after(header: str | None, default: float, cap: float = 120) -> float:
    """Seconds to wait for a 429. Only the delta-seconds form; an HTTP-date falls back to default."""
    try:
        return min(max(float(header), 1), cap)
    except (TypeError, ValueError):
        return min(default, cap)


def map_name(name: str, rules: list[dict] | None) -> str:
    """First rule whose `match` regex matches the start of `name` renames it to `name`."""
    for rule in rules or []:
        if re.match(rule["match"], name):
            return rule["name"]
    return name
