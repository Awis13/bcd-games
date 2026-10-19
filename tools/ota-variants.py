#!/usr/bin/env python3
"""Package app identities and render a private OTA publication for one build."""
import argparse
from datetime import datetime, timedelta
from email.message import EmailMessage
import html
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import smtplib
import ssl
import subprocess
from urllib.parse import quote
from zoneinfo import ZoneInfo


# The app is "BCD" (owner, 2026-10-18), with its own bundle IDs. The internal names stay
# Madeira and Madeira2: they name the build outputs, the bucket objects and the pages.
VARIANTS = (("Madeira", "com.thebcd69.bcd"),
            ("Madeira2", "com.thebcd69.bcd2"))
DISPLAY = {"Madeira": "BCD", "Madeira2": "BCD 2"}


def stage_variants(source, output):
    source, output = Path(source), Path(output)
    with (source / "Info.plist").open("rb") as f:
        original = plistlib.load(f)
    original_id = original["CFBundleIdentifier"]
    if original_id != VARIANTS[0][1]:
        raise ValueError("The archived app must carry the primary bundle identifier")
    staged = []
    for name, bundle_id in VARIANTS:
        root = output / "ipa-variants" / name
        if root.exists():
            shutil.rmtree(root)
        app = root / "Payload" / (name + ".app")
        shutil.copytree(source, app, symlinks=True)
        if bundle_id != original_id:
            info = dict(original)
            info.update(CFBundleIdentifier=bundle_id, CFBundleDisplayName=DISPLAY[name], CFBundleName=name)
            info["BGTaskSchedulerPermittedIdentifiers"] = [
                bundle_id + value[len(original_id):] if value.startswith(original_id + ".") else value
                for value in info.get("BGTaskSchedulerPermittedIdentifiers", [])]
            with (app / "Info.plist").open("wb") as f:
                plistlib.dump(info, f, fmt=plistlib.FMT_BINARY, sort_keys=False)
            # MemoryHost is discovered relative to Bundle.main.bundleIdentifier.
            for extension in app.rglob("*.appex"):
                path = extension / "Info.plist"
                with path.open("rb") as f:
                    nested = plistlib.load(f)
                old = nested["CFBundleIdentifier"]
                if not old.startswith(original_id + "."):
                    raise ValueError("An app extension has an unrelated bundle identifier")
                nested["CFBundleIdentifier"] = bundle_id + old[len(original_id):]
                with path.open("wb") as f:
                    plistlib.dump(nested, f, fmt=plistlib.FMT_BINARY, sort_keys=False)
        staged.append((name, root))
    return staged


# Sideloaders sign with the provisioning profile's entitlements FILTERED BY THE APP'S
# OWN (SideStore/AltStore's AltSign: "Original app does not have this entitlement, so
# don't give it to resigned app"). An IPA without a signature carries no entitlements,
# so the Increased Memory Limit and Extended Virtual Addressing that GetMoreRam enables
# on the App ID were dropped for testers (owner, 2026-10-18), while upstream's 0.1.3,
# signed in Xcode, carried them. An ad-hoc signature (no certificate, no team) carries
# them and names no one. The owner's OTA signing re-signs everything with the profile.
APP = Path(__file__).resolve().parents[1] / "app"
SIDELOAD_ENTITLEMENTS = APP / "Madeira" / "Sideload.entitlements"
EXTENSION_ENTITLEMENTS = {"MadeiraMemoryHost.appex": APP / "MadeiraMemoryHost" / "MemoryHost.entitlements",
                          "MadeiraJITHelper.appex": APP / "MadeiraJITHelper" / "JITHelper.entitlements"}


def fakesign(app, entitlements=SIDELOAD_ENTITLEMENTS):
    app = Path(app)

    def sign(path, ents=None):
        command = ["codesign", "--force", "--sign", "-", "--timestamp=none"]
        if ents:
            command += ["--generate-entitlement-der", "--entitlements", str(ents)]
        subprocess.run(command + [str(path)], check=True, capture_output=True)

    # Inside out: a bundle's seal covers its nested code, so that is signed first.
    nested = [p for folder in ("Frameworks", "PlugIns", "Extensions") if (app / folder).is_dir()
              for p in (app / folder).iterdir() if p.suffix in (".framework", ".appex", ".dylib")]
    for path in sorted(nested, key=lambda p: len(p.parts), reverse=True):
        sign(path, EXTENSION_ENTITLEMENTS.get(path.name))
    sign(app, entitlements)


