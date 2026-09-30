"""Minimal client for the GNS3 v3 controller REST API.

GNS3 3.x controllers expose a `/v3`-prefixed REST API secured with JWT
bearer tokens (`POST /v3/access/users/login`), which is a different scheme
from the GNS3 v2 API (HTTP Basic Auth, `/v2` prefix) that older clients such
as `gns3fy` target. This client talks to the v3 API directly, based on the
server's own OpenAPI spec (`/openapi.json`).
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

from .exceptions import Gns3ApiError


class Gns3Client:
    def __init__(self, base_url: str, username: str, password: str, verify: bool = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify = verify
        self.session = requests.Session()
        self.session.verify = verify
        self._token: Optional[str] = None

    # -- auth -----------------------------------------------------------
    def authenticate(self) -> str:
        resp = self.session.post(
            f"{self.base_url}/v3/access/users/login",
            data={"username": self.username, "password": self.password},
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self.session.headers["Authorization"] = f"Bearer {self._token}"
        return self._token

    def _ensure_auth(self) -> None:
        if self._token is None:
            self.authenticate()

    def _send_with_retry(self, method: str, url: str, retries: int, **kwargs):
        # Some GNS3 setups (e.g. behind a proxy) drop keep-alive connections
        # after just one or two requests. Recreate the session (fresh TCP
        # connection) and retry on transient connection errors instead of
        # failing the whole call.
        last_error: Exception = RuntimeError("no attempt made")
        for attempt in range(retries + 1):
            try:
                return self.session.request(method, url, **kwargs)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_error = exc
                self.session.close()
                self.session = requests.Session()
                self.session.verify = self.verify
                if self._token is not None:
                    self.session.headers["Authorization"] = f"Bearer {self._token}"
                time.sleep(0.5 * (attempt + 1))
        raise last_error

    # -- low level --------------------------------------------------------
    def _request(self, method: str, path: str, retries: int = 3, **kwargs) -> Any:
        self._ensure_auth()
        url = f"{self.base_url}{path}"
        resp = self._send_with_retry(method, url, retries, **kwargs)
        if resp.status_code == 401 and self._token is not None:
            # token may have expired; retry once after re-authenticating
            self.authenticate()
            resp = self._send_with_retry(method, url, retries, **kwargs)
        if not resp.ok:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise Gns3ApiError(resp.status_code, detail, method, path)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def get(self, path: str, **kwargs) -> Any:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, json: Any = None, **kwargs) -> Any:
        return self._request("POST", path, json=json, **kwargs)

    def put(self, path: str, json: Any = None, **kwargs) -> Any:
        return self._request("PUT", path, json=json, **kwargs)

    def delete(self, path: str, **kwargs) -> Any:
        return self._request("DELETE", path, **kwargs)

    # -- server info ------------------------------------------------------
    def version(self) -> dict:
        return self.get("/v3/version")

    # -- projects -----------------------------------------------------------
    def list_projects(self) -> list[dict]:
        return self.get("/v3/projects")

    def get_project(self, project_id: str) -> dict:
        return self.get(f"/v3/projects/{project_id}")

    def find_project(self, name: str) -> Optional[dict]:
        for project in self.list_projects():
            if project["name"] == name:
                return project
        return None

    def create_project(self, name: str, **kwargs) -> dict:
        return self.post("/v3/projects", json={"name": name, **kwargs})

    def open_project(self, project_id: str) -> dict:
        return self.post(f"/v3/projects/{project_id}/open")

    def close_project(self, project_id: str) -> dict:
        return self.post(f"/v3/projects/{project_id}/close")

    def delete_project(self, project_id: str) -> None:
        self.delete(f"/v3/projects/{project_id}")

    # -- nodes ------------------------------------------------------------
    def list_nodes(self, project_id: str) -> list[dict]:
        return self.get(f"/v3/projects/{project_id}/nodes")

    def get_node(self, project_id: str, node_id: str) -> dict:
        return self.get(f"/v3/projects/{project_id}/nodes/{node_id}")

    def find_node(self, project_id: str, name: str) -> Optional[dict]:
        for node in self.list_nodes(project_id):
            if node["name"] == name:
                return node
        return None

    def create_node(self, project_id: str, **kwargs) -> dict:
        return self.post(f"/v3/projects/{project_id}/nodes", json=kwargs)

    def update_node(self, project_id: str, node_id: str, **kwargs) -> dict:
        return self.put(f"/v3/projects/{project_id}/nodes/{node_id}", json=kwargs)

    def delete_node(self, project_id: str, node_id: str) -> None:
        self.delete(f"/v3/projects/{project_id}/nodes/{node_id}")

    def start_node(self, project_id: str, node_id: str) -> dict:
        return self.post(f"/v3/projects/{project_id}/nodes/{node_id}/start")

    def stop_node(self, project_id: str, node_id: str) -> dict:
        return self.post(f"/v3/projects/{project_id}/nodes/{node_id}/stop")

    def suspend_node(self, project_id: str, node_id: str) -> dict:
        return self.post(f"/v3/projects/{project_id}/nodes/{node_id}/suspend")

    def reload_node(self, project_id: str, node_id: str) -> dict:
        return self.post(f"/v3/projects/{project_id}/nodes/{node_id}/reload")

    def start_all_nodes(self, project_id: str) -> None:
        self.post(f"/v3/projects/{project_id}/nodes/start")

    def stop_all_nodes(self, project_id: str) -> None:
        self.post(f"/v3/projects/{project_id}/nodes/stop")

    # -- links --------------------------------------------------------------
    def list_links(self, project_id: str) -> list[dict]:
        return self.get(f"/v3/projects/{project_id}/links")

    def create_link(
        self,
        project_id: str,
        node_a_id: str,
        port_a: int,
        node_b_id: str,
        port_b: int,
        adapter_a: int = 0,
        adapter_b: int = 0,
    ) -> dict:
        payload = {
            "nodes": [
                {"node_id": node_a_id, "adapter_number": adapter_a, "port_number": port_a},
                {"node_id": node_b_id, "adapter_number": adapter_b, "port_number": port_b},
            ]
        }
        return self.post(f"/v3/projects/{project_id}/links", json=payload)

    def delete_link(self, project_id: str, link_id: str) -> None:
        self.delete(f"/v3/projects/{project_id}/links/{link_id}")

    # -- templates ------------------------------------------------------------
    def list_templates(self) -> list[dict]:
        return self.get("/v3/templates")

    def find_template(self, name: str) -> Optional[dict]:
        for template in self.list_templates():
            if template["name"] == name:
                return template
        return None

    def create_node_from_template(
        self, project_id: str, template_id: str, x: int = 0, y: int = 0
    ) -> dict:
        return self.post(
            f"/v3/projects/{project_id}/templates/{template_id}", json={"x": x, "y": y}
        )
