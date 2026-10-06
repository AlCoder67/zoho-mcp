"""get_email must report the message's REAL folder and refuse a wrong folder_id.

Zoho's ``/folders/{folderId}/messages/{id}/content`` endpoint ignores the
folder in its path: the same message id returns the same body for any folder
id (reproduced live 2026-10-06 for a Trashed draft queried as Trash and as
Drafts). A caller that "confirmed" a message was in Sent by fetching it with
the Sent folder id therefore confirmed nothing (incident 149: a Drafts item
was reported as sent and a CRM row was closed on it). The ``/details``
endpoint returns the real ``folderId`` whatever folder is in the path.
"""

import httpx
import pytest

from zoho_mcp.zoho.client import ZohoAPIError, ZohoClient

ACCOUNT_ID = "acct-123"
MSG = "1790704686004155000"
SENT = "2301189000000008022"
DRAFTS = "2301189000000008016"
TRASH = "2301189000000008026"
BASE = f"https://mail.zoho.com/api/accounts/{ACCOUNT_ID}"


class FakeTokenManager:
    async def get_access_token(self) -> str:
        return "fake-access-token"


@pytest.fixture
async def http_client():
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def zoho_client(http_client):
    return ZohoClient(
        token_manager=FakeTokenManager(),
        http_client=http_client,
        account_id=ACCOUNT_ID,
        calendar_uid="cal-1",
    )


def mock_message(respx_mock, *, real_folder, path_folder, with_folders=True):
    """The API answers /content AND /details for ANY folder in the path."""
    respx_mock.get(f"{BASE}/folders/{path_folder}/messages/{MSG}/content").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"messageId": MSG, "content": "<p>Hello there</p>"}},
        )
    )
    respx_mock.get(f"{BASE}/folders/{path_folder}/messages/{MSG}/details").mock(
        return_value=httpx.Response(
            200, json={"data": {"messageId": MSG, "folderId": real_folder}}
        )
    )
    if with_folders:
        respx_mock.get(f"{BASE}/folders").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"folderId": SENT, "folderType": "Sent"},
                        {"folderId": DRAFTS, "folderType": "Drafts"},
                        {"folderId": TRASH, "folderType": "Trash"},
                    ]
                },
            )
        )


async def test_checked_get_reports_the_real_folder(respx_mock, zoho_client):
    mock_message(respx_mock, real_folder=DRAFTS, path_folder=DRAFTS)

    result = await zoho_client.get_email_checked(MSG, DRAFTS)

    assert result["id"] == MSG
    assert "Hello there" in result["text"]
    assert result["folder_id"] == DRAFTS
    assert result["folder_type"] == "Drafts"


async def test_checked_get_refuses_when_message_is_in_another_folder(
    respx_mock, zoho_client
):
    # The caller says Sent, the message really is a Drafts item.
    mock_message(respx_mock, real_folder=DRAFTS, path_folder=SENT)

    with pytest.raises(ZohoAPIError) as err:
        await zoho_client.get_email_checked(MSG, SENT)

    text = str(err.value)
    assert DRAFTS in text and SENT in text
    assert "not" in text.lower()


async def test_checked_get_refuses_a_trashed_message_asked_for_as_sent(
    respx_mock, zoho_client
):
    mock_message(respx_mock, real_folder=TRASH, path_folder=SENT)

    with pytest.raises(ZohoAPIError):
        await zoho_client.get_email_checked(MSG, SENT)


async def test_checked_get_compares_ids_as_strings(respx_mock, zoho_client):
    # Zoho returns some ids as numbers and some as strings.
    mock_message(respx_mock, real_folder=int(SENT), path_folder=SENT)

    result = await zoho_client.get_email_checked(MSG, SENT)

    assert result["folder_id"] == SENT


async def test_checked_get_fails_closed_when_details_has_no_folder(
    respx_mock, zoho_client
):
    respx_mock.get(f"{BASE}/folders/{SENT}/messages/{MSG}/details").mock(
        return_value=httpx.Response(200, json={"data": {"messageId": MSG}})
    )
    content = respx_mock.get(f"{BASE}/folders/{SENT}/messages/{MSG}/content").mock(
        return_value=httpx.Response(
            200, json={"data": {"messageId": MSG, "content": "<p>x</p>"}}
        )
    )

    with pytest.raises(ZohoAPIError):
        await zoho_client.get_email_checked(MSG, SENT)
    # It must not hand back a body it could not place.
    assert not content.called


async def test_checked_get_fails_closed_when_details_request_fails(
    respx_mock, zoho_client
):
    respx_mock.get(f"{BASE}/folders/{SENT}/messages/{MSG}/details").mock(
        return_value=httpx.Response(500, json={"status": {"code": 500}})
    )

    with pytest.raises(ZohoAPIError):
        await zoho_client.get_email_checked(MSG, SENT)


async def test_checked_get_still_returns_folder_id_if_type_lookup_fails(
    respx_mock, zoho_client
):
    mock_message(respx_mock, real_folder=SENT, path_folder=SENT, with_folders=False)
    respx_mock.get(f"{BASE}/folders").mock(return_value=httpx.Response(500))

    result = await zoho_client.get_email_checked(MSG, SENT)

    assert result["folder_id"] == SENT
    assert result["folder_type"] is None


async def test_plain_get_email_is_unchanged_for_internal_callers(
    respx_mock, zoho_client
):
    # The duplicate guard and send_email(source_draft_id) call get_email
    # directly; their behavior and request count must not change.
    mock_message(respx_mock, real_folder=DRAFTS, path_folder=SENT)

    result = await zoho_client.get_email(MSG, SENT)

    assert set(result) == {"id", "text"}
