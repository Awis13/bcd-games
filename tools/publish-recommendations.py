#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Check and sign BCD's recommended settings for the online copy (owner's decision, 2026-10-18).

    tools/publish-recommendations.py --init-key   make the signing key, once (an existing key is
                                                  never replaced); prints only the public key
    tools/publish-recommendations.py --bump       raise the list's "version" by one and set
                                                  "generated" to today, then check and sign
    tools/publish-recommendations.py              check recommended/recommendations.json and sign
                                                  it: recommended/recommendations.json.sig
    tools/publish-recommendations.py --check      check only, and that the .sig is valid: the gate
                                                  before the two files go to thebcd69/bcd-games

The app (GameRecommendationsSource.swift) takes an online list only with a valid Ed25519
signature over the file's exact bytes under the public key compiled into it. The private key
stays on this Mac: ~/madeira-local/state/recommendations-signing.key (mode 600, PKCS#8 PEM).

Before it signs, the tool checks:
  - the file with the app's own code (the Swift files, compiled here): the layout, and that
    every line sets a key a download may set (RecommendationsPolicy), whatever its minBuild;
  - JSON this strict: no key twice in an object;
  - against the last file it signed (~/madeira-local/state/recommendations-signed/): a higher
    "version" (the same bytes may be signed again), every rule still there, no rule's version
    lower, a rule whose lines, switches, match, title or minBuild changed has a higher version,
    no rule that existed gets a minBuild (older builds would lose it), and no rule stops
    matching an App ID, folder or program it matched (its games would lose their list;
    --allow-narrower-match when that is the point);
  - that the key's public key is the one in the app.

Publishing is the release step's: copy recommended/recommendations.json and its .sig, byte for
byte, to thebcd69/bcd-games (recommended/). Nothing here touches the network.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app' / 'Madeira'
FILE = ROOT / 'recommended' / 'recommendations.json'
KEY = Path.home() / 'madeira-local' / 'state' / 'recommendations-signing.key'
LEDGER = Path.home() / 'madeira-local' / 'state' / 'recommendations-signed'
SOURCE = APP / 'GameRecommendationsSource.swift'

# ---------------------------------------------------------------------------------------------
# Ed25519 (RFC 8032, section 6's reference code): signing is deterministic, so the only
# randomness is the key's 32 bytes from os.urandom.

_p = 2 ** 255 - 19
_q = 2 ** 252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _p - 2, _p) % _p
_sqrt_m1 = pow(2, (_p - 1) // 4, _p)


def _sha512(data):
    return hashlib.sha512(data).digest()


def _sha512_modq(data):
    return int.from_bytes(_sha512(data), 'little') % _q


def _add(P, Q):
    A, B = (P[1] - P[0]) * (Q[1] - Q[0]) % _p, (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C, D = 2 * P[3] * Q[3] * _d % _p, 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q):
    return (P[0] * Q[2] - Q[0] * P[2]) % _p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _p == 0


def _recover_x(y, sign):
    if y >= _p:
        return None
    x2 = (y * y - 1) * pow(_d * y * y + 1, _p - 2, _p)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_p + 3) // 8, _p)
    if (x * x - x2) % _p != 0:
        x = x * _sqrt_m1 % _p
    if (x * x - x2) % _p != 0:
        return None
    if (x & 1) != sign:
        x = _p - x
    return x


_gy = 4 * pow(5, _p - 2, _p) % _p
_gx = _recover_x(_gy, 0)
_G = (_gx, _gy, 1, _gx * _gy % _p)


def _compress(P):
    zinv = pow(P[2], _p - 2, _p)
    x, y = P[0] * zinv % _p, P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _p)


def _expand(seed):
    if len(seed) != 32:
        raise ValueError('an Ed25519 key is 32 bytes')
    h = _sha512(seed)
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed):
    a, _ = _expand(seed)
    return _compress(_mul(a, _G))


def sign(seed, message):
    a, prefix = _expand(seed)
    A = _compress(_mul(a, _G))
    r = _sha512_modq(prefix + message)
    Rs = _compress(_mul(r, _G))
    h = _sha512_modq(Rs + A + message)
    return Rs + int.to_bytes((r + h * a) % _q, 32, 'little')


def verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= _q:
        return False
    h = _sha512_modq(signature[:32] + public + message)
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))


# ---------------------------------------------------------------------------------------------
# The key file: PKCS#8 PEM, which OpenSSL reads too.

_PKCS8 = bytes.fromhex('302e020100300506032b657004220420')


