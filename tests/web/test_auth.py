"""Authentication covers every HTTP route, including mounted assets."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from ledger.web import server


@pytest.mark.parametrize("path", ["/browse", "/healthz", "/graph/data.json", "/static/style.css", "/openapi.json"])
def test_requires_auth(web_ledger_root, path):
    app = server.create_app()
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/admin/reload").status_code == 401


def test_query_token_sets_cookie(web_ledger_root, monkeypatch):
    monkeypatch.setenv("LEDGER_WEB_TOKEN", "example-token")
    app = server.create_app()
    with TestClient(app) as client:
        assert client.get("/healthz?token=wrong").status_code == 401
        response = client.get("/healthz?token=example-token")
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert response.headers["referrer-policy"] == "no-referrer"
        assert client.get("/healthz").status_code == 200
        client.cookies.clear()
        assert client.get("/healthz", headers={"Authorization": "Bearer example-token"}).status_code == 200
        assert client.get("/healthz?token=%C3%A9").status_code == 401


def test_generated_tokens_are_random(web_ledger_root, monkeypatch):
    monkeypatch.delenv("LEDGER_WEB_TOKEN", raising=False)
    first = server.create_app().state.auth_token
    assert len(first) >= 32
    assert first != server.create_app().state.auth_token


def test_remote_binding_requires_opt_in(monkeypatch):
    monkeypatch.delenv("LEDGER_WEB_ALLOW_REMOTE", raising=False)
    with pytest.raises(ValueError, match="LEDGER_WEB_ALLOW_REMOTE"):
        server.run(host="0.0.0.0")


@pytest.mark.parametrize("reload", [False, True])
def test_startup_token_matches_app(web_ledger_root, monkeypatch, capsys, reload):
    monkeypatch.delenv("LEDGER_WEB_TOKEN", raising=False)
    monkeypatch.setenv("LEDGER_WEB_ALLOW_REMOTE", "1")
    uvicorn = Mock()
    tokens = []

    def launch(app, **kwargs):
        if kwargs.get("factory"):
            app = server.create_app()
        tokens.append(app.state.auth_token)

    uvicorn.run.side_effect = launch
    monkeypatch.setattr(server, "_require_uvicorn", lambda: uvicorn)
    server.run(host="0.0.0.0", reload=reload)
    assert f"?token={tokens[0]}" in capsys.readouterr().err
