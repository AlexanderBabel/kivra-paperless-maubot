import asyncio
import secrets
import time
from dataclasses import dataclass

from aiohttp import ClientSession, ClientTimeout
from aiohttp.web import Request, Response, json_response
from maubot import MessageEvent, Plugin
from maubot.handlers import command, web
from mautrix.types import EventID, Format, MessageType, RoomID, TextMessageEventContent
from mautrix.util import markdown
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

from .kivra import UA, Kivra, KivraError, Login
from .paperless import Paperless
from .util import doc_filename, html_to_text, map_name

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Kivra BankID login</title>
<style>
  body { font-family: system-ui, sans-serif; background: #fff; color: #111;
         display: flex; flex-direction: column; align-items: center; padding: 2rem 1rem; }
  #qr { padding: 16px; background: #fff; }
  a.btn { display: inline-block; margin-top: 1rem; padding: .8rem 1.4rem; border-radius: 8px;
          background: #193e4f; color: #fff; text-decoration: none; }
  @media (prefers-color-scheme: dark) { body { background: #111; color: #eee; } }
</style></head><body>
<h1>Kivra → Paperless</h1>
<p id="msg">Open the BankID app and scan the code.</p>
<div id="qr"></div>
<a id="open" class="btn" hidden>Open BankID on this device</a>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
  const qr = new QRCode(document.getElementById("qr"), { width: 256, height: 256 });
  const msg = document.getElementById("msg"), open = document.getElementById("open");
  async function tick() {
    let s;
    try { s = await (await fetch(location.pathname + "/state", { cache: "no-store" })).json(); }
    catch { return setTimeout(tick, 2000); }
    if (s.error) { msg.textContent = "This login link has expired. Run !kivra sync again."; qr.clear(); return; }
    if (s.done) { msg.textContent = "Logged in. Follow the progress in Matrix."; qr.clear(); open.hidden = true; return; }
    qr.makeCode(s.qr);  // BankID's animated QR changes every second
    if (s.autostart) {
      open.href = "https://app.bankid.com/?autostarttoken=" + encodeURIComponent(s.autostart) + "&redirect=null";
      open.hidden = false;
    }
    setTimeout(tick, 1000);
  }
  tick();
</script></body></html>
"""


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        for key in ("allowed_users", "paperless.url", "paperless.token", "paperless.tags",
                    "paperless.document_type",
                    "paperless.receipt_document_type", "paperless.source_field",
                    "paperless.source", "paperless.correspondents", "login_timeout"):
            helper.copy(key)


@dataclass
class Run:
    """What every upload in one sync shares."""
    p: Paperless
    kivra: Kivra
    tags: list[int]
    source: dict | None
    rules: list[dict]

    async def put(self, filename: str, data: bytes, mime: str, *, title: str, created: str,
                  sender: str, doc_type: int | None) -> bool:
        """Upload unless Paperless already has these exact bytes. True if uploaded."""
        if await self.p.has_checksum(data):
            return False
        correspondent = await self.p.id_for("correspondents", map_name(sender, self.rules))
        await self.p.upload(filename, data, mime, title=title, created=created,
                            correspondent=correspondent, document_type=doc_type,
                            tags=self.tags, custom_fields=self.source)
        return True


class Sync:
    def __init__(self, room: RoomID):
        self.room = room
        self.token = secrets.token_urlsafe(24)  # capability for the QR page
        self.login: Login | None = None
        self.event: EventID | None = None
        self.task: asyncio.Task | None = None
        self.status = "starting"
        self.last_edit = 0.0


class KivraPaperlessBot(Plugin):
    # ponytail: one sync at a time for the whole bot; per-user syncs if several people use it.
    sync: Sync | None = None

    @classmethod
    def get_config_class(cls) -> type[BaseProxyConfig]:
        return Config

    async def start(self) -> None:
        self.config.load_and_update()
        self.sync = None

    async def stop(self) -> None:
        if self.sync and self.sync.task:
            self.sync.task.cancel()

    @command.new("kivra", require_subcommand=True, help="Sync Kivra letters and receipts into Paperless")
    async def kivra(self, evt: MessageEvent) -> None:
        pass

    @kivra.subcommand("sync", help="Log in with BankID and import new letters and receipts")
    async def cmd_sync(self, evt: MessageEvent) -> None:
        if evt.sender not in self.config["allowed_users"]:
            await evt.reply("You are not allowed to run Kivra syncs.")
            return
        if self.sync:
            await evt.reply(f"A sync is already running: {self.sync.status}")
            return
        s = self.sync = Sync(evt.room_id)
        s.event = await self._send(s.room, "⏳ Starting Kivra login…")
        s.task = asyncio.create_task(self._run(s))

    @kivra.subcommand("status", help="Show the running sync")
    async def cmd_status(self, evt: MessageEvent) -> None:
        await evt.reply(self.sync.status if self.sync else "No sync running.")

    @kivra.subcommand("cancel", help="Cancel the running sync")
    async def cmd_cancel(self, evt: MessageEvent) -> None:
        if evt.sender not in self.config["allowed_users"]:
            return
        if self.sync and self.sync.task:
            self.sync.task.cancel()
        else:
            await evt.reply("No sync running.")

    @web.get("/login/{token}")
    async def login_page(self, req: Request) -> Response:
        return Response(text=PAGE, content_type="text/html", headers={"Cache-Control": "no-store"})

    @web.get("/login/{token}/state")
    async def login_state(self, req: Request) -> Response:
        s = self.sync
        if not s or not secrets.compare_digest(req.match_info["token"], s.token):
            return json_response({"error": "expired"}, status=404)
        if not s.login:
            return json_response({"done": True})
        return json_response({"qr": s.login.qr, "autostart": s.login.autostart},
                             headers={"Cache-Control": "no-store"})

    async def _send(self, room: RoomID, text: str, edit: EventID | None = None) -> EventID:
        content = TextMessageEventContent(msgtype=MessageType.NOTICE, body=text,
                                          format=Format.HTML, formatted_body=markdown.render(text))
        if edit:
            content.set_edit(edit)
        return await self.client.send_message(room, content)

    async def _status(self, s: Sync, text: str, force: bool = False) -> None:
        s.status = text
        # Progress edits are throttled; every edit is a new event in the room's history.
        if force or time.monotonic() - s.last_edit > 5:
            s.last_edit = time.monotonic()
            await self._send(s.room, text, edit=s.event)

    async def _run(self, s: Sync) -> None:
        try:
            async with ClientSession(timeout=ClientTimeout(total=120),
                                     headers={"User-Agent": UA}) as http:
                kivra = await self._login(s, http)
                await self._import(s, http, kivra)
        except asyncio.CancelledError:
            await self._status(s, "🛑 Sync cancelled.", force=True)
        except Exception as e:
            self.log.exception("Kivra sync failed")
            await self._status(s, f"❌ Sync failed: {e}", force=True)
        finally:
            self.sync = None

    async def _login(self, s: Sync, http: ClientSession) -> Kivra:
        s.login = await Login.start(http)
        url = f"{str(self.webapp_url).rstrip('/')}/login/{s.token}"
        text = f"🔐 **Log in to Kivra with BankID**\n\nScan the live QR code: {url}"
        if s.login.autostart:
            text += (f"\n\nOn this phone: [open BankID](https://app.bankid.com/?autostarttoken="
                     f"{s.login.autostart}&redirect=null)")
        await self._status(s, text, force=True)
        deadline = time.monotonic() + self.config["login_timeout"]
        while (kivra := await s.login.poll()) is None:
            if time.monotonic() > deadline:
                raise KivraError("BankID login timed out")
            await asyncio.sleep(1)
        s.login = None
        return kivra

    async def _import(self, s: Sync, http: ClientSession, kivra: Kivra) -> None:
        cfg = self.config
        p = Paperless(http, cfg["paperless.url"], cfg["paperless.token"])
        tags = [await p.id_for("tags", t) for t in cfg["paperless.tags"] or []]

        async def doc_type(key: str) -> int | None:
            return await p.id_for("document_types", cfg[key]) if cfg[key] else None

        source = None
        if cfg["paperless.source_field"]:
            source = await p.custom_field(cfg["paperless.source_field"], cfg["paperless.source"])
            if source is None:
                self.log.warning(f"Paperless has no custom field {cfg['paperless.source_field']!r}")
        letter_type = await doc_type("paperless.document_type")
        receipt_type = await doc_type("paperless.receipt_document_type")

        run = Run(p, kivra, tags, source, cfg["paperless.correspondents"] or [])
        pending = await p.pending_filenames()

        await self._status(s, "✅ Logged in. Listing letters and receipts…", force=True)
        letters, receipts = await kivra.letters(), await kivra.receipts()
        items = [(self._import_letter, x, letter_type) for x in letters] + \
                [(self._import_receipt, x, receipt_type) for x in receipts]
        new = skipped = failed = 0
        seen: set[str] = set()
        for i, (importer, item, dtype) in enumerate(items, 1):
            await self._status(s, f"📬 {i}/{len(items)} ({len(letters)} letters, {len(receipts)} "
                                  f"receipts) · {new} new · {skipped} already in Paperless · "
                                  f"{failed} failed")
            key = item["key"]
            try:
                if (key in seen or any(key in f for f in pending) or await p.has(key)):
                    skipped += 1
                    continue
                seen.add(key)
                if await importer(run, item, dtype):
                    new += 1
                else:
                    skipped += 1  # every file was already in Paperless by checksum
            except Exception as e:
                failed += 1
                self.log.warning(f"Kivra item {key} failed: {e}")
        icon = "⚠️" if failed else "🎉"
        await self._status(s, f"{icon} Kivra sync done: {new} new, {skipped} already in "
                              f"Paperless, {failed} failed (of {len(letters)} letters, "
                              f"{len(receipts)} receipts).", force=True)

    @staticmethod
    async def _import_receipt(run: Run, receipt: dict, doc_type: int | None) -> int:
        key = receipt["key"]
        date = (receipt.get("purchaseDate") or "")[:10]
        store = (receipt.get("store") or {}).get("name") or "Kivra"
        amount = (receipt.get("totalAmount") or {}).get("formatted")
        title = f"Kvitto {store}" + (f" {amount}" if amount else "")
        return await run.put(doc_filename(date, store, key, None, "pdf"),
                             await run.kivra.receipt_pdf(key), "application/pdf", title=title,
                             created=date, sender=store, doc_type=doc_type)

    @staticmethod
    async def _import_letter(run: Run, letter: dict, doc_type: int | None) -> int:
        key = letter["key"]
        date = (letter.get("receivedAt") or "")[:10]
        sender = (letter.get("sender") or {}).get("name") or "Kivra"
        subject = letter.get("subject") or sender
        parts = (await run.kivra.content(key)).get("parts") or []
        types = [part.get("content_type") for part in parts]
        if "application/pdf" in types:
            # The text part next to a PDF is Kivra's cover note ("Du har fått ett
            # meddelande från …"); the PDF is the letter.
            parts = [part for part in parts if part.get("content_type") == "application/pdf"]
        files = []
        for part in parts:
            ctype = part.get("content_type")
            if ctype == "application/pdf":
                files.append((await run.kivra.file(key, part["key"]), "pdf", ctype))
            elif ctype == "text/plain":
                files.append(((part.get("body") or "").encode(), "txt", ctype))
            elif ctype == "text/html":
                files.append((html_to_text(part.get("body") or "").encode(), "txt", "text/plain"))
        files = [f for f in files if f[0].strip()]
        if not files:
            raise KivraError(f"no uploadable parts ({types})")
        multi = len(files) > 1
        uploaded = 0
        for n, (data, ext, mime) in enumerate(files):
            title = f"{subject} ({n + 1}/{len(files)})" if multi else subject
            uploaded += await run.put(doc_filename(date, sender, key, n if multi else None, ext),
                                      data, mime, title=title, created=date, sender=sender,
                                      doc_type=doc_type)
        return uploaded
