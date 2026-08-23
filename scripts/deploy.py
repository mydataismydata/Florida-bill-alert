#!/usr/bin/env python3
"""Push the built site to the public host over SFTP, sending only what changed.

One command, no browser, no file-by-file uploading. Shared hosting gives SFTP
even on plans with no shell, so this needs nothing installed on the server.

    scripts/deploy.py                 # dry run -- prints what would change
    scripts/deploy.py --go            # push

Configure once in .env (gitignored) or the environment:

    FLBA_HOST=access.example.com
    FLBA_USER=u12345678
    FLBA_PATH=/billalert            # remote directory, as SFTP sees it

Authentication is an SSH key, so a push never stops to ask for a password:

    ssh-keygen -t ed25519 -f ~/.ssh/ionos -N ''
    ssh-copy-id -i ~/.ssh/ionos.pub u12345678@access.example.com

Set FLBA_KEY if the key is not at ~/.ssh/ionos.

Why not rsync: it needs a shell on the far end, and shared hosting often sells
SFTP without one. Why not a plain mirror: the site is regenerated from
templates, so every file's timestamp changes on every build even when its bytes
do not. Comparing content instead means a routine rebuild ships the handful of
pages that really moved rather than all 9,731.

The first push is the whole site. After that a manifest in .deploy/ records
what the server holds, and only the difference travels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(os.environ.get("FLBA_SRC", ROOT / "site"))
STATE = ROOT / ".deploy"

# Above this, individual PUTs cost more in round trips than shipping one
# archive and unpacking it on the far side.
BUNDLE_THRESHOLD = 400


def load_env() -> None:
    """Read .env if present, without overriding a real environment variable."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        out[str(path.relative_to(root))] = digest(path)
    return out


def plan(current: dict[str, str], deployed: dict[str, str]):
    upload = sorted(k for k, v in current.items() if deployed.get(k) != v)
    remove = sorted(set(deployed) - set(current))
    return upload, remove


def sftp_batch(commands: list[str], host: str, user: str, key: str | None,
               quiet: bool = False) -> None:
    """Run one SFTP session. `-` before a command tolerates its failure, which
    is how mkdir works when the directory is already there."""
    args = ["sftp", "-oBatchMode=yes"]
    if key:
        args += ["-i", key]
    args += ["-b", "-", f"{user}@{host}"]
    script = "\n".join(commands) + "\nbye\n"
    proc = subprocess.run(args, input=script, text=True,
                          stdout=subprocess.PIPE if quiet else None,
                          stderr=subprocess.STDOUT if quiet else None)
    if proc.returncode != 0:
        if quiet and proc.stdout:
            sys.stderr.write(proc.stdout)
        raise SystemExit(f"sftp failed with status {proc.returncode}")


def push_files(paths: list[str], dest: str, host: str, user: str,
               key: str | None) -> None:
    """Upload each file, creating the directories it needs first.

    Directories are created deepest-last in sorted order so a parent always
    precedes its child, and every mkdir is prefixed with `-` because most of
    them will already exist.
    """
    dirs = sorted({str(Path(p).parent) for p in paths if str(Path(p).parent) != "."})
    commands = [f'-mkdir "{dest}"']
    for d in dirs:
        parts = Path(d).parts
        for i in range(1, len(parts) + 1):
            commands.append(f'-mkdir "{dest}/{"/".join(parts[:i])}"')
    commands = list(dict.fromkeys(commands))
    for p in paths:
        commands.append(f'put "{SRC / p}" "{dest}/{p}"')
    sftp_batch(commands, host, user, key)


def push_bundle(paths: list[str], dest: str, host: str, user: str,
                key: str | None, token: str) -> None:
    """Ship one archive plus the script that unpacks it.

    Thousands of separate PUTs spend most of their time waiting on round
    trips. One archive and a short PHP unpacker moves the same bytes in a
    fraction of the wall time. The unpacker requires a token, deletes the
    archive when it is done, and then deletes itself, so nothing that can
    write to the web root is left behind.
    """
    import zipfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "_deploy.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
            for p in paths:
                z.write(SRC / p, p)
        unpack = Path(tmp) / "_unpack.php"
        unpack.write_text(UNPACK_PHP.replace("__TOKEN__", token))
        sftp_batch([f'-mkdir "{dest}"',
                    f'put "{bundle}" "{dest}/_deploy.zip"',
                    f'put "{unpack}" "{dest}/_unpack.php"'], host, user, key)


def delete_files(paths: list[str], dest: str, host: str, user: str,
                 key: str | None) -> None:
    if not paths:
        return
    sftp_batch([f'-rm "{dest}/{p}"' for p in paths], host, user, key, quiet=True)


