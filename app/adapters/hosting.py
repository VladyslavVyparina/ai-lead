"""Hosting adapter contract (§5.2): template version + config → deploy status,
URL, logs, expiry and teardown. Vercel first, provider-replaceable."""

import hashlib
import json
from typing import Protocol

import httpx

from app.config import settings


class HostingAdapter(Protocol):
    def deploy(self, *, template_id: str, template_version: str, site_config: dict) -> dict: ...

    def teardown(self, url: str) -> bool: ...


class HostingConfigurationError(RuntimeError):
    pass


class LocalStubAdapter:
    """Deterministic stub for development and the load test: no network,
    returns a stable pseudo-URL derived from the config hash."""

    def deploy(self, *, template_id: str, template_version: str, site_config: dict) -> dict:
        digest = hashlib.sha256(
            json.dumps(site_config, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return {
            "status": "deployed",
            "url": f"https://preview-{digest}.local.test",
            "logs": [f"stub deploy {template_id}@{template_version}"],
        }

    def teardown(self, url: str) -> bool:
        return True


class VercelAdapter:
    """Thin Vercel REST wrapper. Requires VERCEL_TOKEN; deployment expects the
    template project to already exist (templates are audited/published separately)."""

    BASE = "https://api.vercel.com"

    def __init__(
        self,
        token: str,
        project_id: str,
        github_repo_id: str,
        git_ref: str = "main",
        team_id: str = "",
    ):
        self.token = token
        self.project_id = project_id
        self.github_repo_id = github_repo_id
        self.git_ref = git_ref
        self.team_id = team_id

    def _params(self) -> dict:
        params = {"forceNew": "1"}
        if self.team_id:
            params["teamId"] = self.team_id
        return params

    def deploy(self, *, template_id: str, template_version: str, site_config: dict) -> dict:
        response = httpx.post(
            f"{self.BASE}/v13/deployments",
            headers={"Authorization": f"Bearer {self.token}"},
            params=self._params(),
            json={
                "name": template_id,
                "project": self.project_id,
                "target": "preview",
                "gitSource": {
                    "type": "github",
                    "repoId": self.github_repo_id,
                    "ref": self.git_ref,
                },
                "env": {"SITE_CONFIG": json.dumps(site_config, default=str)},
                "meta": {
                    "template_id": template_id,
                    "template_version": template_version,
                },
            },
            timeout=60.0,
        )
        if response.status_code >= 400:
            return {"status": "failed", "url": "", "logs": [response.text]}
        data = response.json()
        return {
            "status": "deployed",
            "url": f"https://{data.get('url', '')}",
            "deployment_id": data.get("id", ""),
            "logs": [],
        }

    def teardown(self, url: str) -> bool:
        deployment_id = url.removeprefix("https://")
        response = httpx.delete(
            f"{self.BASE}/v13/deployments/{deployment_id}",
            headers={"Authorization": f"Bearer {self.token}"},
            params=self._params(),
            timeout=30.0,
        )
        return response.status_code < 400


def get_hosting_adapter() -> HostingAdapter:
    vercel_values = (
        settings.vercel_token,
        settings.vercel_project_id,
        settings.vercel_github_repo_id,
    )
    if any(vercel_values):
        if not all(vercel_values):
            raise HostingConfigurationError(
                "Vercel requires VERCEL_TOKEN, VERCEL_PROJECT_ID, and "
                "VERCEL_GITHUB_REPO_ID"
            )
        return VercelAdapter(
            token=settings.vercel_token,
            project_id=settings.vercel_project_id,
            github_repo_id=settings.vercel_github_repo_id,
            git_ref=settings.vercel_git_ref,
            team_id=settings.vercel_team_id,
        )
    return LocalStubAdapter()
