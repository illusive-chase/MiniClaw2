from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from miniclaw2.app import create_app
from miniclaw2.artifacts import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS_TOTAL_BYTES,
    publish_artifacts,
    workspace_artifacts_dir,
)
from miniclaw2.domain import Category, Node, NodeState, Project
from miniclaw2.registry import ProjectRegistry
from miniclaw2.store import Store


SVG_FIXTURES = {
    "safe.svg": '''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80">
      <style>rect { fill: rgb(0, 128, 0) }</style><rect width="160" height="80"/>
      <image x="120" width="40" height="40" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect width='40' height='40' fill='red'/%3E%3C/svg%3E"/>
    </svg>''',
    "active.svg": '''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80"
      onload="fetch('/attack-onload'); top.__svgAttack = true">
      <script>fetch('/attack-script'); top.__svgAttack = true; window.open('/attack-popup')</script>
      <a href="/attack-navigation" target="_top"><rect width="160" height="80" fill="green"/></a>
      <foreignObject width="100" height="80"><div xmlns="http://www.w3.org/1999/xhtml">
        <iframe src="/attack-frame"/><img src="/attack-html-image"/>
        <form action="/attack-form"><input type="submit"/></form>
      </div></foreignObject>
    </svg>''',
    "external.svg": '''<?xml version="1.0"?><?xml-stylesheet href="/attack-stylesheet" type="text/css"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="160" height="80">
      <style>@import url('/attack-import'); @font-face { font-family: remote; src: url('/attack-font') } text { font-family: remote }</style>
      <rect width="160" height="80" fill="green"/><text y="20">外部字体</text>
      <image href="/attack-image" width="40" height="40"/><use href="/attack-use#shape"/>
    </svg>''',
    "entity.svg": '''<?xml version="1.0"?>
    <!DOCTYPE svg [<!ENTITY external SYSTEM "/attack-entity">]>
    <svg xmlns="http://www.w3.org/2000/svg" width="160" height="80">
      <rect width="160" height="80" fill="green"/><text y="20">&external;</text>
    </svg>''',
    "data.svg": '''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80">
      <a href="data:text/html,%3Cscript%3Eopener.__svgAttack=true%3C/script%3E" target="_blank" download="attack.html">
        <rect width="160" height="80" fill="green"/>
      </a>
      <image width="40" height="40" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' onload='top.__svgAttack=true'/%3E"/>
    </svg>''',
}


def browser_responses() -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with patch.dict(os.environ, {
            "MINICLAW_HOME": str(root / "store"),
            "MINICLAW_CONTEXT_HOME": str(root / "context"),
        }):
            registry = ProjectRegistry(initialize=False)
            project = registry.create_project(str(root))
            node = Node(id="svg-node", project_id=project.id, category=Category.REGULAR, state=NodeState.DONE, model_preset_id="opus-4-8")
            registry.store.create_node(node)
            outputs = workspace_artifacts_dir(project, node.id)
            outputs.mkdir(parents=True)
            for name, content in SVG_FIXTURES.items():
                (outputs / name).write_text(content, encoding="utf-8")
            publish_artifacts(project, node, list(SVG_FIXTURES), registry.store)
            registry.store.update_node(node)
            client = TestClient(create_app(registry))
            try:
                responses = {}
                for name in SVG_FIXTURES:
                    url = f"/sessions/{project.id}/nodes/{node.id}/artifacts/{name}"
                    response = client.get(url, params={"raw": 1})
                    assert response.status_code == 200
                    responses[name] = {"headers": dict(response.headers), "body": response.text}
                return responses
            finally:
                client.close()


def test_svg_active_content_remains_downloadable_but_not_an_active_document() -> None:
    for name, response in browser_responses().items():
        assert response["body"] == SVG_FIXTURES[name]
        policy = response["headers"]["content-security-policy"]
        assert "default-src 'none'" in policy
        assert "allow-scripts" not in policy
        assert "allow-same-origin" not in policy
        assert "img-src data:" in policy
        assert response["headers"]["content-disposition"].startswith("attachment;")


def test_svg_publication_enforces_size_extension_and_directory_limits(tmp_path: Path) -> None:
    store = Store(root=tmp_path / "store")
    project = Project(root_path=str(tmp_path / "project"))
    node = Node(project_id=project.id, category=Category.REGULAR, model_preset_id="opus-4-8")
    outputs = workspace_artifacts_dir(project, node.id)
    outputs.mkdir(parents=True)
    (outputs / "large.svg").write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
    (outputs / "wrong.svgz").write_text(SVG_FIXTURES["safe.svg"], encoding="utf-8")
    outside = tmp_path / "outside.svg"
    outside.write_text(SVG_FIXTURES["safe.svg"], encoding="utf-8")
    (outputs / "escape.svg").symlink_to(outside)
    names = ["large.svg", "wrong.svgz", "escape.svg", "../outside.svg"]
    for index in range(MAX_ARTIFACTS_TOTAL_BYTES // MAX_ARTIFACT_BYTES + 1):
        name = f"budget-{index}.svg"
        (outputs / name).write_bytes(b"x" * MAX_ARTIFACT_BYTES)
        names.append(name)
    refs = publish_artifacts(project, node, names, store)
    assert all(ref.status == "dropped" for ref in refs[:4])
    assert "MiB cap" in (refs[0].reason or "")
    assert "suffix" in (refs[1].reason or "")
    assert "outside" in (refs[2].reason or "")
    assert "bare filename" in (refs[3].reason or "")
    assert all(ref.status == "published" for ref in refs[4:-1])
    assert refs[-1].status == "dropped"
    assert "node budget" in (refs[-1].reason or "")


if __name__ == "__main__":
    print(json.dumps(browser_responses()))
