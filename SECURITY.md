# Security policy

This plugin handles a Kivra session obtained via BankID and a Paperless API
token, so please report vulnerabilities privately through
[GitHub security advisories](https://github.com/AlexanderBabel/kivra-paperless-maubot/security/advisories/new)
rather than in a public issue.

Only the latest release is supported.

What the plugin does with credentials:

- The Kivra access token lives only in memory for the duration of one sync and
  is never written to disk or logged.
- The QR page is guarded by a random per-login token and stops working once the
  login completes or times out.
- The Paperless token is read from the maubot instance config.
