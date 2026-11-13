"""
Collect real, signed Sheffield section 106 deeds from Sheffield City Council's
own public planning register.

Why this exists
---------------
Sheffield City Council (organisation-entity 294) publishes **zero** rows in the
national planning.data.gov.uk developer-agreement dataset, so there is no
structured CSV to audit the city from. The deeds themselves are public though:
the council's Infrastructure Funding Statement points at
`planningapps.sheffield.gov.uk/online-applications/`, and every permission
granted subject to a legal agreement has the engrossed deed attached as a PDF.

Sheffield's register exposes explicit decision codes for exactly this:

    GCLA   Grant Conditionally Legal Agreement
    GRLA   Grant Legal Agreement
    GCSLA  G Conditional Subject to Legal Agreement
    GSLA   GRA GC subject to Legal Agreement
    CONSLA Cond approved subject to Legal Agreement
    GCUA   Grant Conditionally Unilateral Agreement
    ACLA   Res Matls App Conditionally Legal Agreement

The register is an Idox "Public Access" (Struts) application: the advanced
search form is CSRF protected and the session must be seeded first, so a bare
GET of the results endpoint returns HTTP 500. This script does the handshake
properly: GET the form, keep the cookie, POST with the token.

What it produces
----------------
Deed PDFs in `data/fixtures/pdfs/sheffield/` plus `manifest.json`, one entry per
deed recording the planning reference, site address, decision code, the exact
register URL it came from, the byte size and a sha256. Nothing is invented and
nothing is relabelled: every file here was published by Sheffield City Council
against a real Sheffield planning application.

Politeness: one request at a time, a delay between every request, a hard cap on
deeds downloaded, and `--dry-run` to look without taking.

Usage:
    python3 -m scripts.scrape_sheffield_register --dry-run
    python3 -m scripts.scrape_sheffield_register --max-deeds 20
    python3 -m scripts.scrape_sheffield_register --decision GRLA --type 001 --type 007
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import ssl
import time
from base64 import b64encode
from datetime import date
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import certifi
import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "fixtures" / "pdfs" / "sheffield"
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"
TLS_CACHE = OUTPUT_DIR / ".tls"

BASE_URL = "https://planningapps.sheffield.gov.uk/online-applications"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Decision codes Sheffield uses when a permission was granted subject to a
# planning obligation. Read out of the live `searchCriteria.caseDecision` select.
DECISION_CODES: dict[str, str] = {
    "GCLA": "Grant Conditionally Legal Agreement",
    "GRLA": "Grant Legal Agreement",
    "GCSLA": "G Conditional Subject to Legal Agreement",
    "GSLA": "GRA GC subject to Legal Agreement",
    "CONSLA": "Cond approved subject to Legal Agreement",
    "GCUA": "Grant Conditionally Unilateral Agreement",
    "ACLA": "Res Matls App Conditionally Legal Agreement",
}

# Residential application types: the ones that actually carry financial obligations.
RESIDENTIAL_TYPES: dict[str, str] = {
    "001": "Large Scale Major Dwellings",
    "007": "Small Scale Major Dwellings",
    "013": "Minor Dwellings",
}

# A document is treated as the deed when its filename names a legal agreement,
# a unilateral undertaking or a planning obligation.
DEED_NAME_RE = re.compile(
    r"(legal[_\s-]?agreement|unilateral|s106|section[_\s-]?106|planning[_\s-]?obligation)",
    re.IGNORECASE,
)
# ...and rejected when it is one of the other things a case file holds: the many
# non-obligation "agreements" (extension of time, pre-commencement correspondence),
# unsigned drafts, and section 111 agreements, which are a different legal power.
NOT_A_DEED_RE = re.compile(
    r"(extension[_\s-]?of[_\s-]?time|eot|pre[_\s-]?commence|pre[_\s-]?commencement"
    r"|written[_\s-]?confirmation|draft|unsigned|review[_\s-]?of[_\s-]?conditions"
    r"|discharge[_\s-]?of[_\s-]?conditions|time[_\s-]?extension"
    r"|s111|section[_\s-]?111|s\.?111"
    # progress notes about the obligation, not the obligation itself
    r"|memo|memorandum|status|update|tracking|checklist|schedule[_\s-]?of[_\s-]?obligations"
    r"|CIL[_\s-]*and)",
    re.IGNORECASE,
)

# When a case file holds several copies of the same deed (developer signed, council
# signed, final, engrossment), prefer the most authoritative single copy.
DEED_PREFERENCE: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"engross", re.IGNORECASE), 60),
    (re.compile(r"completed", re.IGNORECASE), 50),
    (re.compile(r"(scc|council)[_\s-]*signed|signed[_\s-]*copy", re.IGNORECASE), 40),
    (re.compile(r"final", re.IGNORECASE), 30),
    (re.compile(r"signed", re.IGNORECASE), 20),
    (re.compile(r"s106|section[_\s-]?106", re.IGNORECASE), 10),
]


def deed_score(filename: str) -> int:
    return sum(weight for pattern, weight in DEED_PREFERENCE if pattern.search(filename))


PDF_HREF_RE = re.compile(r'href="(/online-applications/files/[^"]+\.pdf)"', re.IGNORECASE)
KEYVAL_RE = re.compile(r"applicationDetails\.do\?[^\"']*keyVal=([A-Z0-9]+)", re.IGNORECASE)
CSRF_RE = re.compile(r'name="_csrf"[^>]*value="([^"]+)"')
UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Anonymous searches return at most this many applications, whatever the form asks for.
REGISTER_PAGE_CAP = 10


def date_windows(start: str, end: str, months: int) -> list[tuple[str, str]]:
    """
    The register silently caps anonymous searches at 10 results and its paging
    endpoint returns nothing useful, so wide searches silently lose applications.
    Splitting the period into windows and taking each one whole is the only
    reliable way to cover a multi-year span.
    """
    if months <= 0:
        return [(start, end)]
    from datetime import datetime, timedelta

    def add_months(moment, count):
        total = moment.month - 1 + count
        year = moment.year + total // 12
        month = total % 12 + 1
        day = min(moment.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                                else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return moment.replace(year=year, month=month, day=day)

    first = datetime.strptime(start, "%d/%m/%Y")
    last = datetime.strptime(end, "%d/%m/%Y")
    windows: list[tuple[str, str]] = []
    cursor = first
    while cursor <= last:
        stop = min(add_months(cursor, months) - timedelta(days=1), last)
        windows.append((cursor.strftime("%d/%m/%Y"), stop.strftime("%d/%m/%Y")))
        cursor = stop + timedelta(days=1)
    return windows


def _clean(value: Optional[str]) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


def _leaf_der(host: str) -> bytes:
    """Grab the leaf certificate the server actually presents."""
    context = ssl._create_unverified_context()
    with socket.create_connection((host, 443), timeout=30) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise RuntimeError(f"{host} presented no certificate")
    return der


def _aia_urls(der: bytes) -> list[str]:
    """Issuer URLs embedded in the leaf: the AIA caIssuers pointers a browser follows."""
    urls = re.findall(rb"https?://[A-Za-z0-9./_-]+\.crt", der)
    return sorted({u.decode() for u in urls})


def _pem(der: bytes) -> str:
    return f"-----BEGIN CERTIFICATE-----\n{b64encode(der).decode()}\n-----END CERTIFICATE-----\n"


def repair_ca_bundle(host: str) -> Path:
    """
    Sheffield serves an incomplete TLS chain: the leaf only, no intermediate, so a
    strict client cannot build a path to a root even though the certificate itself
    is valid. Rather than switch verification off, fetch the missing issuer from the
    URLs inside the leaf certificate - exactly what a browser does - and build a
    bundle we can genuinely verify against.
    """
    TLS_CACHE.mkdir(parents=True, exist_ok=True)
    der = _leaf_der(host)
    intermediates: list[str] = []
    for url in _aia_urls(der):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed AIA hosts
                blob = response.read()
        except OSError as exc:
            print(f"    ! could not fetch issuer {url}: {exc}")
            continue
        intermediates.append(
            blob.decode() + "\n" if blob.lstrip().startswith(b"-----BEGIN") else _pem(blob)
        )
    if not intermediates:
        raise RuntimeError(f"no issuer certificates found in the {host} leaf")
    bundle = TLS_CACHE / "repaired-chain.pem"
    bundle.write_text("".join(intermediates) + Path(certifi.where()).read_text())
    return bundle


def _is_cert_error(exc: Exception) -> bool:
    return "certificate verify failed" in str(exc).lower()


def looks_like_deed(filename: str) -> bool:
    """True for engrossed deeds, False for the other 'agreements' in a case file."""
    return bool(DEED_NAME_RE.search(filename)) and not NOT_A_DEED_RE.search(filename)


class SheffieldRegister:
    """Minimal, polite client for the Idox Public Access advanced search."""

    def __init__(self, delay: float = 1.5) -> None:
        self.delay = delay
        self.headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"}
        self.tls_note = "chain verified against the bundled CA store"
        self.client = self._client(verify=True)
        self._csrf: Optional[str] = None
        self._page_csrf: Optional[str] = None

    def _client(self, verify: Any) -> httpx.Client:
        return httpx.Client(
            headers=self.headers, timeout=httpx.Timeout(60.0), follow_redirects=True, verify=verify
        )

    def _probe(self) -> None:
        """
        One request up front. If the council's incomplete chain defeats verification,
        repair the chain and rebuild the client rather than silently turning TLS
        checks off.
        """
        try:
            self.client.get(f"{BASE_URL}/").raise_for_status()
            return
        except httpx.ConnectError as exc:
            if not _is_cert_error(exc):
                raise
        self.client.close()
        host = BASE_URL.split("//", 1)[1].split("/", 1)[0]
        bundle = repair_ca_bundle(host)
        self.client = self._client(verify=str(bundle))
        self.tls_note = (
            "the council omits its TLS intermediate, so the issuer was fetched from the "
            f"certificate's own AIA URL at collection time ({bundle.name})"
        )
        print(f"  TLS: {host} serves an incomplete chain; issuer recovered from its AIA URL")
        self.client.get(f"{BASE_URL}/").raise_for_status()

    def close(self) -> None:
        self.client.close()

    def _get(self, url: str) -> httpx.Response:
        time.sleep(self.delay)
        return self.client.get(url)

    def token(self, refresh: bool = False) -> str:
        """Seed the session and read the CSRF token off the advanced search form."""
        if refresh or self._csrf is None:
            response = self._get(f"{BASE_URL}/search.do?action=advanced&searchType=Application")
            response.raise_for_status()
            match = CSRF_RE.search(response.text)
            if not match:
                raise RuntimeError("no _csrf token on the advanced search form")
            self._csrf = match.group(1)
        return self._csrf

    def _post_with_backoff(self, url: str, body: dict[str, str], attempts: int = 4) -> httpx.Response:
        """
        The council rate limits the search endpoint and answers 429. Honour
        Retry-After and back off instead of hammering it.
        """
        for attempt in range(1, attempts + 1):
            time.sleep(self.delay)
            response = self.client.post(url, data=body)
            if response.status_code != 429:
                return response
            wait = float(response.headers.get("retry-after") or (15 * attempt))
            print(f"    ~ rate limited (429), waiting {wait:.0f}s")
            time.sleep(min(wait, 90))
        return response

    def search(
        self,
        decision: Optional[str],
        dev_type: Optional[str],
        start: str,
        end: str,
        page: int = 1,
        per_page: int = 100,
        status: Optional[str] = None,
    ) -> list[dict[str, str]]:
        """One page of the advanced search. Returns application cards."""
        body = {
            "searchType": "Application",
            "searchCriteria.resultsPerPage": str(per_page),
            "searchCriteria.page": str(page),
            "date(applicationDecisionStart)": start,  # the register wants dd/mm/yyyy
            "date(applicationDecisionEnd)": end,
        }
        if decision:  # "ANY" means do not filter on the decision code at all
            body["searchCriteria.caseDecision"] = decision
        if status:
            body["searchCriteria.caseStatus"] = status
        if dev_type:
            body["searchCriteria.developmentType"] = dev_type
        response = None
        for attempt in (1, 2):
            # The token is single-use in practice, so a rejected search is retried
            # once against a freshly fetched form before giving up.
            body["_csrf"] = self.token(refresh=attempt == 2)
            response = self._post_with_backoff(
                f"{BASE_URL}/advancedSearchResults.do?action=firstPage", body
            )
            if response.status_code != 200:
                print(f"    ! search failed: HTTP {response.status_code}")
                return []
            # A rejected search is bounced back to the form, which still says
            # "Applications Search"; real results say "Search Results".
            if "Search Results" in response.text:
                token = CSRF_RE.search(response.text)
                self._page_csrf = token.group(1) if token else None
                return self._cards(response.text)
        warning = re.search(
            r"(?i)please check the search criteria(.{0,200})", response.text, re.S
        )
        if re.search(r"(?i)no results found", response.text):
            print("    - no applications with this decision code")
            return []
        detail = ""
        if warning:
            detail = _clean(warning.group(1))[:120]
        print(f"    ! search rejected{': ' + detail if detail else ''}")
        return []

    @staticmethod
    def _cards(html: str) -> list[dict[str, str]]:
        """Pull (keyVal, reference, address, description) out of a results page."""
        cards: list[dict[str, str]] = []
        seen: set[str] = set()
        # Every card is introduced by its summary link: ...keyVal=XXXX&activeTab=summary
        parts = re.split(r"applicationDetails\.do\?[^\"']*?keyVal=([A-Z0-9]+)", html)
        for index in range(1, len(parts) - 1, 2):
            key_val, chunk = parts[index].upper(), parts[index + 1][:2000]
            reference = re.search(
                r"Ref\.?\s*No:?(?:\s|</[^>]+>)*([0-9]{2}/[0-9]{4,7}/[A-Z/]+)", chunk
            )
            if not reference or key_val in seen:
                continue
            seen.add(key_val)
            address = re.search(r'class="address"[^>]*>(.*?)</p>', chunk, re.S)
            description = re.search(
                r'class="summaryLinkTextClamp"[^>]*>(.*?)</div>', chunk, re.S
            )
            cards.append(
                {
                    "key_val": key_val,
                    "planning_reference": reference.group(1).strip(),
                    "site_address": _clean(address.group(1)) if address else "",
                    "description": _clean(description.group(1)) if description else "",
                }
            )
        return cards

    def documents(self, key_val: str, retries: int = 2) -> list[dict[str, str]]:
        """The document list for one application, as {name, url} pairs."""
        found: list[dict[str, str]] = []
        for attempt in range(1, retries + 1):
            response = self._get(
                f"{BASE_URL}/applicationDetails.do?activeTab=documents&keyVal={key_val}"
            )
            if response.status_code == 429:
                time.sleep(min(20 * attempt, 90))
                continue
            if response.status_code != 200:
                return []
            seen: set[str] = set()
            found = []
            for href in PDF_HREF_RE.findall(response.text):
                url = urljoin(BASE_URL, href)
                if url in seen:
                    continue
                seen.add(url)
                found.append({"filename": url.rsplit("/", 1)[-1], "url": url})
            # A case with documents that returns none is a rate limit, not an empty file.
            if found or attempt == retries:
                return found
            time.sleep(6 * attempt)
        return found

    def page(self, page: int) -> list[dict[str, str]]:
        """
        Page through the last search. Paging is a separate form post: the criteria
        live in the session, and the results page carries its own token and an
        `action=page` control.
        """
        if not self._page_csrf:
            return []
        body = {
            "_csrf": self._page_csrf,
            "action": "page",
            "searchCriteria.page": str(page),
            "searchCriteria.resultsPerPage": "100",
            "orderBy": "DateReceived",
            "orderByDirection": "Descending",
        }
        response = self._post_with_backoff(f"{BASE_URL}/pagedSearchResults.do", body)
        if response.status_code != 200 or "Search Results" not in response.text:
            return []
        token = CSRF_RE.search(response.text)
        if token:
            self._page_csrf = token.group(1)
        return self._cards(response.text)

    def download(self, url: str) -> bytes:
        response = self._get(url)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"{url} is not a PDF ({response.headers.get('content-type')})")
        return response.content


def _safe_name(reference: str, filename: str) -> str:
    ref = UNSAFE_RE.sub("-", reference).strip("-")
    name = UNSAFE_RE.sub("_", filename).strip("_")
    return f"sheffield_{ref}_{name}".lower()


def collect(
    decisions: list[str],
    dev_types: list[str],
    start: str,
    end: str,
    max_deeds: int,
    dry_run: bool,
    delay: float,
    all_versions: bool = False,
    status: Optional[str] = None,
    window_months: int = 0,
) -> tuple[list[dict[str, Any]], str]:
    register = SheffieldRegister(delay=delay)
    entries: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    seen_hashes: set[str] = set()
    try:
        register._probe()
        for decision in decisions:
            decision = None if decision == "ANY" else decision
            for dev_type in (dev_types or [None]):
                if len(entries) >= max_deeds:
                    break
                label = f"{decision or 'ANY DECISION'} x {dev_type or 'any type'}"
                print(f"\n[{label}] {DECISION_CODES.get(decision, 'any decision')} / "
                      f"{RESIDENTIAL_TYPES.get(dev_type, 'all application types')}"
                      f"{f' / status {status}' if status else ''}, {start} to {end}")
                for w_start, w_end in date_windows(start, end, window_months):
                    if len(entries) >= max_deeds:
                        break
                    if window_months > 0:
                        print(f"    window {w_start} to {w_end}")
                    cards = register.search(decision, dev_type, w_start, w_end, status=status)
                    if not cards:
                        continue
                    if len(cards) >= REGISTER_PAGE_CAP:
                        print(f"    ! {len(cards)} results: the register caps a search at "
                              f"{REGISTER_PAGE_CAP}, so this window may be incomplete. Lower "
                              f"--window-months to cover it properly.")
                    new = [c for c in cards if c["planning_reference"] not in seen_refs]
                    print(f"    {len(cards)} applications, {len(new)} new")
                    for card in new:
                        if len(entries) >= max_deeds:
                            break
                        seen_refs.add(card["planning_reference"])
                        docs = register.documents(card["key_val"])
                        deeds = [d for d in docs if looks_like_deed(d["filename"])]
                        # A case file often holds several copies of the same deed. Take
                        # the most authoritative one unless every copy was requested.
                        deeds.sort(key=lambda d: -deed_score(d["filename"]))
                        if not all_versions and len(deeds) > 1:
                            skipped = [d["filename"] for d in deeds[1:]]
                            deeds = deeds[:1]
                            print(f"    · {card['planning_reference']}: {len(skipped)} other "
                                  f"copies of this deed left in the register")
                        if not deeds:
                            print(f"    - {card['planning_reference']}: "
                                  f"{len(docs)} documents, no signed deed")
                            continue
                        for deed in deeds:
                            if len(entries) >= max_deeds:
                                break
                            out_name = _safe_name(card["planning_reference"], deed["filename"])
                            if dry_run:
                                print(f"    + {card['planning_reference']}: {deed['filename']}")
                                entries.append(
                                    {**card, "decision_code": decision,
                                     "development_type": dev_type, "file": out_name,
                                     "document_url": deed["url"], "dry_run": True}
                                )
                                continue
                            try:
                                blob = register.download(deed["url"])
                            except (httpx.HTTPError, ValueError) as exc:
                                print(f"    ! {deed['filename']}: download failed ({exc})")
                                continue
                            digest = hashlib.sha256(blob).hexdigest()
                            if digest in seen_hashes:
                                print(f"    = {deed['filename']}: duplicate of one already taken")
                                continue
                            seen_hashes.add(digest)
                            (OUTPUT_DIR / out_name).write_bytes(blob)
                            entry = {
                                **card,
                                "decision_code": decision,
                                "decision_label": DECISION_CODES.get(decision, decision),
                                "development_type": dev_type,
                                "development_type_label": RESIDENTIAL_TYPES.get(dev_type, dev_type),
                                "file": out_name,
                                "document_url": deed["url"],
                                "bytes": len(blob),
                                "sha256": digest,
                            }
                            entries.append(entry)
                            print(f"    + {card['planning_reference']} -> {out_name} "
                                  f"({len(blob):,} bytes)")
                    if len(entries) >= max_deeds:
                        break
    finally:
        tls_note = register.tls_note
        register.close()
    return entries, tls_note


def main() -> None:
    today = date.today().strftime("%d/%m/%Y")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", action="append", choices=sorted(DECISION_CODES),
                        help="decision code to search (repeatable); default: all")
    parser.add_argument("--type", dest="dev_type", action="append", choices=sorted(RESIDENTIAL_TYPES),
                        help="development type to search (repeatable); default: all residential")
    parser.add_argument("--from-date", default="01/01/2022", help="decision start dd/mm/yyyy")
    parser.add_argument("--to-date", default=today, help="decision end dd/mm/yyyy")
    parser.add_argument("--max-deeds", type=int, default=20)
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between requests")
    parser.add_argument("--dry-run", action="store_true", help="list deeds without downloading")
    parser.add_argument("--all-versions", action="store_true",
                        help="keep every copy of a deed, not just the most authoritative")
    parser.add_argument("--any-decision", action="store_true",
                        help="do not filter on decision code: sweep every decided application")
    parser.add_argument("--status", default=None,
                        choices=["Decided", "Registered", "Awaiting decision",
                                 "Appeal lodged", "Appeal decided"],
                        help="case status filter, useful with --any-decision")
    parser.add_argument("--window-months", type=int, default=0,
                        help="split the date range into windows of N months; the register "
                             "caps every search at 10 applications, so wide ranges "
                             "silently lose results")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Sheffield public planning register: {BASE_URL}")
    print(f"decision codes: {', '.join(args.decision or sorted(DECISION_CODES))}")
    print(f"types: {', '.join(args.dev_type) if args.dev_type else 'all (no type filter)'}")
    print(f"decided between {args.from_date} and {args.to_date}, cap {args.max_deeds} deeds")

    entries, tls_note = collect(
        decisions=["ANY"] if args.any_decision else (args.decision or sorted(DECISION_CODES)),
        dev_types=args.dev_type or [],
        start=args.from_date,
        end=args.to_date,
        max_deeds=args.max_deeds,
        dry_run=args.dry_run,
        delay=args.delay,
        all_versions=args.all_versions,
        status=args.status,
        window_months=args.window_months,
    )

    # Re-running adds to the corpus instead of throwing away what was already taken.
    previous: list[dict[str, Any]] = []
    if MANIFEST_FILE.exists() and not args.dry_run:
        try:
            previous = json.loads(MANIFEST_FILE.read_text()).get("deeds", [])
        except (json.JSONDecodeError, AttributeError):
            previous = []
    merged: dict[str, dict[str, Any]] = {d["file"]: d for d in previous}
    merged.update({d["file"]: d for d in entries})

    manifest = {
        "_comment": (
            "Real section 106 deeds downloaded from Sheffield City Council's public "
            "planning register (Idox Public Access). Each entry is the engrossed legal "
            "agreement attached to a Sheffield permission granted subject to a planning "
            "obligation. Not synthetic."
        ),
        "_generated": date.today().isoformat(),
        "_source": BASE_URL,
        "_authority": "Sheffield City Council",
        "_authority_entity": "local-authority:SHF",
        "_tls": tls_note,
        "deeds": [merged[key] for key in sorted(merged)],
    }
    if not args.dry_run:
        MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"\n{len(entries)} new deeds in {OUTPUT_DIR} ({len(merged)} total in manifest)")
        print(f"manifest: {MANIFEST_FILE}")
    else:
        print(f"\ndry run: {len(entries)} deeds would be downloaded")


if __name__ == "__main__":
    main()
