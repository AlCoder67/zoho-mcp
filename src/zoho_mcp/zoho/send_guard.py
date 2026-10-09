"""Monarc outbound recipient guard: no real send from the agency mailbox to a
Microsoft 365-hosted or deferred-ledger recipient domain.

Scope (owner decision 2026-10-09): applies ONLY when the message's
``fromAddress`` is ``partnerships@monarcmediahq.com``. Mail composed as any other
address is never inspected, never delayed and never logged here.

For a guarded sender every To/Cc/Bcc recipient domain must be:
  * absent from the shared host ledger ``~/.monarc/m365_deferred.json`` (any row
    for that domain, or for a parent of it, whose status is not
    ``owner_released``: ``x@sub.held.com`` is held when ``held.com`` is), and
  * CLEAR on a live MX lookup: no target in a Microsoft-hosted family --
    ``*.mail.protection.outlook.com``, ``*.mx.microsoft``,
    ``*.olc.protection.outlook.com``, ``*.mail.eo.outlook.com``,
    ``*.mail.protection.office365.us`` -- directly or through a CNAME'd MX host
    (a bare suffix host is not a match; owner decision 2026-10-09).
Anything that cannot be established -- ledger missing/corrupt, ``dig`` missing,
timeout, SERVFAIL, truncated or inconsistent answer, no MX, null MX, an
unparseable address, or an effective sender address that cannot be determined
(missing, empty, not text, or the account lookup failed) -- refuses the send
(fail closed).

No call-supplied override is accepted. An owner-approved exception would require
a separate authenticated out-of-band mechanism. Dependency-free: stdlib plus
the system ``dig``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable

from email.utils import getaddresses
from pathlib import Path

GUARDED_SENDERS = frozenset({"partnerships@monarcmediahq.com"})
M365_SUFFIXES = (
    ".mail.protection.outlook.com",
    ".mx.microsoft",
    ".olc.protection.outlook.com",
    ".mail.eo.outlook.com",
    ".mail.protection.office365.us",
)
_DOMAIN = re.compile(
    r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)
_MX = re.compile(r"^(\S+)\s+\d+\s+IN\s+MX\s+\d+\s+(\S+)\s*$", re.IGNORECASE)
_CNAME = re.compile(r"^(\S+)\s+\d+\s+IN\s+CNAME\s+(\S+)\s*$", re.IGNORECASE)

Runner = Callable[..., subprocess.CompletedProcess]


class SendGuardRefusal(Exception):
    """A guarded send was refused. Carries the per-domain reasons."""


def default_ledger_path() -> Path:
    override = os.environ.get("MONARC_M365_LEDGER", "").strip()
    return (
        Path(override) if override else Path.home() / ".monarc" / "m365_deferred.json"
    )


def _normalize_domain(domain: str) -> str:
    domain = domain.rstrip(".").lower()
    if not _DOMAIN.fullmatch(domain):
        raise ValueError(f"invalid domain {domain!r}")
    return domain


def sender_address(from_address: object) -> str:
    """The bare lower-case address of a ``fromAddress`` value ('' if unparseable)."""
    if not isinstance(from_address, str):
        return ""
    parsed = [addr for _name, addr in getaddresses([from_address]) if addr]
    return parsed[0].strip().lower() if len(parsed) == 1 else ""


def is_guarded_sender(
    from_address: object, guarded: Iterable[str] = GUARDED_SENDERS
) -> bool:
    if not isinstance(from_address, str):
        return False
    address = sender_address(from_address)
    if address:
        return address in guarded
    # An unparseable From that still names the guarded mailbox is guarded (fail closed).
    return any(g in from_address.lower() for g in guarded)


def recipient_domains(fields: Iterable[object]) -> list[str]:
    """Domains of every address in Zoho's comma-joined address strings. Raises on junk."""
    values = [f for f in fields if f]
    if any(not isinstance(v, str) for v in values):
        raise ValueError("recipient field is not text")
    addresses = [addr.strip() for _name, addr in getaddresses([str(v) for v in values])]
    if not addresses or any(not a for a in addresses):
        raise ValueError("no parseable recipients")
    domains = []
    for address in addresses:
        if address.count("@") != 1 or not address.split("@")[0]:
            raise ValueError(f"unparseable recipient {address!r}")
        domains.append(_normalize_domain(address.rsplit("@", 1)[1]))
    return sorted(set(domains))


