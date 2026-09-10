"""GitHub deploy pipeline for the generated static web app (Layer 3 helper).

Creates a public GitHub repo, pushes the static web-app files (Base64 via the
contents API), and — when ``deploy_mode`` is "live" — enables GitHub Pages for a
production URL. Used two ways:

  * the MCP tool ``webapp_github_pipeline`` (agent-driven), and
  * a stdin runner — ``python -m arcgis_pro_salah_mcp.webapp.github`` — that the
    SalahAIBridge "Deploy Web App" ribbon button shells out to (token on stdin).

``requests`` is imported lazily (it ships with arcgispro-py3), so importing this
module never fails and a missing dependency comes back as a clean error envelope.
"""
from __future__ import annotations

import base64
from pathlib import Path

from .._result import err, guard, ok
from ..config import CONFIG

GITHUB_API = "https://api.github.com"
_API_VERSION = "2022-11-28"


def _requests():
    import requests  # ships with arcgispro-py3
    return requests


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
        "User-Agent": "ArcGIS-Pro-Salah-MCP",
    }


def _collect(src: Path) -> list[tuple[str, Path]]:
    """(repo-relative POSIX path, absolute path) for every file under ``src``."""
    return [
        (p.relative_to(src).as_posix(), p)
        for p in src.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]


@guard
def github_pipeline(repo_name: str, token: str, deploy_mode: str = "upload",
                    app_dir: str | None = None) -> dict:
    """Create a public repo, push the static web app, optionally enable Pages.

    ``deploy_mode="live"`` (or "deploy"/"pages") also turns on GitHub Pages on
    ``main`` and returns the production URL.
    """
    if not repo_name or not token:
        return err("repo_name and token are required.")

    requests = _requests()
    src = Path(app_dir or CONFIG.webapp_output_dir).resolve()
    if not src.is_dir():
        return err(f"web app folder not found: {src} (generate it first with webapp_create).")
    files = _collect(src)
    if not files:
        return err(f"no files to deploy in {src}.")

    h = _headers(token)

    # 1. authenticate / resolve the owner
    who = requests.get(f"{GITHUB_API}/user", headers=h, timeout=30)
    if who.status_code != 200:
        return err(f"GitHub auth failed (HTTP {who.status_code}): {who.text[:200]}")
    owner = who.json().get("login")

    # 2. create the repo (auto_init gives a 'main' branch we can write to)
    created = requests.post(
        f"{GITHUB_API}/user/repos", headers=h, timeout=30,
        json={"name": repo_name, "private": False, "auto_init": True,
              "description": "Static ArcGIS Maps SDK for JS app — published by Salah MCP."},
    )
    if created.status_code not in (201, 422):   # 422 = already exists → reuse
        hint = ""
        if created.status_code == 403:
            hint = (" — this token can't create repositories. Use a classic token with "
                    "the 'repo' scope, OR a fine-grained token with Repository access set "
                    "to 'All repositories' and Administration + Contents + Pages = Read and "
                    "write. (Or create the repo on GitHub first, then a fine-grained token "
                    "scoped to just that repo with Contents + Pages write is enough.)")
        return err(f"create repo failed (HTTP {created.status_code}): {created.text[:200]}{hint}")

    # 3. push each file via the contents API (update in place if it already exists)
    pushed = 0
    push_errors = []
    for rel, abspath in files:
        url = f"{GITHUB_API}/repos/{owner}/{repo_name}/contents/{rel}"
        body = {
            "message": f"Add {rel} (Salah MCP)",
            "content": base64.b64encode(abspath.read_bytes()).decode("ascii"),
            "branch": "main",
        }
        existing = requests.get(url, headers=h, params={"ref": "main"}, timeout=30)
        if existing.status_code == 200:
            body["sha"] = existing.json().get("sha")
        put = requests.put(url, headers=h, json=body, timeout=60)
        if put.status_code in (200, 201):
            pushed += 1
        else:
            push_errors.append(f"{rel}: HTTP {put.status_code} {put.text[:120]}")

    # Don't report a hollow success: if nothing landed, the repo was created but the
    # files couldn't be written — surface why (almost always a missing permission).
    if pushed == 0 and push_errors:
        hint = ""
        if any("HTTP 403" in e for e in push_errors):
            hint = (" — the token can create the repo but can't write files. Add "
                    "'Contents: Read and write' to the fine-grained token, or use a "
                    "classic token with the 'repo' scope.")
        return err(f"pushed 0/{len(files)} files. First error: {push_errors[0]}{hint}",
                   repository_url=f"https://github.com/{owner}/{repo_name}")

    # 4. optionally enable GitHub Pages
    production_url = None
    if str(deploy_mode).lower() in ("live", "deploy", "pages", "website"):
        pg = requests.post(
            f"{GITHUB_API}/repos/{owner}/{repo_name}/pages", headers=h, timeout=30,
            json={"source": {"branch": "main", "path": "/"}},
        )
        if pg.status_code in (201, 409):   # created / already enabled
            production_url = f"https://{owner}.github.io/{repo_name}/"

    payload = {
        "repository_url": f"https://github.com/{owner}/{repo_name}",
        "production_url": production_url,
        "files_pushed": pushed,
        "deploy_mode": deploy_mode,
    }
    if push_errors:   # some files landed, some didn't — report the stragglers
        payload["push_errors"] = push_errors
    return ok(payload)


def _main() -> None:
    """stdin JSON runner for the C# 'Deploy Web App' button.

    Reads ``{"repo_name","token","deploy_mode","app_dir"}`` from stdin and prints
    one ``SALAH_RESULT:<envelope-json>`` line (token never appears in argv).
    """
    import json
    import sys

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:  # noqa: BLE001
        print("SALAH_RESULT:" + json.dumps(err(f"bad input: {exc}")), flush=True)
        return

    result = github_pipeline(
        payload.get("repo_name", ""),
        payload.get("token", ""),
        payload.get("deploy_mode", "upload"),
        payload.get("app_dir") or None,
    )
    print("SALAH_RESULT:" + json.dumps(result), flush=True)


if __name__ == "__main__":
    _main()