def package(source, output, build):
    output = Path(output).resolve()
    for name, root in stage_variants(source, output):
        fakesign(root / "Payload" / (name + ".app"))
        target = output / f"{name}-0.1.{build}-unsigned.ipa"
        # No ._ AppleDouble files for extended attributes a Mac gave the files: iOS
        # refuses a bundle with files its signature does not cover.
        subprocess.run(["ditto", "-c", "-k", "--norsrc", "--noextattr", "--noqtn", "--noacl",
                        "--keepParent", "Payload", str(target)], cwd=root, check=True)
        print(f"Packaged {name} 0.1.{build}")


def build_version(key):
    patterns = (r"ota/Madeira2?-([0-9]+(?:\.[0-9]+)+)\.ipa",
                r"ota/manifest(?:-Madeira2)?-([0-9]+(?:\.[0-9]+)+)\.plist",
                r"kurulum(?:-Madeira2?)?-([0-9]+(?:\.[0-9]+)+)\.html")
    for pattern in patterns:
        match = re.fullmatch(pattern, key)
        if match:
            return match[1]
    return None


def prune_plan(listing, reserve, budget, keep, current):
    objects = {}
    for line in listing.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[2].isdigit():
            objects[parts[3]] = int(parts[2])
    groups = {}
    for key in objects:
        version = build_version(key)
        if version:
            groups.setdefault(version, []).append(key)
    total, builds = sum(objects.values()), len(groups)
    if reserve:
        # Reserve both new IPAs together; metadata allowance is included by the caller.
        total -= sum(objects.get(f"ota/{name}-{current}.ipa", 0) for name, _ in VARIANTS)
        total += reserve
        if current not in groups:
            builds += 1
    delete = []
    for version in sorted(groups, key=lambda v: tuple(map(int, v.split(".")))):
        if version == current:
            continue
        if builds <= keep and total <= budget:
            break
        delete.extend(groups[version])
        total -= sum(objects[key] for key in groups[version])
        builds -= 1
    return dict(delete=delete, used=total, limit=budget, builds=builds)


