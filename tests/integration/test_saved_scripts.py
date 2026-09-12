"""Saved worksheet scripts: they belong to the account that saved them.

A saved script is the one piece of worksheet state that outlives a session, so the
checks here are about what happens to it afterwards -- that it is listed, that a
delete really removes it, and that neither is visible to another account.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _save(client: TestClient, headers: dict, name: str, profile_id: str | None = None) -> str:
    payload: dict = {"name": name, "body": "select 1 from dual"}
    if profile_id is not None:
        payload["profileId"] = profile_id
    response = client.post("/api/v1/scripts", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _names(client: TestClient, headers: dict) -> list[str]:
    response = client.get("/api/v1/scripts", headers=headers)
    assert response.status_code == 200, response.text
    return [row["name"] for row in response.json()["scripts"]]


def test_a_saved_script_is_listed_with_its_body(client: TestClient, developer, targets) -> None:
    profile_id = targets["development"]["id"]
    _save(client, developer, "daily check", profile_id)

    scripts = client.get("/api/v1/scripts", headers=developer).json()["scripts"]
    assert [s["name"] for s in scripts] == ["daily check"]
    assert scripts[0]["body"] == "select 1 from dual"
    assert scripts[0]["profileId"] == profile_id
    assert scripts[0]["updatedAt"]


def test_deleting_a_script_removes_it(client: TestClient, developer, targets) -> None:
    """The delete has to reach the row, not just report that it did."""

    profile_id = targets["development"]["id"]
    keep = _save(client, developer, "keep", profile_id)
    discard = _save(client, developer, "discard", profile_id)

    response = client.delete(f"/api/v1/scripts/{discard}", headers=developer)
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "scriptId": discard}

    assert _names(client, developer) == ["keep"]

    # And it is gone for good: deleting it again finds nothing to delete.
    again = client.delete(f"/api/v1/scripts/{discard}", headers=developer)
    assert again.status_code == 404
    assert _names(client, developer) == ["keep"]
    assert keep


def test_deleting_a_script_that_does_not_exist_is_a_404(client: TestClient, developer) -> None:
    response = client.delete("/api/v1/scripts/scr_nothing", headers=developer)
    assert response.status_code == 404
    assert response.json()["error"]["detail"]["scriptId"] == "scr_nothing"


def test_another_account_can_neither_see_nor_delete_your_script(
    client: TestClient, developer, second_developer, targets
) -> None:
    script_id = _save(client, developer, "mine", targets["development"]["id"])

    assert _names(client, second_developer) == []

    response = client.delete(f"/api/v1/scripts/{script_id}", headers=second_developer)
    assert response.status_code == 404
    # Refusing is not enough; the script has to still be there afterwards.
    assert _names(client, developer) == ["mine"]
