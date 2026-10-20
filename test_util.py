"""Run: python3 test_util.py"""
import base64
import json
import sys

sys.path.insert(0, "kivra_paperless")
from util import doc_filename, html_to_text, jwt_claims, pkce_challenge, retry_after  # noqa: E402

# S256 = unpadded base64url of a 32-byte digest
c = pkce_challenge("verifier")
assert len(c) == 43 and "=" not in c and "+" not in c and "/" not in c

payload = base64.urlsafe_b64encode(json.dumps({"kivra_user_id": "u1"}).encode()).decode().rstrip("=")
assert jwt_claims(f"h.{payload}.sig") == {"kivra_user_id": "u1"}

assert doc_filename("2026-01-02", "Skatteverket / Tax", "abc", None, "pdf") == \
    "2026-01-02_Skatteverket _ Tax_abc.pdf"
assert doc_filename("2026-01-02", "Åhléns", "abc", 1, "txt") == "2026-01-02_Åhléns_abc_part1.txt"
assert doc_filename("", "///", "k", None, "pdf") == "_unknown_k.pdf"

text = html_to_text("<style>p{}</style><h1>Faktura</h1><p>Att  betala:<br>100 kr</p><script>x()</script>")
assert text == "Faktura\n\nAtt betala:\n100 kr\n", repr(text)
assert retry_after("7", 5) == 7
assert retry_after(None, 40) == 40
assert retry_after("Wed, 21 Oct 2026 07:28:00 GMT", 10) == 10
assert retry_after("9999", 5) == 120 and retry_after("0", 5) == 1
print("ok")
