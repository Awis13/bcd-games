#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional permission: Madeira Converter Exception, version 1 (LICENSE-EXCEPTION.md).
"""Publish metadata from BCD's latest stable GitHub release. Requires macOS."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


UPSTREAM = "thebcd69/bcd-games"
SOURCE_URL = "https://thebcd69.github.io/bcd-games/"
API_URL = f"https://api.github.com/repos/{UPSTREAM}/releases/latest"


def fetch_json(url):
    headers = {"User-Agent": "bcd-altsource", "Accept": "application/json"}
    if url == API_URL and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        return json.load(response)


def release_state(release):
    if release["draft"] or release["prerelease"]:
        raise ValueError("Expected a published stable release")
    assets = [a for a in release["assets"] if a["name"] == "BCD.ipa"]
    if len(assets) != 1:
        raise ValueError("The latest stable release must contain exactly one BCD.ipa")
    asset = assets[0]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or ""):
        raise ValueError("The release asset has no SHA-256 digest")
    expected_url = f"https://github.com/{UPSTREAM}/releases/download/"
    if not asset["browser_download_url"].startswith(expected_url):
        raise ValueError("The IPA download must belong to the upstream repository")
    return {
        "generator": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "release": {k: release[k] for k in ("tag_name", "name", "published_at", "body")},
        "asset": {k: asset[k] for k in (
            "id", "name", "size", "digest", "browser_download_url", "updated_at"
        )},
    }


def verify_ipa(path, asset):
    if path.stat().st_size != asset["size"]:
        raise ValueError("IPA size does not match the release asset")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if f"sha256:{digest.hexdigest()}" != asset["digest"]:
        raise ValueError("IPA checksum does not match the release asset")


def inspect_ipa(path):
    entitlements = set()
    privacy = {}
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as temp:
        names = archive.namelist()
        main_plists = [n for n in names if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
        if len(main_plists) != 1:
            raise ValueError("Expected exactly one main app in the IPA")
        main = plistlib.loads(archive.read(main_plists[0]))
        for name in names:
            if not name.startswith("Payload/") or not name.endswith((".app/Info.plist", ".appex/Info.plist")):
                continue
            info = plistlib.loads(archive.read(name))
            executable = info["CFBundleExecutable"]
            if not executable or PurePosixPath(executable).name != executable:
                raise ValueError("Invalid bundle executable name")
            binary = Path(temp) / "executable"
            binary.write_bytes(archive.read(str(PurePosixPath(name).parent / executable)))
            result = subprocess.run(
                ["codesign", "--display", "--entitlements", "-", "--xml", str(binary)],
                capture_output=True, check=True,
            )
            entitlements.update(plistlib.loads(result.stdout) if result.stdout.strip() else {})
            privacy.update({k: v for k, v in info.items() if "UsageDescription" in k})
        for key in ("CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion", "MinimumOSVersion"):
            if not isinstance(main.get(key), str) or not main[key]:
                raise ValueError(f"Missing app metadata: {key}")
    return main, {"entitlements": sorted(entitlements), "privacy": privacy}


def make_source(state, info, permissions):
    release, asset = state["release"], state["asset"]
    icon_url = (
        f"https://raw.githubusercontent.com/{UPSTREAM}/{quote(release['tag_name'], safe='')}/"
        "app/Madeira/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png"
    )
    version = {
        "version": info["CFBundleShortVersionString"],
        "buildVersion": info["CFBundleVersion"],
        "date": release["published_at"],
        "localizedDescription": release["body"] or release["name"] or release["tag_name"],
        "downloadURL": asset["browser_download_url"],
        "size": asset["size"],
        "minOSVersion": info["MinimumOSVersion"],
    }
    return {
        "name": "BCD",
        "identifier": "io.github.thebcd69.bcd-games",
        "sourceURL": SOURCE_URL + "source.json",
        "subtitle": "The latest stable BCD release from GitHub",
        "description": (
            "BCD is developed by thebcd69. "
            "IPA downloads come directly from thebcd69/bcd-games on GitHub. "
            "This source updates on release events and checks for stable releases hourly."
        ),
        "website": f"https://github.com/{UPSTREAM}",
        "iconURL": icon_url,
        "apps": [{
            "name": "BCD",
            "bundleIdentifier": info["CFBundleIdentifier"],
            "developerName": "thebcd69",
            "subtitle": "Windows games on iPhone and iPad",
            "localizedDescription": (
                "BCD runs Windows games on iPhone and iPad. "
                "It requires iOS 18 or later and JIT. Installation and game compatibility "
                f"information: https://github.com/{UPSTREAM}"
            ),
            "iconURL": icon_url,
            "category": "games",
            "versions": [version],
            "appPermissions": permissions,
        }],
        "news": [],
    }


def set_changed(changed):
    print(f"Source changed: {str(changed).lower()}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"changed={str(changed).lower()}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipa", type=Path, help="Inspect an already downloaded official IPA")
    parser.add_argument("--force", action="store_true", help="Generate even if the published source is current")
    args = parser.parse_args()
    state = release_state(fetch_json(API_URL))
    if not args.force:
        try:
            if fetch_json(SOURCE_URL + "state.json") == state:
                set_changed(False)
                return
        except HTTPError as error:
            if error.code != 404:
                raise
    with tempfile.TemporaryDirectory() as temp:
        ipa = args.ipa or Path(temp) / "BCD.ipa"
        if not args.ipa:
            with urlopen(state["asset"]["browser_download_url"], timeout=120) as response, ipa.open("wb") as output:
                shutil.copyfileobj(response, output)
        verify_ipa(ipa, state["asset"])
        info, permissions = inspect_ipa(ipa)
        source = make_source(state, info, permissions)
    site = Path(__file__).resolve().parents[1] / "build-out" / "altsource"
    site.mkdir(parents=True, exist_ok=True)
    for filename, value in (("source.json", source), ("state.json", state)):
        (site / filename).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    (site / ".nojekyll").touch()
    (site / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>BCD AltStore Source</title><main><h1>BCD AltStore Source</h1>'
        '<p>The latest stable BCD release by thebcd69.</p>'
        '<p>Add this URL in AltStore Classic or SideStore:</p>'
        f'<p><a href="source.json">{SOURCE_URL}source.json</a></p>'
        f'<p><a href="altstore://source?url={quote(SOURCE_URL + "source.json", safe="")}">Add to AltStore</a></p>'
        f'<p><a href="sidestore://source?url={quote(SOURCE_URL + "source.json", safe="")}">Add to SideStore</a></p>'
        f'<p><a href="https://github.com/{UPSTREAM}/releases">Official BCD releases</a></p>'
        '<p>Checks for stable releases hourly. IPA files download directly from GitHub.</p></main>\n'
    )
    print(f"Generated BCD {info['CFBundleShortVersionString']} ({info['CFBundleVersion']})")
    set_changed(True)


if __name__ == "__main__":
    main()
