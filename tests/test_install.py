import io
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "humble_integrator"))
import install  # noqa: E402

ELF = b"\x7fELF" + b"\x00" * 60


def make_tar(path, files, mode="w:gz"):
    with tarfile.open(path, mode) as t:
        for name, (data, perm) in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = perm
            t.addfile(info, io.BytesIO(data))


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tar_keeps_exec_bits_and_launcher_is_found(self):
        make_tar(self.d / "g.tar.gz", {
            "Cool Game/CoolGame.x86_64": (ELF, 0o755),
            "Cool Game/CoolGame_Data/lib.so": (ELF, 0o755),
            "Cool Game/uninstall.sh": (b"#!/bin/sh", 0o755),
            "Cool Game/readme.txt": (b"hi", 0o644),
        })
        out = install.unpack(self.d / "g.tar.gz", self.d / "out", system="Linux")
        exe = install.find_launcher(out, "Linux", "Cool Game")
        self.assertEqual(exe.name, "CoolGame.x86_64")
        self.assertTrue(os.access(exe, os.X_OK))

    def test_start_script_wins_on_linux(self):
        make_tar(self.d / "g.tgz", {"game/start.sh": (b"#!/bin/sh", 0o755), "game/game/bin": (ELF, 0o755)})
        out = install.unpack(self.d / "g.tgz", self.d / "out", system="Linux")
        self.assertEqual(install.find_launcher(out, "Linux", "Other").name, "start.sh")

    def test_archives_cannot_escape_the_install_dir(self):
        with zipfile.ZipFile(self.d / "evil.zip", "w") as z:
            z.writestr("../../escaped.txt", "x")
        with self.assertRaises(install.UnsafeArchive):
            install.unpack(self.d / "evil.zip", self.d / "out")
        make_tar(self.d / "evil.tar", {"/etc/escaped": (b"x", 0o644)}, mode="w")
        with self.assertRaises(install.UnsafeArchive):
            install.unpack(self.d / "evil.tar", self.d / "out2")
        self.assertFalse((self.d.parent / "escaped.txt").exists())

    def test_deb_contents_are_extracted_without_installing(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as t:
            info = tarfile.TarInfo("./opt/game/game")
            info.size, info.mode = len(ELF), 0o755
            t.addfile(info, io.BytesIO(ELF))
        body = data.getvalue()

        def member(name, payload):
            hdr = f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(payload):<10}`\n".encode()
            return hdr + payload + (b"\n" if len(payload) % 2 else b"")

        (self.d / "g.deb").write_bytes(b"!<arch>\n" + member("debian-binary", b"2.0\n")
                                       + member("control.tar.gz", b"") + member("data.tar.gz", body))
        out = install.unpack(self.d / "g.deb", self.d / "out", system="Linux")
        self.assertTrue((out / "opt/game/game").exists())

    def test_mojosetup_sh_is_unzipped(self):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("data/noarch/game/start.sh", "#!/bin/sh")
        (self.d / "gog_game.sh").write_bytes(b"#!/bin/sh\n# MojoSetup stub\nexit 0\n" + z.getvalue())
        out = install.unpack(self.d / "gog_game.sh", self.d / "out", system="Linux")
        self.assertTrue((out / "data/noarch/game/start.sh").exists())

    def test_windows_launcher_skips_installers_and_redists(self):
        root = self.d / "w"
        for n in ("unins000.exe", "vcredist_x64.exe", "Game/Galaxy Quest.exe", "Game/UnityCrashHandler64.exe"):
            (root / n).parent.mkdir(parents=True, exist_ok=True)
            (root / n).write_bytes(b"MZ")
        self.assertEqual(install.find_launcher(root, "Windows", "Galaxy Quest").name, "Galaxy Quest.exe")

    def test_mac_launcher_is_the_top_level_app(self):
        (self.d / "m/Game.app/Contents/Helpers/Crash.app").mkdir(parents=True)
        self.assertEqual(install.find_launcher(self.d / "m", "Darwin", "Game").name, "Game.app")

    def test_windows_installer_detection_and_silent_switches(self):
        for marker, kind in ((b"Inno Setup Setup Data", "inno"), (b"Nullsoft Install System", "nsis"),
                             (b"InstallAware", "installaware"), (b"nothing", "exe")):
            p = self.d / f"{kind}.exe"
            p.write_bytes(b"MZ" + b"\x00" * 100 + marker)
            self.assertEqual(install.installer_kind(p), kind)
        self.assertEqual(install.silent_command("s.exe", "nsis", r"C:\G")[-1], r"/D=C:\G")
        self.assertIn("/VERYSILENT", install.silent_command("s.exe", "inno", r"C:\G"))
        self.assertIsNone(install.silent_command("s.exe", "exe", r"C:\G"))
        self.assertIsNone(install.unpack(self.d / "inno.exe", self.d / "o"))


if __name__ == "__main__":
    unittest.main()
