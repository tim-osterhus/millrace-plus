"""Exact installed artifacts: Core-free catalog and inert stock execution."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

from test_distribution import PLUS_ROOT, Env, _record, _sha256
from test_distribution import (
    artifacts as artifacts,  # noqa: F401,F811 - shared pytest fixture
)


def test_v023_templates_installed_and_initializable(
    artifacts: dict[str, Path],  # noqa: F811 - pytest fixture injection
) -> None:
    root = artifacts["root"]
    wheel = artifacts["plus_wheel"]
    assert _record(wheel) == _record(artifacts["plus_sdist_wheel"])
    standalone = Env(root / "catalog-only", [wheel])
    console = standalone.root / "bin/millrace-plus"
    listed = standalone.run(str(console), "catalog", "list", check=True)
    entries = json.loads(listed.stdout)["templates"]
    assert [v["selector"] for v in entries] == [
        "bundled:document-review",
        "bundled:lad",
        "bundled:software-change",
    ]
    standalone.py(
        "import importlib.util; assert importlib.util.find_spec('millrace') is None"
    )
    # The integration job supplies its admitted pinned Node executable. Public
    # Python-only CI still verifies every ESM byte through the artifact inventory.
    node = os.environ.get("MILLRACE_TEST_PAPERCLIP_NODE")
    if node:
        package = standalone.py(
            "from pathlib import Path; "
            "import millrace_plus.integrations.paperclip_bridge as bridge; "
            "print(Path(bridge.__file__).parent / 'paperclip/index.js')"
        ).stdout.strip()
        loaded = standalone.run(
            node, "--input-type=module", "--eval",
            "const mod = await import(process.argv[1]); "
            "const adapter = mod.createServerAdapter(); "
            "if (adapter.type !== 'millrace_graph' || "
            "typeof adapter.execute !== 'function' || "
            "typeof adapter.testEnvironment !== 'function') process.exit(1); "
            "console.log(adapter.type);",
            package, check=True,
        )
        assert loaded.stdout.strip() == "millrace_graph"
    for name in ("document-review", "lad", "software-change"):
        copy = standalone.cwd / name
        standalone.run(
            str(console), "catalog", "init", f"bundled:{name}", str(copy), check=True
        )
        assert (copy / "workflow.json").is_file()
        assert not list(copy.rglob("__pycache__"))
    source = standalone.cwd / "software-change"
    copied = standalone.cwd / "software-change-copy"
    graph = json.loads((source / "workflow.json").read_text())
    prompt = graph["planes"]["main"]["nodes"]["work"]["invoke"]["config"]["prompt"]
    source_prompt = source / prompt
    copied_prompt = copied / prompt

    def files(folder: Path) -> dict[str, str]:
        return {
            path.relative_to(folder).as_posix(): _sha256(path)
            for path in sorted(folder.rglob("*"))
            if path.is_file()
        }

    source_files = files(source)
    standalone.run(
        str(console), "catalog", "copy", str(source), str(copied), check=True
    )
    assert files(copied) == source_files
    assert all(
        not (source / relative).samefile(copied / relative) for relative in source_files
    )
    assert all(
        stat.S_IMODE((source / relative).stat().st_mode)
        == stat.S_IMODE((copied / relative).stat().st_mode)
        for relative in source_files
    )
    original_prompt = source_prompt.read_bytes()
    copied_prompt.write_bytes(original_prompt + b"\nIndependent copy.\n")
    assert source_prompt.read_bytes() == original_prompt
    copied_files = files(copied)
    standalone.py(
        "from pathlib import Path; import shutil, sys; "
        "from millrace_plus import catalog; "
        "source = catalog.bundled_root() / 'software-change'; "
        "assert source.is_dir(); "
        "assert source.resolve().is_relative_to(Path(sys.prefix).resolve()); "
        "shutil.rmtree(source); assert not source.exists()"
    )
    after_removal = standalone.cwd / "software-change-after-removal"
    standalone.run(
        str(console), "catalog", "copy", str(copied), str(after_removal), check=True
    )
    assert files(after_removal) == copied_files
    assert all(
        stat.S_IMODE((copied / relative).stat().st_mode)
        == stat.S_IMODE((after_removal / relative).stat().st_mode)
        for relative in copied_files
    )
    standalone.py(
        "import importlib.util; assert importlib.util.find_spec('millrace') is None"
    )
    (standalone.cwd / "catalog-copy-proof.json").write_text(
        json.dumps(
            {
                "core_absent": True,
                "source_files": source_files,
                "independent_copy_files": copied_files,
                "post_bundled_source_removal_files": files(after_removal),
                "bundled_software_change_removed": True,
            },
            indent=2,
        )
        + "\n"
    )
    lad = standalone.cwd / "lad"
    lad_copy = standalone.cwd / "lad-copy"
    standalone.run(str(console), "catalog", "copy", str(lad), str(lad_copy), check=True)
    lad_files = files(lad)
    assert len(lad_files) == 37
    assert files(lad_copy) == lad_files
    assert all(not (lad / rel).samefile(lad_copy / rel) for rel in lad_files)
    prompt = lad_copy / "prompts/planner.md"
    prompt.write_bytes(prompt.read_bytes() + b"\nLocal LAD guidance.\n")
    assert files(lad) == lad_files
    standalone.py(
        "from pathlib import Path; import shutil, sys; "
        "from millrace_plus import catalog; "
        "source = catalog.bundled_root() / 'lad'; "
        "assert source.resolve().is_relative_to(Path(sys.prefix).resolve()); "
        "shutil.rmtree(source)"
    )
    lad_after_removal = standalone.cwd / "lad-after-removal"
    standalone.run(
        str(console), "catalog", "copy", str(lad_copy), str(lad_after_removal),
        check=True,
    )
    assert files(lad_after_removal) == files(lad_copy)
    core = Path(os.environ.get("MILLRACE_TEST_CORE_WHEEL", artifacts["core_wheel"]))
    assert _sha256(core) == _sha256(artifacts["core_wheel"])
    env = Env(root / "stock-installed", [core, artifacts["plus_sdist_wheel"]])
    for executable in ("claude", "pi", "codex"):
        assert not (env.root / "bin" / executable).exists()
    support = Path(__file__).with_name("catalog_support.py")
    shutil.copyfile(support, env.cwd / "catalog_support.py")
    (env.cwd / "fixtures").mkdir()
    shutil.copyfile(
        support.parent / "fixtures/workflow_peer.py",
        env.cwd / "fixtures/workflow_peer.py",
    )
    # -I runs a task-owned proof driver; imports under test come only from wheels.
    driver = env.cwd / "driver.py"
    driver.write_text(
        (env.cwd / "catalog_support.py").read_text()
        + """
