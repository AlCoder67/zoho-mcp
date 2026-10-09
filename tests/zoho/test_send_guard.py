"""Monarc M365 / deferred-ledger send guard (fake DNS, temp ledger)."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from zoho_mcp.tools import mail as mail_tools
from zoho_mcp.zoho import send_guard as sg
from zoho_mcp.zoho.client import ZohoAPIError, ZohoClient

ACCOUNT_ID = "acct-123"
GUARDED = "partnerships@monarcmediahq.com"
SEND_URL = f"https://mail.zoho.com/api/accounts/{ACCOUNT_ID}/messages"


class FakeTokenManager:
    async def get_access_token(self) -> str:
        return "fake-access-token"


def dig_answer(owner, *targets, status="NOERROR", flags="qr rd ra", kind="MX"):
    lines = [
        f";; ->>HEADER<<- opcode: QUERY, status: {status}, id: 1",
        f";; flags: {flags}; QUERY: 1, ANSWER: {len(targets)}, AUTHORITY: 0, ADDITIONAL: 0",
    ]
    for i, target in enumerate(targets):
        lines.append(
            f"{owner}. 300 IN MX {i} {target}"
            if kind == "MX"
            else f"{owner}. 300 IN CNAME {target}"
        )
    return "\n".join(lines) + "\n"


def runner_for(answers, cname=None):
    """answers: domain -> dig text for MX; cname: host -> dig text for CNAME (default empty)."""

    def run(cmd, **kwargs):
        host, kind = cmd[-2], cmd[-1]
        if kind == "MX":
            out = answers[host]
        else:
            out = (cname or {}).get(host, dig_answer(host, kind="CNAME"))
        if isinstance(out, BaseException):
            raise out
        return subprocess.CompletedProcess(cmd, 0, out, "")

    return run


def fake_resolver(calls=None):
    def resolve(domain):
        if calls is not None:
            calls.append(domain)
        if "m365" in domain:
            return "DROP_M365", "fake outlook MX"
        if "dnsfail" in domain:
            return "HOLD_DNS", "fake timeout"
        return "CLEAR", "fake google MX"

    return resolve


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "m365_deferred.json"
    path.write_text(
        json.dumps(
            {
                "deferred": [
                    {
                        "brand": "Deferred Co",
                        "domain": "deferred.test",
                        "status": "deferred_m365",
                    },
                    {
                        "brand": "Released Co",
                        "domain": "released.test",
                        "status": "owner_released",
                        "owner_approval": "Ofentse 2026-10-09",
                        "sender_domain_history_verified": True,
                    },
                ]
            }
        )
    )
    return path


@pytest.fixture
def guard(tmp_path, ledger):
    calls: list[str] = []
    g = sg.SendGuard(
        ledger_path=ledger,
        resolver=fake_resolver(calls),
    )
    g.calls = calls  # type: ignore[attr-defined]
    return g


# --- MX classification -------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (dig_answer("brand.test", "aspmx.l.google.com."), "CLEAR"),
        (
            dig_answer("brand.test", "brand-test.mail.protection.outlook.com."),
            "DROP_M365",
        ),
        (
            dig_answer("brand.test", "BRAND-TEST.MAIL.PROTECTION.OUTLOOK.COM."),
            "DROP_M365",
        ),
        (
            dig_answer(
                "brand.test", "aspmx.l.google.com.", "x.mail.protection.outlook.com."
            ),
            "DROP_M365",
        ),
        (dig_answer("brand.test", "mail.protection.outlook.com.evil.test."), "CLEAR"),
        (dig_answer("brand.test"), "HOLD_DNS"),
        (dig_answer("brand.test", "."), "HOLD_DNS"),
        (
            dig_answer("brand.test", "aspmx.l.google.com.", status="SERVFAIL"),
            "HOLD_DNS",
        ),
        (
            dig_answer("brand.test", "aspmx.l.google.com.", flags="qr tc rd ra"),
            "HOLD_DNS",
        ),
        (
            dig_answer("brand.test", "aspmx.l.google.com.").replace(
                "ANSWER: 1", "ANSWER: 2"
            ),
            "HOLD_DNS",
        ),
        (dig_answer("other.test", "aspmx.l.google.com."), "HOLD_DNS"),
        (
            dig_answer("brand.test", "aspmx.l.google.com.") + ";; WARNING: recursion\n",
            "HOLD_DNS",
        ),
        ("", "HOLD_DNS"),
    ],
)
def test_mx_verdict_classifies_and_fails_closed(answer, expected):
    assert (
        sg.mx_verdict("brand.test", runner=runner_for({"brand.test": answer}))[0]
        == expected
    )


def test_new_microsoft_mx_suffix_direct_is_dropped():
    run = runner_for({"brand.test": dig_answer("brand.test", "tonyschocolonely-com.i-v1.mx.microsoft.")})
    assert sg.mx_verdict("brand.test", runner=run)[0] == "DROP_M365"


def test_new_microsoft_mx_suffix_via_alias_is_dropped():
    run = runner_for(
        {"brand.test": dig_answer("brand.test", "mx.brand.test.")},
        {"mx.brand.test": dig_answer("mx.brand.test", "tonyschocolonely-com.i-v1.mx.microsoft.", kind="CNAME")},
    )
    assert sg.mx_verdict("brand.test", runner=run)[0] == "DROP_M365"


def test_mx_alias_to_microsoft_is_dropped():
    run = runner_for(
        {"brand.test": dig_answer("brand.test", "mx.brand.test.")},
        {
            "mx.brand.test": dig_answer(
                "mx.brand.test", "tenant.mail.protection.outlook.com.", kind="CNAME"
            )
        },
    )
    assert sg.mx_verdict("brand.test", runner=run)[0] == "DROP_M365"


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("dig"), subprocess.TimeoutExpired("dig", 2), OSError("boom")],
)
def test_mx_resolver_errors_hold(error):
    assert (
        sg.mx_verdict("brand.test", runner=runner_for({"brand.test": error}))[0]
        == "HOLD_DNS"
    )


def test_mx_nonzero_exit_or_stderr_holds():
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 9, dig_answer("brand.test", "aspmx.l.google.com."), ""
        )

    assert sg.mx_verdict("brand.test", runner=run)[0] == "HOLD_DNS"

    def run2(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, dig_answer("brand.test", "aspmx.l.google.com."), "warning"
        )

    assert sg.mx_verdict("brand.test", runner=run2)[0] == "HOLD_DNS"


# --- ledger -------------------------------------------------------------------


def test_ledger_released_rows_do_not_block_and_bad_ledgers_raise(tmp_path, ledger):
    assert sg.deferred_domains(ledger) == frozenset({"deferred.test"})
    with pytest.raises(FileNotFoundError):
        sg.deferred_domains(tmp_path / "missing.json")
    for bad in (
        "not json",
        "[]",
        '{"deferred": {}}',
        '{"deferred": [{"brand": "x"}]}',
        '{"deferred": [{"domain": "a.test", "status": "dead"}]}',
    ):
        (tmp_path / "bad.json").write_text(bad)
        with pytest.raises((ValueError, TypeError)):
            sg.deferred_domains(tmp_path / "bad.json")


# --- SendGuard.check ----------------------------------------------------------


def test_unguarded_sender_is_never_inspected(tmp_path):
    calls: list[str] = []
    g = sg.SendGuard(
        ledger_path=tmp_path / "missing.json",
        resolver=fake_resolver(calls),
    )
    for sender in (
        "ofentse@icloud.com",
        "Ofentse <ofentse@gmail.com>",
        "hello@monarcmediahq.com",
        "partnerships@monarcmediahq.co",
    ):
        g.check(sender, ["x@m365.test", "y@deferred.test"])
        g.check(sender, None)
    assert calls == []


def test_guarded_clear_recipients_pass(guard):
    guard.check(GUARDED, ["a@clear.test", None, ""])
    guard.check(f"Monarc Media <{GUARDED.upper()}>", ["a@clear.test"], context="x")
    assert guard.calls == ["clear.test", "clear.test"]


@pytest.mark.parametrize(
    "fields",
    [
        ["b@m365.test"],
        ["a@clear.test", "b@m365.test"],  # Cc
        ["a@clear.test", None, "b@m365.test"],  # Bcc
        ["a@clear.test,b@m365.test"],  # comma-joined To, as Zoho receives it
        ["x@deferred.test"],  # ledger domain, DNS clear
        ["x@DEFERRED.TEST"],
        ["c@dnsfail.test"],
        ["not-an-address"],
        ["a@clear.test,"],
        [],
    ],
)
def test_guarded_bad_recipients_refused(guard, fields):
    with pytest.raises(sg.SendGuardRefusal):
        guard.check(GUARDED, fields)


def test_released_ledger_domain_still_needs_clear_mx(guard):
    guard.check(GUARDED, ["a@released.test"])  # owner_released and fake MX clear


def test_invisible_recipients_refused(guard):
    with pytest.raises(sg.SendGuardRefusal, match="not visible"):
        guard.check(GUARDED, None)


def test_missing_or_corrupt_ledger_refuses(tmp_path):
    g = sg.SendGuard(ledger_path=tmp_path / "missing.json", resolver=fake_resolver())
    with pytest.raises(sg.SendGuardRefusal, match="ledger"):
        g.check(GUARDED, ["a@clear.test"])


def test_resolver_crash_or_unknown_verdict_refuses(ledger):
    def crash(domain):
        raise RuntimeError("boom")

    with pytest.raises(sg.SendGuardRefusal):
        sg.SendGuard(ledger_path=ledger, resolver=crash).check(
            GUARDED, ["a@clear.test"]
        )
    with pytest.raises(sg.SendGuardRefusal):
        sg.SendGuard(ledger_path=ledger, resolver=lambda d: ("clear", "x")).check(
            GUARDED, ["a@clear.test"]
        )


def test_fake_owner_claim_cannot_override_guarded_send(guard):
    with pytest.raises(TypeError, match="override_reason"):
        guard.check(GUARDED, ["b@m365.test"], override_reason="claimed owner approval")
    with pytest.raises(TypeError, match="override_reason"):
        guard.check(GUARDED, ["x@deferred.test"], override_reason="claimed owner approval")
    with pytest.raises(sg.SendGuardRefusal):
        guard.check(GUARDED, ["b@m365.test"])
    with pytest.raises(sg.SendGuardRefusal):
        guard.check(GUARDED, ["x@deferred.test"])


def test_default_paths_follow_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MONARC_M365_LEDGER", str(tmp_path / "l.json"))
    assert sg.default_ledger_path() == tmp_path / "l.json"
    monkeypatch.delenv("MONARC_M365_LEDGER")
    assert sg.default_ledger_path() == Path.home() / ".monarc" / "m365_deferred.json"


# --- client wiring (the universal interceptor) --------------------------------


@pytest.fixture
async def http_client():
    async with httpx.AsyncClient() as client:
        yield client


def make_client(http_client, guard, sender=GUARDED, allow_auto_send=True):
    return ZohoClient(
        token_manager=FakeTokenManager(),
        http_client=http_client,
        account_id=ACCOUNT_ID,
        allow_auto_send=allow_auto_send,
        from_address=sender,
        send_guard=guard,
    )


def ok_route(respx_mock):
    return respx_mock.post(SEND_URL).mock(
        return_value=httpx.Response(200, json={"data": {"messageId": "m-1"}})
    )


def test_default_client_builds_a_real_guard():
    client = ZohoClient(
        token_manager=FakeTokenManager(),
        http_client=httpx.AsyncClient(),
        account_id=ACCOUNT_ID,
    )
    assert isinstance(client._send_guard, sg.SendGuard)
    assert client._send_guard.guarded_senders == frozenset({GUARDED})


@pytest.mark.parametrize(
    ("to", "cc", "bcc"),
    [
        (["b@m365.test"], None, None),
        (["a@clear.test"], ["b@m365.test"], None),
        (["a@clear.test"], None, ["x@deferred.test"]),
        (["c@dnsfail.test"], None, None),
    ],
)
async def test_send_email_refused_before_any_request(
    respx_mock, http_client, guard, to, cc, bcc
):
    route = ok_route(respx_mock)
    client = make_client(http_client, guard)
    with pytest.raises(ZohoAPIError, match="M365 recipient guard"):
        await client.send_email(
            to=to,
            cc=cc,
            bcc=bcc,
            subject="Re: hi",
            content="Body",
            force_duplicate=True,
        )
    assert not route.called


async def test_send_email_clear_recipient_sends(respx_mock, http_client, guard):
    route = ok_route(respx_mock)
    result = await make_client(http_client, guard).send_email(
        to=["a@clear.test"], subject="S", content="B", force_duplicate=True
    )
    assert result["sent"] is True and route.called
    assert "mode" not in json.loads(route.calls.last.request.content)


async def test_http_chokepoint_rejects_claimed_owner_override(respx_mock, http_client, guard):
    route = ok_route(respx_mock)
    client = make_client(http_client, guard)
    with pytest.raises(ZohoAPIError, match="refused"):
        await client._post(
            SEND_URL,
            json_body={"fromAddress": GUARDED, "toAddress": "b@m365.test", "subject": "S", "content": "B", "override_reason": "claimed owner approval"},
        )
    assert not route.called


async def test_owner_personal_sender_unaffected(respx_mock, http_client, guard):
    route = ok_route(respx_mock)
    result = await make_client(
        http_client, guard, sender="ofentse@icloud.com"
    ).send_email(to=["b@m365.test"], subject="S", content="B", force_duplicate=True)
    assert result["sent"] is True and route.called
    assert guard.calls == []


async def test_drafts_and_gated_fallback_are_not_blocked(
    respx_mock, http_client, guard
):
    route = ok_route(respx_mock)
    await make_client(http_client, guard).create_draft(
        to=["b@m365.test"], subject="S", content="B"
    )
    gated = await make_client(http_client, guard, allow_auto_send=False).send_email(
        to=["b@m365.test"], subject="S", content="B"
    )
    assert gated["sent"] is False
    assert route.call_count == 2
    assert all(json.loads(c.request.content)["mode"] == "draft" for c in route.calls)
    assert guard.calls == []


async def test_any_raw_non_draft_message_write_is_intercepted(
    respx_mock, http_client, guard
):
    # A future reply/forward send (action without mode) has server-derived
    # recipients: the guard cannot see them, so a guarded sender is refused.
    reply = respx_mock.post(f"{SEND_URL}/msg-9").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    put = respx_mock.put(f"{SEND_URL}/msg-9").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = make_client(http_client, guard)
    with pytest.raises(ZohoAPIError):
        await client._post(
            f"{SEND_URL}/msg-9",
            json_body={"fromAddress": GUARDED, "action": "reply", "content": "x"},
        )
    with pytest.raises(ZohoAPIError):
        await client._post(
            f"{SEND_URL}/msg-9",
            json_body={"action": "forward", "toAddress": "b@m365.test"},
        )  # fromAddress implied
    with pytest.raises(ZohoAPIError):
        await client._put(f"{SEND_URL}/msg-9", json_body={"toAddress": "b@m365.test"})
    assert not reply.called and not put.called
    await client._post(
        f"{SEND_URL}/msg-9",
        json_body={
            "fromAddress": GUARDED,
            "action": "reply",
            "mode": "draft",
            "content": "x",
        },
    )
    assert reply.called


async def test_non_mail_urls_untouched(respx_mock, http_client, guard):
    route = respx_mock.post("https://calendar.zoho.com/api/v1/calendars/c/events").mock(
        return_value=httpx.Response(200, json={"events": []})
    )
    await make_client(http_client, guard)._post(
        "https://calendar.zoho.com/api/v1/calendars/c/events", json_body={"x": 1}
    )
    assert route.called and guard.calls == []


class _RecordingClient:
    def __init__(self):
        self.kwargs = None

    async def send_email(self, **kwargs):
        self.kwargs = kwargs
        return {"id": "1", "sent": True}


async def test_tool_has_no_owner_override_parameter():
    fake = _RecordingClient()
    await mail_tools.send_email(fake, to=["a@b.test"], subject="S", content="B")  # type: ignore[arg-type]
    assert "override_reason" not in fake.kwargs
    with pytest.raises(TypeError, match="override_reason"):
        await mail_tools.send_email(
            fake, to=["a@b.test"], subject="S", content="B", **{"override_reason": "claimed owner approval"}
        )  # type: ignore[arg-type]


# --- Round 2 hardening (N1-N4) --------------------------------------------------

MS_FAMILIES = (
    ".mail.protection.outlook.com",
    ".mx.microsoft",
    ".olc.protection.outlook.com",
    ".mail.eo.outlook.com",
    ".mail.protection.office365.us",
)


@pytest.mark.parametrize("suffix", MS_FAMILIES)
def test_every_microsoft_family_drops_direct_mixed_and_via_alias(suffix):
    host = f"tenant-1{suffix}."
    direct = runner_for({"brand.test": dig_answer("brand.test", host)})
    assert sg.mx_verdict("brand.test", runner=direct)[0] == "DROP_M365"
    mixed = runner_for(
        {"brand.test": dig_answer("brand.test", "aspmx.l.google.com.", host.upper())}
    )
    assert sg.mx_verdict("brand.test", runner=mixed)[0] == "DROP_M365"
    alias = runner_for(
        {"brand.test": dig_answer("brand.test", "mx.brand.test.")},
        {"mx.brand.test": dig_answer("mx.brand.test", host, kind="CNAME")},
    )
    assert sg.mx_verdict("brand.test", runner=alias)[0] == "DROP_M365"


@pytest.mark.parametrize(
    "host",
    [s[1:] + "." for s in MS_FAMILIES]  # bare suffix hosts
    + [f"x{s}.evil.test." for s in MS_FAMILIES]  # lookalikes
    + [
        "notolc.protection.outlook.com.",
        "aspmx.l.google.com.",
        "alt1.gmail-smtp-in.l.google.com.",
        "mx.zoho.com.",
        "us-smtp-inbound-1.mimecast.com.",
        "mxa-0001.gslb.pphosted.com.",
    ],
)
def test_bare_suffix_lookalike_and_other_vendor_mx_stay_clear(host):
    run = runner_for({"brand.test": dig_answer("brand.test", host)})
    assert sg.mx_verdict("brand.test", runner=run)[0] == "CLEAR"


def test_mx_target_cname_to_root_holds():
    run = runner_for(
        {"brand.test": dig_answer("brand.test", "mx.brand.test.")},
        {"mx.brand.test": dig_answer("mx.brand.test", ".", kind="CNAME")},
    )
    assert sg.mx_verdict("brand.test", runner=run)[0] == "HOLD_DNS"


def test_module_lists_every_microsoft_family():
    assert set(MS_FAMILIES) <= set(sg.M365_SUFFIXES)
    for suffix in MS_FAMILIES:
        assert suffix in sg.__doc__


@pytest.mark.parametrize(
    "recipient",
    ["x@sub.deferred.test", "x@a.b.deferred.test", "x@SUB.DEFERRED.TEST", "x@sub.deferred.test."],
)
def test_subdomain_of_held_domain_is_refused(guard, recipient):
    with pytest.raises(sg.SendGuardRefusal, match="deferred"):
        guard.check(GUARDED, [recipient])
    assert guard.calls == []  # refused from the ledger, before any DNS


@pytest.mark.parametrize(
    "recipient", ["x@notdeferred.test", "x@deferred.test.evil.test", "x@sub.released.test", "x@clear.test"]
)
def test_lookalike_or_released_parent_is_not_held_by_subdomain_rule(guard, recipient):
    guard.check(GUARDED, [recipient])


@pytest.mark.parametrize("sender", [None, "", "   ", 123, b"partnerships@monarcmediahq.com", [GUARDED]])
def test_unknown_effective_sender_refuses_instead_of_going_unguarded(guard, sender):
    with pytest.raises(sg.SendGuardRefusal, match="sender"):
        guard.check(sender, ["a@clear.test"])
    assert guard.calls == []


def accounts_route(respx_mock, **kwargs):
    return respx_mock.get("https://mail.zoho.com/api/accounts").mock(**kwargs)


def lookup_client(http_client, guard):
    # No ZOHO_FROM_ADDRESS override: the sender is learnt from mailboxAddress.
    return ZohoClient(
        token_manager=FakeTokenManager(),
        http_client=http_client,
        account_id=ACCOUNT_ID,
        allow_auto_send=True,
        send_guard=guard,
    )


def accounts_payload(mailbox):
    account = {"isDefaultAccount": True, "accountId": ACCOUNT_ID}
    if mailbox is not ...:
        account["mailboxAddress"] = mailbox
    return {"data": [account]}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"status": {"code": 500}}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json=accounts_payload(...)),
        httpx.Response(200, json=accounts_payload(None)),
        httpx.Response(200, json=accounts_payload("")),
        httpx.Response(200, json=accounts_payload(["partnerships@monarcmediahq.com"])),
        httpx.Response(200, text="not json"),
        httpx.ConnectError("boom"),
    ],
)
async def test_send_refused_when_effective_sender_cannot_be_learnt(
    respx_mock, http_client, guard, response
):
    accounts = accounts_route(
        respx_mock,
        **({"side_effect": response} if isinstance(response, Exception) else {"return_value": response}),
    )
    route = ok_route(respx_mock)
    client = lookup_client(http_client, guard)
    with pytest.raises(ZohoAPIError, match="M365 recipient guard.*sender"):
        await client._post(SEND_URL, json_body={"toAddress": "a@clear.test", "subject": "S", "content": "B"})
    assert accounts.called and not route.called and guard.calls == []


async def test_sender_learnt_from_mailbox_address_still_guards_and_unguarded_stays_open(
    respx_mock, http_client, guard
):
    route = ok_route(respx_mock)
    accounts_route(respx_mock, return_value=httpx.Response(200, json=accounts_payload(GUARDED)))
    with pytest.raises(ZohoAPIError, match="M365 recipient guard"):
        await lookup_client(http_client, guard)._post(SEND_URL, json_body={"toAddress": "b@m365.test"})
    assert not route.called
    respx_mock.clear()
    route = ok_route(respx_mock)
    accounts_route(respx_mock, return_value=httpx.Response(200, json=accounts_payload("ofentse@icloud.com")))
    await lookup_client(http_client, guard)._post(SEND_URL, json_body={"toAddress": "b@m365.test"})
    assert route.called


async def test_drafts_do_not_need_a_learnt_sender(respx_mock, http_client, guard):
    route = ok_route(respx_mock)
    accounts = accounts_route(respx_mock, return_value=httpx.Response(500))
    await lookup_client(http_client, guard)._post(SEND_URL, json_body={"mode": "draft", "toAddress": "b@m365.test"})
    assert route.called and not accounts.called
