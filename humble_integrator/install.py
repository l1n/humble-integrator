"""Install a downloaded DRM-free build without root, and find what to launch.

    python3 -I humble_integrator/install.py unpack ARCHIVE DEST
    python3 -I humble_integrator/install.py launcher DIR [--name NAME] [--system Darwin|Linux|Windows]
    python3 -I humble_integrator/install.py detect SETUP.EXE
"""

import argparse
import io
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from difflib import SequenceMatcher
from pathlib import Path


class UnsafeArchive(Exception):
    pass


def _safe_target(dest, member_name):
    target = (dest / member_name).resolve()
    if not str(target).startswith(str(dest.resolve()) + os.sep) and target != dest.resolve():
        raise UnsafeArchive(f"refusing to write outside the install dir: {member_name}")
    return target


def unzip(path_or_file, dest):
    dest = Path(dest)
    with zipfile.ZipFile(path_or_file) as z:
        for info in z.infolist():
            target = _safe_target(dest, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111:
                target.chmod(mode | 0o600)


def untar(path_or_fileobj, dest):
    dest = Path(dest)
    kwargs = {"fileobj": path_or_fileobj} if hasattr(path_or_fileobj, "read") else {"name": path_or_fileobj}
    with tarfile.open(mode="r:*", **kwargs) as t:
        for m in t.getmembers():
            _safe_target(dest, m.name)
            if m.issym() or m.islnk():
                _safe_target(dest, os.path.join(os.path.dirname(m.name), m.linkname))
            if m.isdev():
                raise UnsafeArchive(f"device file in archive: {m.name}")
        t.extractall(dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))


def undeb(path, dest):
    """Extract a .deb's files (no maintainer scripts, no root): ar archive -> data.tar.*."""
    data = Path(path).read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise ValueError(f"{path} is not a .deb")
    i = 8
    while i + 60 <= len(data):
        name = data[i:i + 16].decode().strip().rstrip("/")
        size = int(data[i + 48:i + 58].decode().strip())
        body = data[i + 60:i + 60 + size]
        if name.startswith("data.tar"):
            untar(io.BytesIO(body), dest)
            return
        i += 60 + size + (size & 1)
    raise ValueError(f"no data.tar in {path}")


def unpack(archive, dest, system=None):
    """Unpack ARCHIVE into DEST. Returns DEST, or None for a Windows installer (needs Wine)."""
    archive, dest = Path(archive), Path(dest)
    system = system or platform.system()
    name = archive.name.lower()
    dest.mkdir(parents=True, exist_ok=True)
    if name.endswith((".exe", ".msi")):
        return None
    if name.endswith(".zip"):
        unzip(archive, dest)
    elif re.search(r"\.(tar(\.\w+)?|tgz|tbz2?|txz)$", name):
        untar(archive, dest)
    elif name.endswith(".deb"):
        undeb(archive, dest)
    elif name.endswith(".appimage"):
        out = dest / archive.name
        shutil.copy2(archive, out)
        out.chmod(out.stat().st_mode | 0o755)
    elif name.endswith(".sh"):
        unpack_sh(archive, dest)
    elif name.endswith(".dmg") and system == "Darwin":
        undmg(archive, dest)
    else:
        raise ValueError(f"don't know how to unpack {archive.name}")
    return dest


def unpack_sh(path, dest):
    head = Path(path).read_bytes()[:20000]
    if b"Makeself" in head or b"makeself" in head:
        subprocess.run(["sh", str(path), "--noexec", "--target", str(dest)], check=True)
    elif zipfile.is_zipfile(path):
        # MojoSetup (GOG-style) installers are a shell stub with a zip appended.
        unzip(path, dest)
    else:
        raise ValueError(f"{path.name}: unknown .sh installer (not makeself or MojoSetup)")


def undmg(path, dest):
    mnt = Path(tempfile.mkdtemp(prefix="hi-dmg-"))
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
                    "-mountpoint", str(mnt), str(path)], check=True, capture_output=True)
    try:
        apps = [p for p in mnt.iterdir() if p.suffix == ".app"]
        for app in apps or [mnt]:
            subprocess.run(["ditto", str(app), str(dest / app.name) if apps else str(dest)], check=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mnt), "-quiet"], check=False)
        shutil.rmtree(mnt, ignore_errors=True)


# ---------------------------------------------------------------- launchers

NOT_THE_GAME = re.compile(
    r"unins|setup|install|crash|redist|vcredist|dxsetup|directx|dotnet|launcherhelper|"
    r"updater|uninstall|cleanup|report|benchmark|server|ue4prereq|dxwebsetup|oalinst", re.I)


