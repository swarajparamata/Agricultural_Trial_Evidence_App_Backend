import pytest

from tests.conftest import ADMIN, VIEWER, login


def test_login_and_me(client):
    response = client.post("/api/auth/login", json={"email": "ADMIN@agrievidence.demo ", "password": "admin123"})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "admin"
    assert body["expires_at"].endswith("Z")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me["email"] == "admin@agrievidence.demo"
    assert me["last_login_at"] is not None


def test_wrong_password_and_missing_token(client):
    assert client.post("/api/auth/login", json={"email": ADMIN[0], "password": "nope"}).status_code == 401
    response = client.get("/api/trials")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not signed in."
    assert client.get("/api/trials", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_oauth2_form_login_for_api_docs(client):
    response = client.post("/api/auth/token", data={"username": VIEWER[0], "password": VIEWER[1]})
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/trials", {"id": "T99", "crop": "Wheat", "product": "X", "country": "France", "year": 2024, "trial_type": "Scientific", "treatment_yield": 1}),
        ("patch", "/api/trials/T01", {"crop": "Barley"}),
        ("delete", "/api/trials/T01", None),
        ("post", "/api/trials/T03/conflicts/treatment_yield/resolve", {"value": 44}),
        ("get", "/api/users", None),
        ("post", "/api/users", {"email": "new@x.org", "password": "secret1"}),
        ("put", "/api/settings/display", {"app_name": "Mine"}),
        ("post", "/api/data/rebuild", None),
        ("get", "/api/data/files", None),
        ("get", "/api/audit", None),
        ("post", "/api/demo/reset", None),
        ("post", "/api/assistant/test", {}),
    ],
)
def test_viewers_cannot_use_admin_endpoints(client, viewer, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    response = getattr(client, method)(path, headers=viewer, **kwargs)
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Only admins can do this."


def test_viewers_can_read(client, viewer):
    assert client.get("/api/trials", headers=viewer).status_code == 200
    assert client.get("/api/trials/T03", headers=viewer).status_code == 200
    assert client.get("/api/settings", headers=viewer).status_code == 200
    assert client.post("/api/compare", headers=viewer, json={"trial_ids": ["T01", "T02"]}).status_code == 200


def test_signup_is_off_by_default(client, admin):
    body = {"email": "farmer@example.org", "password": "secret1"}
    assert client.post("/api/auth/signup", json=body).status_code == 403
    client.put("/api/settings/access", headers=admin, json={"allow_self_signup": True})
    response = client.post("/api/auth/signup", json=body)
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "viewer"
    assert client.get("/api/meta").json()["allow_signup"] is True


def test_change_password(client, viewer):
    bad = client.post("/api/auth/change-password", headers=viewer, json={"current_password": "x", "new_password": "newpass1"})
    assert bad.status_code == 400
    ok = client.post("/api/auth/change-password", headers=viewer, json={"current_password": VIEWER[1], "new_password": "newpass1"})
    assert ok.status_code == 204
    login(client, VIEWER[0], "newpass1")


def test_admin_manages_users(client, admin):
    created = client.post(
        "/api/users", headers=admin, json={"email": "Agronomist@Example.org", "full_name": "Ana", "password": "secret1", "role": "viewer"}
    )
    assert created.status_code == 201
    user = created.json()
    assert user["email"] == "agronomist@example.org"
    assert client.post("/api/users", headers=admin, json={"email": "agronomist@example.org", "password": "secret1"}).status_code == 409

    promoted = client.patch(f"/api/users/{user['id']}", headers=admin, json={"role": "admin"}).json()
    assert promoted["role"] == "admin"
    assert client.get("/api/users", headers=login(client, "agronomist@example.org", "secret1")).status_code == 200

    client.patch(f"/api/users/{user['id']}", headers=admin, json={"is_active": False})
    assert client.post("/api/auth/login", json={"email": "agronomist@example.org", "password": "secret1"}).status_code == 401

    assert client.delete(f"/api/users/{user['id']}", headers=admin).status_code == 204
    emails = [u["email"] for u in client.get("/api/users", headers=admin).json()]
    assert emails == [ADMIN[0], VIEWER[0]]


def test_admins_cannot_lock_themselves_out(client, admin):
    me = client.get("/api/auth/me", headers=admin).json()
    assert client.patch(f"/api/users/{me['id']}", headers=admin, json={"role": "viewer"}).status_code == 400
    assert client.patch(f"/api/users/{me['id']}", headers=admin, json={"is_active": False}).status_code == 400
    assert client.delete(f"/api/users/{me['id']}", headers=admin).status_code == 400


def test_last_admin_is_protected(client, admin):
    other = client.post("/api/users", headers=admin, json={"email": "boss@example.org", "password": "secret1", "role": "admin"}).json()
    boss = login(client, "boss@example.org", "secret1")
    me = client.get("/api/auth/me", headers=admin).json()
    # Two admins: the other one may demote the demo admin, but then the last admin can't be removed.
    assert client.patch(f"/api/users/{me['id']}", headers=boss, json={"role": "viewer"}).status_code == 200
    assert client.delete(f"/api/users/{other['id']}", headers=boss).status_code == 400


def test_deactivated_user_token_stops_working(client, admin):
    viewer_headers = login(client, *VIEWER)
    viewer_id = client.get("/api/auth/me", headers=viewer_headers).json()["id"]
    client.patch(f"/api/users/{viewer_id}", headers=admin, json={"is_active": False})
    assert client.get("/api/trials", headers=viewer_headers).status_code == 401


def test_meta_lists_demo_accounts(client):
    meta = client.get("/api/meta").json()
    assert meta["demo_mode"] is True
    assert [account["email"] for account in meta["demo_accounts"]] == [ADMIN[0], VIEWER[0]]
