"""Controlled HTTP reproduction; exit 0 means known requirements survived.

Uses isolated test storage and deletes its temporary pytest module.
Run with backend/.venv/Scripts/python.exe from the repository root.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

TEST_SOURCE = """from tests.test_v2_t05_requirements import (
    Script, _act, _create_project, _environment, _messages,
    _needs_input, _ready_data, _start, _view,
)

async def test_sparse_partial_preserves_known_requirements(full_settings):
    def partial(request, *, sparse=False):
        data = _ready_data(
            request, product_description=None,
            purpose=None if sparse else "电商台面主图",
            style_keywords=[] if sparse else ["极简", "冷白背景"],
            composition=None if sparse else "台面主图",
            facts=[], must_include=[], assumptions=[],
        )
        return _needs_input(
            request, question="请说明这组新品的具体商品。",
            path="product_description", detail="商品待补充", data=data,
        )
    script = Script([lambda r: partial(r), lambda r: partial(r, sparse=True)])
    async with _environment(full_settings, script) as (_, client, stub, visual):
        project = await _create_project(client)
        started = await _start(
            client, project["id"], "为这组新品生成一张极简台面主图，冷白背景",
        )
        response = await _act(
            client, project["id"], "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        first = (await _messages(client, project["id"]))[-1]["content"]
        assert "使用场景与目标：电商台面主图" in first, first
        assert "风格、构图与光影：极简、冷白背景" in first, first
        view = await _view(client, project["id"])
        response = await _act(
            client, project["id"], "answer_requirements_clarification",
            text="1A 2B 3C", expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        calls = [
            r.payload["input_data"] for r in stub.role_calls_received
            if r.payload["task"]["role"] == "requirements_consultant"
        ]
        assert len(calls) == 2
        assert calls[-1]["current_requirement"]["purpose"] == "电商台面主图"
        assert calls[-1]["current_requirement"]["style_keywords"] == ["极简", "冷白背景"]
        assert visual.submit_calls == []
        second = (await _messages(client, project["id"]))[-1]["content"]
        assert (
            "使用场景与目标：电商台面主图" in second
            and "风格、构图与光影：极简、冷白背景" in second
        ), second
"""


def main() -> int:
    backend = Path(__file__).resolve().parents[2] / "backend"
    temporary_test: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".py",
            prefix="_role_chain_probe_", dir=backend / "tests", delete=False,
        ) as handle:
            handle.write(TEST_SOURCE)
            temporary_test = Path(handle.name)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(temporary_test),
             "--tb=short", "--show-capture=no"],
            cwd=backend, check=False,
        )
        return result.returncode
    finally:
        if temporary_test is not None:
            temporary_test.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
