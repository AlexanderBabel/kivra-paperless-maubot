import json

from aiohttp import ClientSession, FormData


class PaperlessError(Exception):
    pass


class Paperless:
    def __init__(self, http: ClientSession, url: str, token: str):
        self.http, self.api = http, url.rstrip("/") + "/api"
        self.headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        self._ids: dict[tuple[str, str], int] = {}

    async def _get(self, path: str, **params) -> dict:
        async with self.http.get(f"{self.api}/{path}", params=params, headers=self.headers) as r:
            if r.status != 200:
                raise PaperlessError(f"GET {path}: HTTP {r.status}")
            return await r.json()

    async def id_for(self, kind: str, name: str) -> int:
        """Id of the tag/correspondent/document_type called `name`, created if missing."""
        if (kind, name) not in self._ids:
            found = (await self._get(f"{kind}/", name__iexact=name))["results"]
            if found:
                self._ids[kind, name] = found[0]["id"]
            else:
                # matching_algorithm 0 = none: don't let Paperless auto-assign it elsewhere.
                async with self.http.post(f"{self.api}/{kind}/", headers=self.headers,
                                          json={"name": name, "matching_algorithm": 0}) as r:
                    if r.status not in (200, 201):
                        raise PaperlessError(f"create {kind} {name!r}: HTTP {r.status}")
                    self._ids[kind, name] = (await r.json())["id"]
        return self._ids[kind, name]

    async def custom_field(self, name: str, value: str) -> dict[str, str] | None:
        """{field_id: value} for post_document, or None if the field doesn't exist.

        Select fields store the option id, so `value` is matched against option labels.
        """
        found = (await self._get("custom_fields/", name__iexact=name))["results"]
        if not found:
            return None
        field = found[0]
        if field["data_type"] == "select":
            opts = (field.get("extra_data") or {}).get("select_options") or []
            match = [o["id"] for o in opts if o["label"].lower() == value.lower()]
            if not match:
                raise PaperlessError(f"custom field {name!r} has no option {value!r}")
            value = match[0]
        return {str(field["id"]): value}

    async def has(self, kivra_key: str) -> bool:
        # ponytail: one search per letter; fine for a personal inbox (hundreds of letters).
        # Upgrade path: store keys in a custom field and fetch them all in one query.
        return (await self._get("documents/", original_filename__icontains=kivra_key,
                                page_size=1))["count"] > 0

    async def upload(self, filename: str, data: bytes, mime: str, *, title: str,
                     created: str, correspondent: int | None, document_type: int | None,
                     tags: list[int], custom_fields: dict[str, str] | None = None) -> None:
        form = FormData(quote_fields=False)  # else the filename arrives as "Telia%20AB"
        form.add_field("document", data, filename=filename, content_type=mime)
        form.add_field("title", title)
        if created:
            form.add_field("created", created)
        if correspondent:
            form.add_field("correspondent", str(correspondent))
        if document_type:
            form.add_field("document_type", str(document_type))
        for t in tags:
            form.add_field("tags", str(t))
        if custom_fields:
            form.add_field("custom_fields", json.dumps(custom_fields))
        async with self.http.post(f"{self.api}/documents/post_document/", data=form,
                                  headers=self.headers) as r:
            if r.status not in (200, 201, 202):
                raise PaperlessError(f"upload {filename}: HTTP {r.status} {(await r.text())[:200]}")