def publication(version, build, secondary):
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    return dict(version=version, build=str(build), created=now.strftime("%Y-%m-%d %H:%M TRT"),
                until=(now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M TRT"),
                variants=[dict(name=name, display=DISPLAY[name], bundle_id=bundle_id)
                          for name, bundle_id in VARIANTS[:2 if secondary else 1]])


def manifest_key(name, version):
    return f"manifest-{'Madeira2-' if name == 'Madeira2' else ''}{version}.plist"


def page_key(name, version):
    return f"kurulum-{name}-{version}.html"


def install_page(data, variants):
    version, build = html.escape(data["version"]), html.escape(data["build"])
    title = " ve ".join(html.escape(row["display"]) for row in variants)
    cards = []
    for row in variants:
        name = html.escape(row["display"])
        install = "itms-services://?action=download-manifest&url=" + quote(row["manifest_url"], safe="")
        cards.append(f'<section><h2>{name}</h2><a class="b" href="{html.escape(install)}">{name} Yükle</a></section>')
    return ('<!doctype html><html lang="tr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title} {version}</title><style>'
            'body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;'
            'display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px}'
            '.c{text-align:center;max-width:420px}section{margin:24px 0}'
            'a.b{display:inline-block;padding:14px 28px;border-radius:12px;background:#0a84ff;'
            'color:#fff;text-decoration:none;font-size:18px;font-weight:600}p{color:#aaa;font-size:14px}'
            f'</style></head><body><div class="c"><h1>{title} {version}</h1>'
            f'<p>Build {build} &middot; {html.escape(data["created"])}</p>' + ''.join(cards) +
            f'<p>Dokun, ardından "Yükle"yi onayla. Linkler {html.escape(data["until"])} tarihine kadar geçerli.</p>'
            '</div></body></html>')


def render(data, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for row in data["variants"]:
        item = dict(assets=[dict(kind="software-package", url=row["ipa_url"])],
                    metadata={"bundle-identifier": row["bundle_id"], "bundle-version": data["version"],
                              "kind": "software", "title": row["display"] + " " + data["version"]})
        with (directory / manifest_key(row["name"], data["version"])).open("wb") as f:
            plistlib.dump(dict(items=[item]), f)
        (directory / page_key(row["name"], data["version"])).write_text(install_page(data, [row]))
    (directory / f'kurulum-{data["version"]}.html').write_text(install_page(data, data["variants"]))


# The signed .ipa links under the install buttons (owner, 2026-10-17, for testers who re-sign).
# Off since the same day: "maildeki şu kısımları da kaldır şimdilik. belki sonra ihtiyacımız olur".
MAIL_IPA_LINKS = False


def email_message(data, sender, recipient):
    names = " ve ".join(row["display"] for row in data["variants"])
    message = EmailMessage()
    message["Subject"] = f'{names} {data["version"]} kuruluma hazır'
    message["From"], message["To"] = sender, recipient
    links = '\n\n'.join(row["display"] + ' kurulum sayfası:\n' + row["page_url"] for row in data["variants"])
    # Owner, 2026-10-17: every mail also carries the signed .ipa itself, under the
    # install buttons, for testers who re-sign it (same pre-signed URL as the manifest's).
    ipas = '\n\n'.join(row["display"] + ' .ipa (doğrudan indirme):\n' + row["ipa_url"] for row in data["variants"])
    message.set_content(f'{names} {data["version"]} (build {data["build"]}) imzalandı.\n\n{links}\n\n'
                        f'iPhone’da açıp Yükle’ye dokun. Linkler {data["until"]} tarihine kadar geçerli.\n'
                        + (f'\n{ipas}\n' if MAIL_IPA_LINKS else ''))
    buttons = ''.join(f'<p><a href="{html.escape(row["page_url"])}" style="display:inline-block;'
                      'padding:12px 24px;border-radius:10px;background:#0a84ff;color:#fff;'
                      f'text-decoration:none;font-weight:600">{html.escape(row["display"])} Kurulum</a></p>'
                      for row in data["variants"])
    downloads = ''.join(f'<p><a href="{html.escape(row["ipa_url"])}">{html.escape(row["display"])} .ipa indir</a></p>'
                        for row in data["variants"])
    message.add_alternative(f'<p>{html.escape(names)} <b>{html.escape(data["version"])}</b> '
                            f'(build {html.escape(data["build"])}) imzalandı.</p>{buttons}'
                            '<p style="color:#888">Açılan sayfada Yükle’ye dokun. '
                            f'Linkler {html.escape(data["until"])} tarihine kadar geçerli.</p>'
                            + ('<p style="color:#888">İmzalı .ipa dosyasının kendisi (doğrudan indirme):</p>'
                               f'{downloads}' if MAIL_IPA_LINKS else ''), subtype="html")
    return message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("package")
    p.add_argument("source"); p.add_argument("output"); p.add_argument("build", type=int)
    p = commands.add_parser("prune")
    p.add_argument("reserve", type=int); p.add_argument("budget", type=float)
    p.add_argument("keep", type=int); p.add_argument("current"); p.add_argument("listing")
    p = commands.add_parser("init")
    p.add_argument("path"); p.add_argument("version"); p.add_argument("build")
    p.add_argument("--secondary", action="store_true")
    p = commands.add_parser("links")
    p.add_argument("path"); p.add_argument("name", choices=[row[0] for row in VARIANTS])
    p = commands.add_parser("render")
    p.add_argument("path"); p.add_argument("directory")
    p = commands.add_parser("mail"); p.add_argument("path")
    args = parser.parse_args()
    if args.command == "package":
        package(args.source, args.output, args.build)
    elif args.command == "prune":
        print(json.dumps(prune_plan(Path(args.listing).read_text(), args.reserve,
                                    int(args.budget * 1e9), args.keep, args.current)))
    elif args.command == "init":
        Path(args.path).write_text(json.dumps(publication(args.version, args.build, args.secondary)))
    else:
        data = json.loads(Path(args.path).read_text())
        if args.command == "links":
            row = next(row for row in data["variants"] if row["name"] == args.name)
            row["bundle_id"] = os.environ["OTA_BUNDLE_ID"]
            for field in ("ipa_url", "manifest_url", "page_url"):
                row[field] = os.environ["OTA_" + field.upper()]
            Path(args.path).write_text(json.dumps(data))
        elif args.command == "render":
            render(data, args.directory)
        elif args.command == "mail":
            message = email_message(data, os.environ["OTA_MAIL_USER"], os.environ["OTA_MAIL_TO"])
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(os.environ["OTA_MAIL_USER"], os.environ["OTA_MAIL_APP_PASSWORD"])
                smtp.send_message(message)


if __name__ == "__main__":
    main()