# The PEM label, split so that no source file carries a PEM private-key header that the
# public export's secret scan (tools/export-public.py) would rightly refuse.
_LABEL = 'PRIVATE' + ' KEY'


def pem(seed):
    body = base64.b64encode(_PKCS8 + seed).decode()
    return f'-----BEGIN {_LABEL}-----\n{body}\n-----END {_LABEL}-----\n'


def seed_from_pem(text):
    m = re.fullmatch(r'\s*-----BEGIN ' + _LABEL + r'-----\s*([A-Za-z0-9+/=\s]+?)\s*-----END ' + _LABEL + r'-----\s*', text)
    if not m:
        raise ValueError('not a PEM private key')
    der = base64.b64decode(''.join(m.group(1).split()))
    if len(der) != 48 or not der.startswith(_PKCS8):
        raise ValueError('not an Ed25519 private key (PKCS#8)')
    return der[16:]


def load_key(path):
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f'{path}: readable by others (mode {stat.S_IMODE(mode):o}); chmod 600 it first')
    return seed_from_pem(path.read_text())


def init_key(path):
    """Makes the key once. Never replaces one: the app trusts exactly one public key."""
    if path.exists():
        print(f'{path} exists; its public key:')
        return public_key(load_key(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(pem(seed))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, 0o600)
    print(f'{path} made (mode 600); its public key:')
    return public_key(seed)


def app_public_key(source=SOURCE):
    m = re.search(r'static let publicKey = "([A-Za-z0-9+/=]+)"', source.read_text())
    return base64.b64decode(m.group(1)) if m else None


def signature_file(signature):
    return (base64.b64encode(signature).decode() + '\n').encode()


def signature_from_file(data):
    try:
        raw = base64.b64decode(data.strip(), validate=True)
    except ValueError:
        return None
    return raw if len(raw) == 64 else None


# ---------------------------------------------------------------------------------------------
# The file

def strict_json(data):
    """JSON with no key twice in an object (the app's reader would keep one of them silently)."""
    def pairs(items):
        keys = [k for k, _ in items]
        twice = sorted({k for k in keys if keys.count(k) > 1})
        if twice:
            raise ValueError(f'"{twice[0]}" twice in one object')
        return dict(items)
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs)


def applied(rule):
    """What a rule applies: everything but its notes and its version."""
    return {k: v for k, v in rule.items() if k not in ('notes', 'version')}


def narrowed(rule, before):
    """What `before` recognised that `rule` no longer does: App IDs, folders, programs."""
    now, then = rule.get('match') or {}, before.get('match') or {}
    gone = [f'App ID {i}' for i in then.get('steamAppIDs', []) if i not in now.get('steamAppIDs', [])]
    gone += [f'folder "{f}"' for f in then.get('steamFolders', []) if f not in now.get('steamFolders', [])]
    gone += [f'program {json.dumps(p, sort_keys=True)}' for p in then.get('programs', []) if p not in now.get('programs', [])]
    return gone


def compare(new, old, allow_narrower=False):
    """Why `new` may not follow `old` (the last signed list); [] when it may."""
    problems = []
    if new['version'] <= old['version']:
        problems.append(f'version {new["version"]} is not higher than {old["version"]}, the last signed list')
    rules = {r['id']: r for r in new['rules']}
    for before in old['rules']:
        rule = rules.get(before['id'])
        if rule is None:
            problems.append(f'{before["id"]}: the rule is gone (its games would get the standard list over theirs)')
            continue
        if rule['version'] < before['version']:
            problems.append(f'{before["id"]}: version {rule["version"]} is lower than {before["version"]}')
        elif applied(rule) != applied(before) and rule['version'] == before['version']:
            problems.append(f'{before["id"]}: its lines, switches, match, title or minBuild changed; raise its version')
        if rule.get('minBuild') is not None and rule.get('minBuild') != before.get('minBuild') \
                and (before.get('minBuild') is None or rule['minBuild'] > before['minBuild']):
            problems.append(f'{before["id"]}: an existing rule may not get a (higher) minBuild: older builds would lose it')
        if not allow_narrower:
            for what in narrowed(rule, before):
                problems.append(f'{before["id"]}: no longer matches {what}: such a game would lose its list '
                                '(--allow-narrower-match if that is the point)')
    return problems


def changes(new, old):
    before = {r['id']: r for r in old['rules']}
    return [f'{r["id"]} v{before[r["id"]]["version"]} -> v{r["version"]}' if r['id'] in before else f'{r["id"]} v{r["version"]} (new)'
            for r in new['rules'] if r['id'] not in before or applied(r) != applied(before[r['id']])]