def _is_elf(p):
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def _similarity(a, b):
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def find_launcher(root, system=None, name=""):
    """Best guess at the program to launch in an install dir, or None."""
    root = Path(root)
    system = system or platform.system()
    candidates = []
    if system == "Darwin":
        for p in root.rglob("*.app"):
            if not any(part.endswith(".app") for part in p.relative_to(root).parts[:-1]):
                candidates.append(p)
    elif system == "Linux":
        for p in root.rglob("*"):
            if not p.is_file() or NOT_THE_GAME.search(p.name):
                continue
            executable = p.stat().st_mode & stat.S_IXUSR
            if p.name in ("start.sh", "run.sh", "launch.sh") or p.suffix in (".x86_64", ".AppImage") \
                    or (executable and (_is_elf(p) or p.suffix == ".sh")):
                if p.suffix in (".so",) or ".so." in p.name:
                    continue
                candidates.append(p)
    else:
        candidates = [p for p in root.rglob("*.exe") if not NOT_THE_GAME.search(p.name)]
    if not candidates:
        return None

    def score(p):
        depth = len(p.relative_to(root).parts)
        bonus = 0.5 if p.name in ("start.sh", "run.sh") else 0
        bonus += 0.2 if p.suffix == ".x86_64" or "64" in p.name else 0
        return _similarity(p.stem, name) + bonus - 0.15 * depth

    return max(candidates, key=score)


MACHO_CPU = {7: "i386", 0x01000007: "x86_64", 12: "arm", 0x0100000C: "arm64", 18: "ppc", 0x01000012: "ppc64"}


def mac_archs(app):
    """CPU architectures of a .app's main executable (empty set if it can't be read)."""
    import plistlib
    import struct
    app = Path(app)
    try:
        exe_name = plistlib.loads((app / "Contents" / "Info.plist").read_bytes()).get("CFBundleExecutable")
    except Exception:
        exe_name = None
    macos = app / "Contents" / "MacOS"
    exe = macos / exe_name if exe_name else next((p for p in sorted(macos.glob("*")) if p.is_file()), None)
    try:
        head = exe.open("rb").read(4096)
    except (OSError, AttributeError):
        return set()
    magic = head[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):  # universal binary
        step = 20 if magic[3] == 0xBE else 32
        n = struct.unpack(">I", head[4:8])[0]
        cpus = [struct.unpack(">i", head[8 + i * step:12 + i * step])[0] for i in range(min(n, 16))]
    elif magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        cpus = [struct.unpack("<i", head[4:8])[0]]
    elif magic in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"):
        cpus = [struct.unpack(">i", head[4:8])[0]]
    else:
        return set()  # a script or something else: can't tell
    return {MACHO_CPU.get(c, hex(c)) for c in cpus}


def runs_on_current_macos(app):
    """False only for builds that are definitely 32-bit/PowerPC (macOS 10.15+ runs 64-bit only)."""
    archs = mac_archs(app)
    return not archs or bool(archs & {"x86_64", "arm64"})


# ---------------------------------------------------------------- Windows installers

def installer_kind(path):
    path = Path(path)
    if path.suffix.lower() == ".msi":
        return "msi"
    with open(path, "rb") as f:
        head = f.read(4 << 20)
    if b"Inno Setup" in head:
        return "inno"
    if b"Nullsoft" in head or b"NSIS" in head:
        return "nsis"
    if b"InstallAware" in head:
        return "installaware"
    if head[:2] == b"MZ" and zipfile.is_zipfile(path):
        return "sfx-zip"
    return "exe"


def silent_command(path, kind, target_dir):
    """Command (argv, run under Wine) that installs PATH unattended into TARGET_DIR (a Windows path)."""
    path = str(path)
    if kind == "inno":
        return [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/DIR={target_dir}"]
    if kind == "nsis":
        return [path, "/S", f"/D={target_dir}"]  # /D must be last and unquoted
    if kind == "msi":
        return ["msiexec", "/i", path, "/qn", f"TARGETDIR={target_dir}"]
    if kind == "installaware":
        return [path, "/s", f"TARGETDIR={target_dir}"]
    return None  # unknown: run interactively, with the wizard driver if a serial is needed


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("unpack")
    u.add_argument("archive")
    u.add_argument("dest")
    lp = sub.add_parser("launcher")
    lp.add_argument("dir")
    lp.add_argument("--name", default="")
    lp.add_argument("--system")
    d = sub.add_parser("detect")
    d.add_argument("setup")
    a = p.parse_args(argv)
    if a.cmd == "unpack":
        print(unpack(a.archive, a.dest) or "Windows installer: run it under Wine")
    elif a.cmd == "launcher":
        print(find_launcher(a.dir, a.system, a.name))
    else:
        kind = installer_kind(a.setup)
        print(kind, silent_command(a.setup, kind, r"C:\Games\App") or "(interactive)")


if __name__ == "__main__":
    main()
