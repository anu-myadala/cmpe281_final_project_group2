"""Guards for the docker compose switch-over (step 10), so the override files can't silently
drift from the shared ones they replace:

* c3/deploy/nginx-default.conf must stay C1's c1/web/nginx/default.conf with exactly one line
  changed (/c3/ -> c3:8003). If C1 edits its file, this fails until the copy is refreshed.
* docker-compose.c3.yml must keep the pieces the switch depends on.
"""
from __future__ import annotations

import difflib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


class _ComposeLoader(yaml.SafeLoader):
    """Accepts docker compose's !override / !reset tags (as plain values)."""


_ComposeLoader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node)
                               if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node))
_ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def _compose() -> dict:
    return yaml.load((REPO / "docker-compose.c3.yml").read_text(), Loader=_ComposeLoader)


def test_nginx_copy_differs_from_c1s_file_by_the_c3_upstream_only():
    c1 = (REPO / "c1" / "web" / "nginx" / "default.conf").read_text().splitlines()
    ours = (REPO / "c3" / "deploy" / "nginx-default.conf").read_text().splitlines()
    changed = [line for line in difflib.unified_diff(c1, ours, lineterm="", n=0)
               if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    assert changed == ["-upstream c3_stub  { server stubs:8003; }", "+upstream c3_stub  { server c3:8003; }"]


def test_compose_override_switches_c1_to_c3():
    services = _compose()["services"]
    c3 = services["c3"]
    assert c3["build"]["context"] == "./c3"
    assert c3["depends_on"]["seed"]["condition"] == "service_completed_successfully"
    assert "8003/api/v1/health" in " ".join(c3["healthcheck"]["test"])
    assert {k: c3["environment"][k] for k in ("C1_BASE_URL", "PUBLISHER", "MAIL_MODE", "SMTP_HOST", "SMTP_PORT")} == {
        "C1_BASE_URL": "http://api:8000/api/v1", "PUBLISHER": "http", "MAIL_MODE": "smtp", "SMTP_HOST": "mailhog",
        "SMTP_PORT": "1025"}
    assert services["api"]["environment"]["C3_BASE_URL"] == "http://c3:8003/api/v1"


def test_compose_override_keeps_c2_and_stops_the_stubs_c3():
    services = _compose()["services"]
    stubs = services["stubs"]
    assert stubs["command"] == ["python", "/srv/stubs_c2_only.py"]
    assert stubs["environment"]["DEMO_MAX_ALERTS"] == "0"
    assert stubs["ports"] == ["127.0.0.1:8002:8002"]                     # no 8003 any more
    assert "./c3/deploy/stubs_c2_only.py:/srv/stubs_c2_only.py:ro" in stubs["volumes"]
    assert "./c3/deploy/nginx-default.conf:/etc/nginx/conf.d/default.conf:ro" in services["web"]["volumes"]
    assert (REPO / "c3" / "deploy" / "stubs_c2_only.py").exists()


def test_stubs_c2_only_serves_the_c2_app_only():
    source = (REPO / "c3" / "deploy" / "stubs_c2_only.py").read_text()
    assert "uvicorn.Config(c2_app" in source and "uvicorn.Config(_c3" not in source