def unpack(url: str, token: str) -> str | None:
    """Ask the server to extract the bundle. None if it could not.

    The usual reason is a host without PHP's zip extension, which is worth
    saying plainly rather than leaving the site half-updated.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/_unpack.php?t={token}",
                                    timeout=300) as r:
            return r.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"  unpack returned HTTP {e.code}: "
                         f"{e.read().decode('utf-8', 'replace')[:200]}\n")
    except Exception as e:                       # network, timeout, bad TLS
        sys.stderr.write(f"  unpack could not be reached: {e}\n")
    return None


UNPACK_PHP = """<?php
// Written by scripts/deploy.py, used once, then deletes itself.
if (!hash_equals('__TOKEN__', $_GET['t'] ?? '')) { http_response_code(403); exit('no'); }
if (!class_exists('ZipArchive')) {
    http_response_code(500);
    exit('this host has no PHP zip extension -- deploy with --files instead');
}
$here = __DIR__;
$zip = new ZipArchive();
if ($zip->open("$here/_deploy.zip") !== true) { http_response_code(500); exit('cannot open bundle'); }
$n = $zip->numFiles;
if (!$zip->extractTo($here)) { $zip->close(); http_response_code(500); exit('extract failed'); }
$zip->close();
@unlink("$here/_deploy.zip");
@unlink(__FILE__);
echo "unpacked $n files";
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--go", action="store_true", help="actually push")
    ap.add_argument("--all", action="store_true",
                    help="ignore the manifest and send everything")
    ap.add_argument("--files", action="store_true",
                    help="force file-by-file instead of a bundle")
    ap.add_argument("--pack", metavar="DIR", nargs="?", const="dist",
                    help="write the bundle and its unpacker locally instead "
                         "of uploading, for a host reachable only by a web "
                         "uploader")
    args = ap.parse_args()

    load_env()
    host = os.environ.get("FLBA_HOST", "")
    user = os.environ.get("FLBA_USER", "")
    dest = os.environ.get("FLBA_PATH", "").rstrip("/")
    key = os.environ.get("FLBA_KEY") or str(Path.home() / ".ssh" / "ionos")
    key = key if Path(key).exists() else None

    if not args.pack and not (host and user and dest):
        print("set FLBA_HOST, FLBA_USER and FLBA_PATH -- see the top of this "
              "file, or put them in .env.\n"
              "No SFTP account? Run with --pack to get two files to upload "
              "by hand instead.", file=sys.stderr)
        return 2
    if not (SRC / "index.html").exists():
        print(f"no built site at {SRC} -- run: flba --session 2026 build",
              file=sys.stderr)
        return 2
    # A --local build carries buttons that run the model on this machine. They
    # would be dead links in public at best. Refuse rather than warn.
    if (SRC / ".local-build").exists():
        print(f"refusing to deploy: {SRC} was built with --local.\n"
              "rebuild without it:  flba --session 2026 build", file=sys.stderr)
        return 3

    print(f"reading {SRC} ...")
    current = manifest(SRC)
    state = STATE / f"{host or 'pack'}{dest.replace('/', '_')}.json"
    deployed = {} if args.all else json.loads(
        state.read_text()) if state.exists() else {}
    upload, remove = plan(current, deployed)

    if not deployed:
        print("no record of a previous push -- this one sends the whole site")
    print(f"{len(upload)} to upload, {len(remove)} to delete, "
          f"{len(current) - len(upload)} unchanged")
    for p in upload[:10]:
        print(f"  + {p}")
    if len(upload) > 10:
        print(f"  ... and {len(upload) - 10} more")
    for p in remove[:10]:
        print(f"  - {p}")
    if len(remove) > 10:
        print(f"  ... and {len(remove) - 10} more")

    if not (upload or remove):
        print("nothing to do.")
        return 0

    if args.pack:
        import zipfile
        out = Path(args.pack)
        out.mkdir(parents=True, exist_ok=True)
        token = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        bundle = out / "_deploy.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
            for p in upload:
                z.write(SRC / p, p)
        (out / "_unpack.php").write_text(UNPACK_PHP.replace("__TOKEN__", token))
        print(f"\nUpload these two files to the site's folder:\n"
              f"  {bundle}  ({bundle.stat().st_size / 1e6:.1f} MB, "
              f"{len(upload)} files)\n"
              f"  {out / '_unpack.php'}\n"
              f"Then open:\n"
              f"  <your site>/_unpack.php?t={token}\n"
              f"It unpacks, then deletes the archive and itself.")
        if remove:
            print(f"\n{len(remove)} files are no longer part of the site and "
                  f"this route cannot delete them:")
            for p in remove[:20]:
                print(f"  {p}")
        STATE.mkdir(exist_ok=True)
        state.write_text(json.dumps(current, indent=0, sort_keys=True))
        return 0

    if not args.go:
        size = sum((SRC / p).stat().st_size for p in upload)
        print(f"\nDRY RUN -- {size / 1e6:.1f} MB would move. "
              "Re-run with --go to push.")
        return 0

    url = os.environ.get("FLBA_URL", "").rstrip("/")
    if len(upload) > BUNDLE_THRESHOLD and not args.files:
        if not url:
            print("set FLBA_URL to the site's public address so the bundle can "
                  "be unpacked without a browser, or pass --files to upload "
                  "them one at a time", file=sys.stderr)
            return 2
        token = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        print(f"bundling {len(upload)} files ...")
        push_bundle(upload, dest, host, user, key, token)
        print("unpacking on the server ...")
        answer = unpack(url, token)
        if answer is None:
            print("\nThe bundle is uploaded but did not unpack. Open this once "
                  "in a browser to finish:", file=sys.stderr)
            print(f"  {url}/_unpack.php?t={token}", file=sys.stderr)
            print("Then re-run with --all so the manifest catches up.",
                  file=sys.stderr)
            return 1
        print(f"  {answer}")
    else:
        print(f"uploading {len(upload)} files ...")
        push_files(upload, dest, host, user, key)
    delete_files(remove, dest, host, user, key)

    STATE.mkdir(exist_ok=True)
    state.write_text(json.dumps(current, indent=0, sort_keys=True))
    print(f"\ndone. {len(upload)} uploaded, {len(remove)} deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