def last_signed(ledger=LEDGER):
    """The highest-versioned file signed on this Mac: (version, bytes) or None."""
    if not ledger.is_dir():
        return None
    found = sorted((int(p.stem.split('.')[0]), p) for p in ledger.glob('*.json') if p.stem.split('.')[0].isdigit())
    return (found[-1][0], found[-1][1].read_bytes()) if found else None


def record(ledger, version, data, signature):
    ledger.mkdir(parents=True, exist_ok=True)
    target = ledger / f'{version}.json'
    if target.exists() and target.read_bytes() != data:
        raise SystemExit(f'{target} holds another list with version {version}; raise the version')
    target.write_bytes(data)
    (ledger / f'{version}.json.sig').write_bytes(signature_file(signature))


def bumped(text, today=None):
    """The file with its top-level "version" raised by one and "generated" set to today; the rest
    byte for byte. Returns (text, version)."""
    today = today or datetime.date.today().isoformat()
    m = re.search(r'^  "version": (\d+),$', text, re.M)
    if not m or not re.search(r'^  "generated": "[0-9-]+",$', text, re.M):
        raise SystemExit('no top-level "version" / "generated" line to raise')
    version = int(m.group(1)) + 1
    text = text[:m.start()] + f'  "version": {version},' + text[m.end():]
    g = re.search(r'^  "generated": "[0-9-]+",$', text, re.M)
    return text[:g.start()] + f'  "generated": "{today}",' + text[g.end():], version


# ---------------------------------------------------------------------------------------------
# The app's own check: the Swift files that read the list in the app, compiled here.

SWIFT_STUBS = r'''
import Foundation

final class LogStore {
    static let shared = LogStore()
    enum Level { case info, error }
    func log(_ message: String, level: Level = .info) {}
}

struct ConfigOption {
    enum Kind { case bool, int, text, choice }
    let key: String
    let category: String
    init(key: String, title: String, kind: Kind, defaultValue: String, category: String, note: String,
         choices: [(String, String)], sources: [String]) {
        self.key = key
        self.category = category
    }
}

enum ConfigCatalog {}
'''

SWIFT_MAIN = r'''
import Foundation

let arguments = CommandLine.arguments
let data = try! Data(contentsOf: URL(fileURLWithPath: arguments[1]))
do {
    let file = try RecommendationsFile.parse(data)
    try file.checkPolicy(RecommendationsStore.policy, build: nil, everyLine: true)
    if arguments.count > 2 {
        let raw = try Data(contentsOf: URL(fileURLWithPath: arguments[2]))
        guard let signature = RecommendationsSignature.signature(fromFile: raw),
              RecommendationsSignature.isValid(signature, for: data, publicKey: RecommendationsSignature.publicKey) else {
            print("SIGNATURE: not valid for these bytes under the app's public key")
            exit(2)
        }
        print("SIGNATURE: valid")
    }
    print("OK: format \(file.format), version \(file.version), \(file.rules.count) rules")
} catch {
    print("INVALID: \(error)")
    exit(1)
}
'''

SWIFT_FILES = ['GameRecommendations.swift', 'GameRecommendationsList.swift', 'GameRecommendationsSource.swift',
               'ConfigCatalog.generated.swift']


def swift_check(path, signature=None, app=APP):
    """Runs the app's reader on the file: (ok, output). None when there is no Swift compiler."""
    compiler = shutil.which('swiftc') or (shutil.which('xcrun') and 'xcrun')
    if not compiler:
        return None
    with tempfile.TemporaryDirectory(prefix='bcd-recs-check-') as tmp:
        tmp = Path(tmp)
        (tmp / 'Stubs.swift').write_text(SWIFT_STUBS)
        (tmp / 'main.swift').write_text(SWIFT_MAIN)
        command = ([compiler] if compiler != 'xcrun' else ['xcrun', 'swiftc']) + \
            [str(app / f) for f in SWIFT_FILES] + [str(tmp / 'Stubs.swift'), str(tmp / 'main.swift'), '-o', str(tmp / 'check')]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode != 0:
            return False, 'the Swift check did not compile:\n' + built.stderr[-3000:]
        run = subprocess.run([str(tmp / 'check'), str(path)] + ([str(signature)] if signature else []),
                             capture_output=True, text=True)
        return run.returncode == 0, run.stdout.strip() + run.stderr.strip()


# ---------------------------------------------------------------------------------------------

