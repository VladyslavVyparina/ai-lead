from app.adapters.hosting import LocalStubAdapter, VercelAdapter


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"id": "dpl_123", "url": "preview-example.vercel.app"}


def test_local_stub_url_is_deterministic():
    adapter = LocalStubAdapter()
    first = adapter.deploy(
        template_id="wellness",
        template_version="v1",
        site_config={"business": "A"},
    )
    second = adapter.deploy(
        template_id="wellness",
        template_version="v1",
        site_config={"business": "A"},
    )
    assert first["url"] == second["url"]


def test_vercel_adapter_uses_configured_project_and_repository(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("app.adapters.hosting.httpx.post", fake_post)
    adapter = VercelAdapter(
        token="token",
        project_id="prj_123",
        github_repo_id="987654",
        git_ref="main",
        team_id="team_123",
    )
    result = adapter.deploy(
        template_id="wellness-next-template",
        template_version="v1",
        site_config={"business_name": {"value": "Spa"}},
    )

    assert result["status"] == "deployed"
    assert result["deployment_id"] == "dpl_123"
    assert captured["params"] == {"forceNew": "1", "teamId": "team_123"}
    payload = captured["json"]
    assert payload["project"] == "prj_123"
    assert payload["gitSource"] == {
        "type": "github",
        "repoId": "987654",
        "ref": "main",
    }
    assert "SITE_CONFIG" in payload["env"]
