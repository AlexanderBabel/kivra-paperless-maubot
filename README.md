# kivra-paperless-maubot

[![release](https://img.shields.io/github/v/release/AlexanderBabel/kivra-paperless-maubot)](https://github.com/AlexanderBabel/kivra-paperless-maubot/releases/latest)
[![build](https://github.com/AlexanderBabel/kivra-paperless-maubot/actions/workflows/release.yml/badge.svg)](https://github.com/AlexanderBabel/kivra-paperless-maubot/actions/workflows/release.yml)
[![license](https://img.shields.io/github/license/AlexanderBabel/kivra-paperless-maubot)](LICENSE)

A [maubot](https://github.com/maubot/maubot) plugin that imports your
[Kivra](https://kivra.com) letters and receipts into [Paperless-ngx](https://docs.paperless-ngx.com),
started from a Matrix chat and logged in with BankID.

```
you:  !kivra sync
bot:  🔐 Log in to Kivra with BankID
      Scan the live QR code: https://bot.example.com/_matrix/maubot/plugin/kivra/login/…
bot:  📬 37/120 · 12 new · 25 already in Paperless · 0 failed      (edited in place)
bot:  🎉 Kivra sync done: 31 new, 89 already in Paperless, 0 failed (of 120).
```

## How it works

1. `!kivra sync` starts a BankID login against Kivra's web OAuth2 flow.
2. The bot posts a link to a small page served by the plugin itself. It shows
   BankID's animated QR code, refreshed every second. A static image in the chat
   doesn't work for this, because BankID rejects a QR frame within seconds.
3. Once you're logged in, the bot lists every letter and receipt in your inbox.
   It skips the ones Paperless already has (it searches `original_filename` for the Kivra
   key) and uploads the rest:
   - PDF parts are uploaded as they are.
   - Plain-text parts are uploaded as `.txt`.
   - HTML parts are converted to `.txt`. Without Tika, Paperless can't ingest
     HTML.
   - The sender becomes the correspondent and the subject becomes the title.
     The received date is used as the created date.
   - Receipts (kvitton) are uploaded as the PDF Kivra renders for them. The
     store becomes the correspondent, the title is `Kvitto <store> <amount>`, and
     the purchase date is used as the created date.
   - If Paperless has a `Source` custom field, every upload gets `Source = Kivra`.
     Both names are configurable, and the bot never creates the field.
4. A single status message is edited as the sync runs (at most once every 5 s).

Each sync is a fresh BankID login. No Kivra token is stored.

## Commands

| Command          | What it does                               |
|------------------|--------------------------------------------|
| `!kivra sync`    | Log in with BankID and import new letters and receipts |
| `!kivra status`  | Show progress of the running sync          |
| `!kivra cancel`  | Cancel the running sync                    |

Only users listed in `allowed_users` can start or cancel a sync.

## Install

Download `com.alexbabel.kivrapaperless-v<version>.mbp` from the
[latest release](../../releases/latest) and upload it in the maubot admin UI
(Plugins → +). Then create an instance with it and fill in the config (see [base-config.yaml](base-config.yaml)):

```yaml
allowed_users: ["@you:example.com"]
paperless:
  url: https://paperless.example.com
  token: <Paperless API token>
  tags: [kivra]
  document_type: ""           # for letters
  receipt_document_type: ""   # for receipts
  source_field: Source         # custom field; empty = off
  source: Kivra                # value, or option label for a select field
login_timeout: 180
```

Other requirements:

- maubot's `server.public_url` must be reachable from the device you scan
  with. The QR page lives under `/_matrix/maubot/plugin/<instance>/login/<token>`.
  The token is random and is only valid while that login is running.
- The page loads `qrcodejs` from cdnjs to draw the QR code.
- Python dependencies: none beyond what maubot already ships (aiohttp, mautrix).

## Limitations

- Kivra has no public API. This plugin uses the endpoints of Kivra's own web
  inbox, as reverse engineered by [kivra-sync](#credits), and will break when Kivra changes them.
- The first sync uploads every receipt you have, which can be thousands of
  card purchases. Paperless OCRs each one.
- Only one sync runs at a time per bot instance.
- The "Open BankID on this device" button only appears if Kivra returns an
  autostart token. Whether it does hasn't been confirmed; the QR code always
  works.
- If a letter's upload fails partway through, its other parts are not retried
  on later syncs, because dedupe works per letter.

## Development

```bash
python3 test_util.py   # stdlib-only self-check of the helpers
pip install maubot     # for the mbc CLI
mbc build              # -> com.alexbabel.kivrapaperless-v<version>.mbp
```

Every push and pull request runs the self-check and builds the plugin in
GitHub Actions. The `.mbp` is attached to the run as an artifact.

### Releasing

1. Bump `version` in [maubot.yaml](maubot.yaml) and commit.
2. Tag that commit with the same version and push the tag:

   ```bash
   git tag v0.1.1
   git push origin v0.1.1
   ```

The [release workflow](.github/workflows/release.yml) checks that the tag
matches `maubot.yaml`, builds the plugin, and publishes a GitHub release with
the `.mbp` attached.

## Credits

- [felixandersen/kivra-sync](https://github.com/felixandersen/kivra-sync)
  worked out Kivra's BankID OAuth flow, the GraphQL queries and the download
  endpoints, and pioneered the Paperless-ngx import. This plugin's Kivra client
  is an async port of that work. kivra-sync itself started from
  [stefangorling/fetch-kivra](https://github.com/stefangorling/fetch-kivra).
  Thank you both!
- [maubot](https://github.com/maubot/maubot) by tulir provides the bot
  framework and the plugin web server.
- [qrcodejs](https://github.com/davidshimjs/qrcodejs) draws the QR code on the
  login page.

## License

MIT, see [LICENSE](LICENSE). It includes the kivra-sync copyright notice
because the Kivra client is derived from that project.

Not affiliated with or endorsed by Kivra, BankID, or Paperless-ngx.
