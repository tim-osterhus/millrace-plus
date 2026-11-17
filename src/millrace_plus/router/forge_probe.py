"""Millrace Forge runner probe, format ``millrace-forge-runner-probe/v1``.

Millrace Plus ships this file as package data and never imports it. Core writes
a copy as ``forge-probe.py`` under the invocation root. The parent adapter runs
it with ``EXECUTABLE -I -B -X utf8 forge-probe.py`` before the worker.

The probe uses only the standard library and never imports ``forge``. It writes
one canonical ASCII JSON line to stdout and nothing to stderr:

    {"format", "python", "implementation", "forge_guardrails", "files_match"}

``files_match`` is true only when each of the 53 files that the pinned
``forge-guardrails`` 0.10.1 wheel places under ``forge/`` is a regular file with
the pinned SHA-256. It does not cover other installed files, bytecode caches,
dependencies or startup hooks. The source must parse under Python 3.11 grammar.
"""

import hashlib
import json
import os
import re
import stat
import sys
from importlib import metadata

FORMAT = "millrace-forge-runner-probe/v1"
DISTRIBUTION = "forge-guardrails"
FILE_LIMIT = 4194304
VERSION_GRAMMAR = re.compile(r"[A-Za-z0-9.+!_-]{1,64}")
IMPLEMENTATION_GRAMMAR = re.compile(r"[a-z0-9_]{1,32}")
PINNED = (
    (
        "forge/__init__.py",
        "16ae92c7a0006f39b600f0d67c91cb4672a85760785d76c7758653af195c8689",
    ),
    (
        "forge/_backend_profiles.py",
        "6acc218e5f66a5bf80ef341c2b15da6bf1a286549249ee7d25021148f064ff36",
    ),
    (
        "forge/_endpoint_layouts.py",
        "b445732bb9f8e562557a09e944e60c8ee8a9ed7d0d890907e8e48bf20a774f5b",
    ),
    (
        "forge/_resolved_backend.py",
        "95f1cd87158520612fe9dd75470d10cf8a3abca6c62af07ead6cee97b65d27a4",
    ),
    (
        "forge/clients/__init__.py",
        "05d43f55a0be5635457bcb8803a98d17af0b0f72c64a83d1c40f604127821377",
    ),
    (
        "forge/clients/anthropic.py",
        "0d9c0da8e6bc5bd0494609c7163e519103d854deec0f991b090577f9a197bcad",
    ),
    (
        "forge/clients/base.py",
        "1e802f923aa3b1978a3c9e7f512c4b7f3baa11012219a5eb971f97c72527e335",
    ),
    (
        "forge/clients/llamafile.py",
        "a1f04a3ec9145ac79738d1e45c296851ac8146d009f3871a874d356ea749e729",
    ),
    (
        "forge/clients/ollama.py",
        "63102ca6cec370f4d00c6e28a39281ae0b735995854b8364f7b8f5da46052e23",
    ),
    (
        "forge/clients/openai_compat.py",
        "f294c2a88c3a7113e2b607ac68a6c1e7aeda1dd2128c02485cbc1e2f4f62585e",
    ),
    (
        "forge/clients/sampling_defaults.py",
        "1a062dfa620556a1d655980edfbd10072ff5ae57e616024046763c0c213281ba",
    ),
    (
        "forge/clients/vllm.py",
        "664e6ccf6a6071af9a2c61d1243be54b0bf62bb782857312cee13d016e7d2745",
    ),
    (
        "forge/context/__init__.py",
        "51a9e69ac54c49122fe7fea6ecfbc94cad0645fbe666f92d56f4a95085fc2497",
    ),
    (
        "forge/context/hardware.py",
        "4c8131b7ecba5bc3df7e3860c89f80b2b03365cdc414be25faaa7850bed76900",
    ),
    (
        "forge/context/manager.py",
        "2e5cbdb290558ac62968160146bdc6325d3881772e8db50f0e958b5cf8e237c4",
    ),
    (
        "forge/context/observations.py",
        "2699f6512addde4e3449102171195695078ff9cc3992faca40edbd2546a5c7cc",
    ),
    (
        "forge/context/strategies.py",
        "00a989debf80f3024104a2125f078f68658a9c7d02c460f0b58847cd5c15fad8",
    ),
    (
        "forge/core/__init__.py",
        "8c80240c5c382818e733f29a7169529ce6b4901fc8073507514fc4f054bff2a0",
    ),
    (
        "forge/core/inference.py",
        "4b7d3dfd740188a2352fb6f97227ad127bd8e790411f789acafbfd9eb17bcb4c",
    ),
    (
        "forge/core/messages.py",
        "41ce4e4cd9d1776ed13491158ca64bb6e6dcb633faf686a8ed6e002602ae5a3e",
    ),
    (
        "forge/core/reasoning.py",
        "cfaebe9ce5b284958470fb2f24d3daaf96d35477f6cfc3ff86ba2772b7edc66b",
    ),
    (
        "forge/core/runner.py",
        "0cbe2fc428171720df683d49d2fa39af5e97d84ec1d9b86c10be7e1c66821c16",
    ),
    (
        "forge/core/slot_worker.py",
        "551a4afaa3152c7ac6dfa1b706c30936eece58fba4bb6bdf430f80a381f0a502",
    ),
    (
        "forge/core/steps.py",
        "aeeef79fa1d78770203346c163786715fc5f08b7e223794ce70031d0656c9d4c",
    ),
    (
        "forge/core/workflow.py",
        "e5baad6aac152a2625a9bdccf164d32e476ce5a02b9e47be2e4640f6c1ad4862",
    ),
    (
        "forge/errors.py",
        "ca4e09ee0c9b0c860a4545eb9faef9a7043c1f81742b345837162d2d62c4d66c",
    ),
    (
        "forge/guardrails/__init__.py",
        "92836e7a3b7ea63e3caa79db4a263633be0f9d7a56544cca2244d861b0882232",
    ),
    (
        "forge/guardrails/error_tracker.py",
        "989658eb8fe14c71eaa131ff5ad1767b35cdc1ce0d0315924e7665165f6cfe8a",
    ),
    (
        "forge/guardrails/guardrails.py",
        "4ed4beae5988abe9b9c146e898bfb6cecc9c5133a3075747f91f20da23407823",
    ),
    (
        "forge/guardrails/nudge.py",
        "35a1ff122f9c8b1d12c51051f7453af4abfb0acc605e46b3bfbbf3fc62863c1c",
    ),
    (
        "forge/guardrails/response_validator.py",
        "215d6310e7f1b5194ea39b54d5ea6ac25d137b8169ddc600673e60d8e6de1363",
    ),
    (
        "forge/guardrails/step_enforcer.py",
        "5a0b50f0f90ba20708fcae6ff13908175d2cb15540e415e13f0dc16b583883c3",
    ),
    (
        "forge/prompts/__init__.py",
        "39ea4f179746a5490131312a045d11e1e6187c18b532d5a4736bf474dffbe1f6",
    ),
    (
        "forge/prompts/nudges.py",
        "ccd6a53de5bf3b5577dc19305a15a5041881c04cca364dc7bca4153e64414cef",
    ),
    (
        "forge/prompts/templates.py",
        "698a923b35d8eb5798f8bb5d4e17e0cd046f3feb41923978e00384fc665b12df",
    ),
    (
        "forge/prompts/think_tags.py",
        "1f120e6e7a547ebde41bd2ca6f860ad7d3dd8f5fcd143279be1f7572a389d16e",
    ),
    (
        "forge/proxy/__init__.py",
        "518a67debfaa876a44e1557daeff6a42a6258ee0a39249e5e21a3cfd25af991e",
    ),
    (
        "forge/proxy/__main__.py",
        "4a8b9cfcfaa6e9d24e319af0b5fb1540d98632ea9203bd2cdb6ae472314a367b",
    ),
    (
        "forge/proxy/_config.py",
        "3df380acfbc26739c765c390d330873bb8c5a223d392a3116bfd17ccfff441bd",
    ),
    (
        "forge/proxy/_installer.py",
        "2ac10f16fe89354b225dbe07f2ecc67b2bc3430667f2ac5643b173d3da25696f",
    ),
    (
        "forge/proxy/_options.py",
        "6ad46b4dc794ec45c61d1c868ccdb6cfb74b26620c357a892eaf5898e244be4d",
    ),
    (
        "forge/proxy/_profiles.py",
        "9e3fb44cb4d0358f8129b29e031661254cda943611b34f79ac96e518c9ef1745",
    ),
    (
        "forge/proxy/auth.py",
        "819417475d8da69f0201e029093385c5b7ad6fd21780ea753d580662cd0335c1",
    ),
    (
        "forge/proxy/convert.py",
        "c549b36cae92dfd98015532b09005388f8dc20f09065f83bce4be9a2ca55c51a",
    ),
    (
        "forge/proxy/convert_anthropic.py",
        "87472b8741125cf105b9b6099c4b6ceb0499b789599b14cb4b29d6854f4e2ed1",
    ),
    (
        "forge/proxy/handler.py",
        "9f23cc80c13696123c1009577a5ee8ebd2576d952622525a6cffb8b720df7079",
    ),
    (
        "forge/proxy/proxy.py",
        "8db238b2c343fbac0c1ee44d671b75b3a7441eefe69dde375b508b5716c057f5",
    ),
    (
        "forge/proxy/server.py",
        "b1e7b90fe0a04726eededf55d055641f6484d89ec77c9c13e30104c046bbd597",
    ),
    (
        "forge/py.typed",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ),
    (
        "forge/rpc.py",
        "624293a1cfe1d54090bbdd223a1146e777735a31109fe0fb3a12d07a03aecb52",
    ),
    (
        "forge/server.py",
        "34ccbb93d765361271229f877c22ba6f9970e5dba21a8715f2f64afe4a9fe84f",
    ),
    (
        "forge/tools/__init__.py",
        "774f4087ee5a49b3030da9fe4b90e07fd7770d40400159085d0dd6215df94575",
    ),
    (
        "forge/tools/respond.py",
        "60f1ede92eaf5437965df99354d820363b0ea94f3d550de7c2a221fdbb0aea93",
    ),
)


