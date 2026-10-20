# Contributing

Issues and pull requests are welcome.

- Kivra has no public API. If a sync suddenly breaks, Kivra most likely changed
  an endpoint. A bug report with the failing stage and the HTTP status helps
  most. Leave out tokens and document contents.
- Keep the plugin free of extra Python dependencies. It has to run on a stock
  maubot image, which ships `aiohttp` and `mautrix`.
- Run `python3 test_util.py` before opening a pull request. CI runs it too and
  builds the `.mbp`.
- Releases are cut by pushing a `vX.Y.Z` tag that matches `maubot.yaml` (see
  the README).
