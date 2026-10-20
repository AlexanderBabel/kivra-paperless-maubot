"""Async Kivra client: BankID login (OAuth2 + PKCE) and letter download.

Endpoints and client id are what Kivra's own web inbox uses; reverse engineered
by https://github.com/felixandersen/kivra-sync. Kivra can change them any time.
"""
import asyncio
import json
import secrets

from aiohttp import ClientSession

from .util import jwt_claims, pkce_challenge, retry_after

API = "https://app.api.kivra.com"
GRAPHQL = "https://bff.kivra.com/graphql"
CLIENT_ID = "14085255171411300228f14dceae786da5a00285fe"  # Kivra web inbox, public
REDIRECT_URI = "https://inbox.kivra.com/auth/kivra/return"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")

LETTERS_QUERY = """
query ContentList($filter: ContentListFilter!, $senderKey: String, $take: Int!, $after: ID) {
  contents(filter: $filter, senderKey: $senderKey, take: $take, after: $after) {
    total
    existsMore
    list { key receivedAt subject sender { name } }
  }
}
"""

RECEIPTS_QUERY = """
query Receipts($search: String, $limit: Int, $offset: Int) {
  receiptsV2(search: $search, limit: $limit, offset: $offset) {
    total
    list { ... on ReceiptBaseDetails { key purchaseDate totalAmount { formatted } store { name } } }
  }
}
"""


class KivraError(Exception):
    pass


async def _json(resp, what: str, ok=(200,)) -> dict:
    if resp.status not in ok:
        raise KivraError(f"{what}: HTTP {resp.status}")
    return await resp.json(content_type=None)


class Login:
    """One BankID login attempt. Call poll() about once a second until it returns a Kivra."""

    def __init__(self, http: ClientSession, data: dict, verifier: str):
        self.http, self.verifier = http, verifier
        self.code = data["code"]
        self.qr: str = data["qr_code"]
        self.poll_url: str = data["next_poll_url"]
        # Present when Kivra hands out a same-device token; field name unconfirmed.
        self.autostart: str | None = data.get("auto_start_token") or data.get("autostart_token")

    @classmethod
    async def start(cls, http: ClientSession) -> "Login":
        await (await http.get("https://app.kivra.com/")).release()  # session cookies
        verifier = secrets.token_urlsafe(32)
        body = {
            "response_type": "bankid_all",
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "scope": "openid profile",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
        }
        async with http.post(f"{API}/v2/oauth2/authorize", json=body) as r:
            data = await _json(r, "authorize", ok=(201, 202))
        if not data.get("qr_code") or not data.get("code"):
            raise KivraError(f"authorize: unexpected response fields {sorted(data)}")
        return cls(http, data, verifier)

    async def poll(self) -> "Kivra | None":
        async with self.http.get(API + self.poll_url) as r:
            data = await _json(r, "poll")
        status = data.get("status")
        if status == "pending":
            self.qr = data.get("qr_code") or self.qr  # animated QR rotates every second
            self.poll_url = data.get("next_poll_url") or self.poll_url
            return None
        if status != "complete":
            raise KivraError(f"BankID login {status or 'failed'}")
        body = {
            "grant_type": "authorization_code",
            "code": self.code,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": self.verifier,
        }
        async with self.http.post(f"{API}/v2/oauth2/token", json=body) as r:
            tok = await _json(r, "token")
        actor = jwt_claims(tok["id_token"]).get("kivra_user_id")
        if not actor:
            raise KivraError("token: no kivra_user_id in id_token")
        return Kivra(self.http, tok["access_token"], actor)


class Kivra:
    def __init__(self, http: ClientSession, token: str, actor: str):
        self.http, self.token, self.actor = http, token, actor

    async def _gql(self, op: str, query: str, variables: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Actor-Key": self.actor,
            "X-Actor-Type": "user",
            "X-Session-Actor": f"user_{self.actor}",
            "X-Kivra-Environment": "production",
            "Origin": "https://inbox.kivra.com",
            "Referer": "https://inbox.kivra.com/",
        }
        body = {"operationName": op, "query": query, "variables": variables}
        async with self.http.post(GRAPHQL, json=body, headers=headers) as r:
            data = await _json(r, op)
        if data.get("errors"):
            raise KivraError(f"{op}: {data['errors'][0].get('message')}")
        return data["data"]

    async def letters(self) -> list[dict]:
        out, after = [], None
        while True:
            page = (await self._gql("ContentList", LETTERS_QUERY, {
                "filter": "inbox", "senderKey": None, "take": 100, "after": after}))["contents"]
            out += page["list"]
            if not page["existsMore"] or not page["list"]:
                return out
            after = page["list"][-1]["key"]

    async def receipts(self) -> list[dict]:
        # ponytail: one page, same as Kivra's own web app; paginate on offset past 20k receipts.
        data = await self._gql("Receipts", RECEIPTS_QUERY, {"search": None, "limit": 20000, "offset": 0})
        return data["receiptsV2"]["list"]

    async def content(self, key: str) -> dict:
        return json.loads(await self._get(f"{API}/v1/content/{key}", "content"))

    async def file(self, key: str, file_key: str) -> bytes:
        return await self._get(f"{API}/v1/content/{key}/file/{file_key}/raw", "file")

    async def receipt_pdf(self, key: str) -> bytes:
        # Kivra renders the structured receipt to a PDF server-side.
        return await self._get(f"{API}/v1/user/{self.actor}/receipts/{key}", "receipt",
                               Accept="application/pdf")

    async def _get(self, url: str, what: str, **headers) -> bytes:
        # Kivra rate-limits per-document downloads (429 after ~100 receipts in a burst).
        wait = 0
        for attempt in range(6):
            await asyncio.sleep(wait)
            async with self.http.get(url, headers={**self._auth, **headers}) as r:
                if r.status == 200:
                    return await r.read()
                if r.status != 429:
                    raise KivraError(f"{what}: HTTP {r.status}")
                wait = retry_after(r.headers.get("Retry-After"), default=5 * 2 ** attempt)
        raise KivraError(f"{what}: still rate limited after retries")

    @property
    def _auth(self) -> dict:
        return {"Authorization": f"token {self.token}"}