def _records(answer: str, owner: str, pattern: re.Pattern) -> list[str]:
    """Accept exactly one complete NOERROR dig response, never a plausible partial one."""
    headers = re.findall(r"^;;\s*->>HEADER<<-.*$", answer, re.MULTILINE)
    flags = re.findall(r"^;;\s*flags:\s*(.*)$", answer, re.MULTILINE)
    if (
        len(headers) != 1
        or len(flags) != 1
        or not re.search(r"\bstatus:\s*NOERROR\b", headers[0], re.IGNORECASE)
        or re.search(r"(?im)^;;\s*(?:WARNING|ERROR|TRUNCATED|malformed)\b", answer)
    ):
        raise ValueError("incomplete or failed DNS response")
    match = re.fullmatch(
        r"([a-z ]+);\s*QUERY:\s*(\d+),\s*ANSWER:\s*(\d+),\s*AUTHORITY:\s*\d+,\s*ADDITIONAL:\s*\d+\s*",
        flags[0],
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("unparseable DNS flags")
    bits = match.group(1).lower().split()
    if "qr" not in bits or "tc" in bits or int(match.group(2)) != 1:
        raise ValueError("truncated or unexpected DNS response")
    lines = [
        line.strip()
        for line in answer.splitlines()
        if line.strip() and not line.startswith(";")
    ]
    if len(lines) != int(match.group(3)):
        raise ValueError("DNS answer count mismatch")
    targets = []
    for line in lines:
        rr = pattern.fullmatch(line)
        if not rr or rr.group(1).rstrip(".").lower() != owner:
            raise ValueError("unparseable DNS record or owner mismatch")
        targets.append("" if rr.group(2) == "." else _normalize_domain(rr.group(2)))
    return targets


def _is_m365(host: str) -> bool:
    return any(host.endswith(suffix) and host != suffix[1:] for suffix in M365_SUFFIXES)


def mx_verdict(
    domain: str, runner: Runner = subprocess.run, budget_s: float = 12.0
) -> tuple[str, str]:
    """('CLEAR' | 'DROP_M365' | 'HOLD_DNS', reason). Never raises."""
    try:
        domain = _normalize_domain(domain)
        deadline = time.monotonic() + budget_s

        def lookup(host: str, kind: str) -> str:
            result = runner(
                [
                    "dig",
                    "+time=2",
                    "+tries=1",
                    "+noall",
                    "+comments",
                    "+answer",
                    host,
                    kind,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=max(0.01, deadline - time.monotonic()),
            )
            if result.returncode or not result.stdout or result.stderr:
                raise ValueError("DNS command failed or returned incomplete output")
            return result.stdout

        targets = _records(lookup(domain, "MX"), domain, _MX)
        if not targets:
            return "HOLD_DNS", "no MX records"
        if any(_is_m365(t) for t in targets):
            return "DROP_M365", "Microsoft 365 MX"
        if "" in targets:
            return "HOLD_DNS", "null MX"
        if len(targets) > 20:
            return "HOLD_DNS", "too many MX targets"
        for target in targets:
            seen: set[str] = set()
            for _ in range(5):
                if target in seen:
                    return "HOLD_DNS", "MX alias loop"
                seen.add(target)
                if _is_m365(target):
                    return "DROP_M365", "Microsoft 365 MX via alias"
                aliases = _records(lookup(target, "CNAME"), target, _CNAME)
                if not aliases:
                    break
                if len(aliases) != 1 or not aliases[0]:
                    return "HOLD_DNS", "ambiguous MX alias"
                target = aliases[0]
            else:
                return "HOLD_DNS", "MX alias chain too long"
        return "CLEAR", "no Microsoft 365 MX"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return "HOLD_DNS", f"DNS lookup failed: {exc}"


def is_held(domain: str, held: Iterable[str]) -> bool:
    """True when ``domain`` is a held domain or any subdomain of one."""
    return any(domain == h or domain.endswith("." + h) for h in held)


def deferred_domains(path: Path) -> frozenset[str]:
    """Domains with any non-released ledger row. Raises on a missing or malformed ledger.

    Read without the writer's lock: the writer replaces the file atomically
    (os.replace), so a reader sees either the old or the new complete file.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("deferred"), list):
        raise TypeError("invalid ledger")
    domains = set()
    for row in data["deferred"]:
        if not isinstance(row, dict) or not isinstance(row.get("domain"), str):
            raise TypeError("invalid ledger row")
        status = row.get("status", "deferred_m365")
        if status not in ("deferred_m365", "owner_released"):
            raise ValueError("invalid ledger status")
        if status != "owner_released":
            domains.add(_normalize_domain(row["domain"]))
    return frozenset(domains)


class SendGuard:
    def __init__(
        self,
        ledger_path: Path | None = None,
        resolver: Callable[[str], tuple[str, str]] | None = None,
        guarded_senders: Iterable[str] = GUARDED_SENDERS,
    ) -> None:
        self._ledger_path = ledger_path
        self._resolver = resolver or mx_verdict
        self.guarded_senders = frozenset(s.lower() for s in guarded_senders)

    def evaluate(
        self, recipient_fields: Iterable[object]
    ) -> dict[str, tuple[str, str]]:
        """Per-domain verdicts; a single '*' key when recipients or the ledger are unusable."""
        try:
            domains = recipient_domains(recipient_fields)
        except ValueError as exc:
            return {"*": ("HOLD_DNS", f"recipients unverifiable: {exc}")}
        try:
            ledger = deferred_domains(self._ledger_path or default_ledger_path())
        except Exception as exc:  # noqa: BLE001 - any ledger problem is a refusal
            return {
                "*": (
                    "HOLD_DNS",
                    f"M365 ledger unreadable: {type(exc).__name__}: {exc}",
                )
            }
        verdicts = {}
        for domain in domains:
            if is_held(domain, ledger):
                verdicts[domain] = (
                    "DROP_M365",
                    "domain is on the M365 deferred ledger",
                )
                continue
            try:
                verdict, reason = self._resolver(domain)
            except Exception as exc:  # noqa: BLE001
                verdict, reason = "HOLD_DNS", f"resolver crashed: {type(exc).__name__}"
            if verdict not in ("CLEAR", "DROP_M365", "HOLD_DNS"):
                verdict, reason = "HOLD_DNS", "unexpected resolver verdict"
            verdicts[domain] = (verdict, reason)
        return verdicts

    def check(
        self,
        from_address: object,
        recipient_fields: Iterable[object] | None,
        context: str = "send",
    ) -> None:
        """Return to allow; raise SendGuardRefusal to refuse.

        No-op for a known unguarded sender; an unknown sender is refused.
        """
        if not isinstance(from_address, str) or not from_address.strip():
            # The effective sender is unknown, so guarded status cannot be ruled out.
            raise SendGuardRefusal(
                "send refused by the Monarc M365 recipient guard: the sender address "
                "could not be determined. Nothing was sent. This is a hold, not an "
                "error to work around."
            )
        if not is_guarded_sender(from_address, self.guarded_senders):
            return
        fields = list(recipient_fields) if recipient_fields is not None else []
        verdicts = (
            self.evaluate(fields)
            if fields
            else {
                "*": (
                    "HOLD_DNS",
                    "recipients not visible to the guard (server-derived reply/forward)",
                )
            }
        )
        blocked = {d: v for d, v in verdicts.items() if v[0] != "CLEAR"}
        if blocked:
            detail = "; ".join(
                f"{d}: {v[0]} ({v[1]})" for d, v in sorted(blocked.items())
            )
            raise SendGuardRefusal(
                "send refused by the Monarc M365 recipient guard (sender "
                f"{sender_address(from_address) or from_address}): {detail}. Nothing was sent. This is a hold, "
                "not an error to work around. No call-supplied override is accepted."
            )