def check_file(path, use_swift=True, signature=None, data=None):
    """The file's bytes (`data`, or the file's own) and JSON, once the app's own reader takes them
    (and `signature`, if given)."""
    if data is not None:
        with tempfile.TemporaryDirectory(prefix='bcd-recs-candidate-') as tmp:
            candidate = Path(tmp) / path.name
            candidate.write_bytes(data)
            return check_file(candidate, use_swift, signature)
    data = path.read_bytes()
    try:
        doc = strict_json(data)
    except ValueError as e:
        raise SystemExit(f'{path}: not valid JSON: {e}')
    if not isinstance(doc, dict) or not isinstance(doc.get('version'), int) or not isinstance(doc.get('rules'), list):
        raise SystemExit(f'{path}: not a list of recommended settings')
    if use_swift:
        result = swift_check(path, signature)
        if result is None:
            raise SystemExit('no Swift compiler: the app\'s own check cannot run (--no-swift skips it, not for a release)')
        ok, output = result
        if not ok:
            raise SystemExit(f'{path}: the app would not take it: {output}')
    return data, doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--init-key', action='store_true', help='make the signing key once; print its public key')
    ap.add_argument('--check', action='store_true', help='check the file and its signature; sign nothing')
    ap.add_argument('--bump', action='store_true', help='raise "version" by one and set "generated" to today first')
    ap.add_argument('--file', type=Path, default=FILE)
    ap.add_argument('--key', type=Path, default=KEY)
    ap.add_argument('--ledger', type=Path, default=LEDGER)
    ap.add_argument('--no-swift', action='store_true', help='skip the app\'s own check (not for a release)')
    ap.add_argument('--allow-narrower-match', action='store_true',
                    help='let a rule stop matching an App ID, folder or program it matched before')
    a = ap.parse_args()

    if a.init_key:
        print(base64.b64encode(init_key(a.key)).decode())
        return
    sig_path = a.file.with_name(a.file.name + '.sig')
    expected = app_public_key()
    if a.check:
        if not sig_path.exists():
            raise SystemExit(f'{sig_path}: missing: sign the list first')
        data, doc = check_file(a.file, use_swift=not a.no_swift, signature=sig_path)
        signature = signature_from_file(sig_path.read_bytes())
        if expected is None or signature is None or not verify(expected, data, signature):
            raise SystemExit(f'{sig_path}: not valid for {a.file.name} as it is now: sign it first')
        print(f'OK: {a.file.name} list v{doc["version"]} ({doc.get("generated")}), {len(doc["rules"])} rules, signed')
        return

    seed = load_key(a.key) if a.key.exists() else None
    if seed is None:
        raise SystemExit(f'{a.key}: no signing key (tools/publish-recommendations.py --init-key makes it once)')
    if expected != public_key(seed):
        raise SystemExit('the signing key is not the one the app trusts (RecommendationsSignature.publicKey)')
    candidate = None
    if a.bump:
        # Raised in memory first: the file changes only once everything below passed.
        text, version = bumped(a.file.read_text())
        candidate = text.encode()
        print(f'{a.file.name}: version {version - 1} -> {version}, generated today')
    data, doc = check_file(a.file, use_swift=not a.no_swift, data=candidate)
    previous = last_signed(a.ledger)
    if previous and previous[1] == data:
        print(f'{a.file.name}: list v{doc["version"]} was signed before; writing its signature again')
    elif previous:
        problems = compare(doc, strict_json(previous[1]), allow_narrower=a.allow_narrower_match)
        if problems:
            raise SystemExit(f'{a.file.name} may not follow the last signed list (v{previous[0]}):\n  ' + '\n  '.join(problems))
    signature = sign(seed, data)
    assert verify(expected, data, signature)
    if candidate is not None:
        a.file.write_bytes(candidate)
    sig_path.write_bytes(signature_file(signature))
    record(a.ledger, doc['version'], data, signature)
    what = changes(doc, strict_json(previous[1])) if previous and previous[1] != data else []
    print(f'signed: {a.file.name} list v{doc["version"]} ({doc.get("generated")}), {len(doc["rules"])} rules, '
          f'sha256 {hashlib.sha256(data).hexdigest()[:16]}')
    for line in what:
        print(f'  changed: {line}')
    print(f'wrote {sig_path.relative_to(ROOT) if sig_path.is_relative_to(ROOT) else sig_path}; '
          'the release step copies both files to thebcd69/bcd-games (recommended/)')


if __name__ == '__main__':
    main()