def _version():
    try:
        value = metadata.version(DISTRIBUTION)
    except Exception:
        return None
    if isinstance(value, str) and VERSION_GRAMMAR.fullmatch(value):
        return value
    return "unrecognized"


def _file_digest(path):
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > FILE_LIMIT:
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return None
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            size += len(chunk)
            if size > FILE_LIMIT:
                return None
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def _files_match():
    try:
        distribution = metadata.distribution(DISTRIBUTION)
        for name, expected in PINNED:
            if _file_digest(str(distribution.locate_file(name))) != expected:
                return False
        return True
    except Exception:
        return False


def main():
    name = sys.implementation.name
    major, minor, micro = sys.version_info[:3]
    report = {
        "format": FORMAT,
        "python": f"{major}.{minor}.{micro}",
        "implementation": (
            name
            if isinstance(name, str) and IMPLEMENTATION_GRAMMAR.fullmatch(name)
            else "unrecognized"
        ),
        "forge_guardrails": _version(),
        "files_match": False,
    }
    if report["forge_guardrails"] is not None:
        report["files_match"] = _files_match()
    line = json.dumps(report, separators=(",", ":"), ensure_ascii=True) + "\n"
    view = memoryview(line.encode("ascii"))
    while view:
        view = view[os.write(1, view) :]
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        code = 1
    sys.exit(code)