import importlib.metadata as metadata
for name in ('document-review', 'software-change'):
    workflow, project, home, fake = stock(Path.cwd() / name, name)
    instance = create(workflow, project, home, fake)
    assert_fake(instance, home, fake)
    result = run(instance, home, fake)
    assert result['status'] == 'succeeded', result
    assert Path(control.__file__).is_relative_to(Path(sys.prefix))
    # Removing the catalog source cannot affect this copied workflow.
    source = catalog.bundled_root() / name
    import shutil
    assert source.resolve().is_relative_to(Path(sys.prefix).resolve())
    shutil.rmtree(source)
    assert_fake(instance, home, fake)
    again = run(instance, home, fake)
    assert again['status'] == 'succeeded', again
origins = {}
for name in ('millrace-ai', 'millrace-plus'):
    direct = metadata.distribution(name).read_text('direct_url.json')
    origins[name] = json.loads(direct)['url']
print(json.dumps(origins))
"""
    )
    proof = env.run(str(env.python), "-I", str(driver), check=True)
    assert json.loads(proof.stdout) == {
        "millrace-ai": core.as_uri(),
        "millrace-plus": artifacts["plus_sdist_wheel"].as_uri(),
    }
    # Installed CLI stock create uses explicit fake aliases and restricted PATH.
    # Source-removal proof used its own environment; CLI gets a fresh install.
    cli_env = Env(root / "stock-cli", [core, wheel])
    cli_env.run(
        str(cli_env.root / "bin/millrace-plus"),
        "catalog",
        "init",
        "bundled:document-review",
        str(cli_env.cwd / "workflow"),
        check=True,
    )
    (cli_env.cwd / "project").mkdir()
    (cli_env.cwd / "project/request.md").write_text(
        "Original goal: accepted content.\n"
    )
    (cli_env.cwd / "project/case.json").write_text('{"product":"document.md"}')
    graph = json.loads((cli_env.cwd / "workflow/workflow.json").read_text())
    for node, spec in graph["planes"]["main"]["nodes"].items():
        prompt = spec["invoke"]["config"].get("prompt")
        if prompt:
            path = cli_env.cwd / "workflow" / prompt
            path.write_text(f"TEST_NODE:{node}\n" + path.read_text())
    fake = cli_env.cwd / "fake-claude"
    fake.write_text(
        f"#!{cli_env.python}\n"
        + (support.parent / "fixtures/workflow_peer.py").read_text()
    )
    fake.chmod(0o700)
    created = cli_env.run(
        str(cli_env.root / "bin/millrace-plus"),
        "router",
        "instance",
        "create",
        "--workflow",
        str(cli_env.cwd / "workflow/workflow.json"),
        "--project",
        str(cli_env.cwd / "project"),
        "--state-home",
        str(cli_env.cwd / "state"),
        "--host-paths",
        json.dumps({"claude": str(fake), "python3": str(cli_env.python)}),
        check=True,
    )
    instance = json.loads(created.stdout.splitlines()[-1])["instance_id"]
    # Verify the captured fake before CLI start.
    checked = cli_env.py(
        'import sqlite3,json\nfrom pathlib import Path\np=Path.cwd()/"state/instances"/'
        + repr(instance)
        + '/"state.sqlite3"\n'
        + 'with sqlite3.connect(f"{p.as_uri()}?mode=ro",uri=True) as c:\n'
        + ' rows=c.execute("SELECT body FROM revisions").fetchall()\n'
        + "assert rows\nselected = []\nfor row in rows:\n"
        + " revision = json.loads(row[0])\n"
        + " graph = json.loads(__import__('base64').b64decode(revision['files']['workflow.json']))\n"  # noqa: E501 - embedded isolated proof
        + " for name, binding in graph['bindings'].items():\n"
        + "  if binding['adapter'] == 'claude_print': selected.append(revision['executables'][name])\n"  # noqa: E501 - embedded isolated proof
        + "assert selected\nassert all(Path(value).resolve() == Path("
        + repr(str(fake))
        + ").resolve() for value in selected)"
    )
    assert checked.returncode == 0
    cli_env.run(
        str(cli_env.root / "bin/millrace-plus"),
        "router",
        "start",
        "--instance",
        instance,
        "--state-home",
        str(cli_env.cwd / "state"),
        check=True,
    )
    assert (cli_env.cwd / "project/document.md").read_text() == "accepted content\n"


def test_every_stock_workflow_asset_is_in_wheel_and_sdist(
    artifacts: dict[str, Path],  # noqa: F811 - pytest fixture injection
) -> None:
    # The complete package inventory includes Python adapters and non-Python
    # workflow, skill and local ESM assets. Compare bytes, not just filenames.
    source_root = PLUS_ROOT / "src/millrace_plus"
    assets = [
        path
        for path in source_root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.name != ".DS_Store"
        and "__pycache__" not in path.relative_to(source_root).parts
    ]

    with zipfile.ZipFile(artifacts["plus_wheel"]) as wheel:
        names = set(wheel.namelist())
        for source in assets:
            relative = source.relative_to(source_root).as_posix()
            packaged = f"millrace_plus/{relative}"
            assert packaged in names, packaged
            assert wheel.read(packaged) == source.read_bytes(), packaged

    with tarfile.open(artifacts["plus_sdist"]) as sdist:
        names = sdist.getnames()
        for source in assets:
            relative = source.relative_to(source_root).as_posix()
            suffix = f"/src/millrace_plus/{relative}"
            matches = [name for name in names if name.endswith(suffix)]
            assert len(matches) == 1, (suffix, matches)
            member = sdist.extractfile(matches[0])
            assert member is not None
            assert member.read() == source.read_bytes(), relative
