"""Drive a Windows installer's wizard under Wine, typing the user's own serial.

The vendor's installer still does its own (online) serial validation; this only
does the clicking and typing a person would. It works by running AutoHotkey in
the same Wine prefix as the installer, so the same script works under
CrossOver, Kegworks, plain Wine and umu/Proton.

    python3 -I humble_integrator/drive.py script --profile installaware --serial XXXX-XXXX-XXXX-XXXX
    python3 -I humble_integrator/drive.py run --prefix DIR --wine wine64 --serial ... SETUP.EXE [ARGS...]
"""

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install  # noqa: E402

AHK_URL = "https://www.autohotkey.com/download/1.1/AutoHotkey_1.1.37.02.zip"
AHK_EXE = "AutoHotkeyU64.exe"


def ahk_string(s):
    """Quote a value for an AutoHotkey v1 expression string."""
    return '"' + str(s).replace("`", "``").replace('"', '""').replace("%", "`%") + '"'


def split_serial(serial):
    parts = [p for p in re.split(r"[\s-]+", serial.strip()) if p]
    return parts or [serial.strip()]


# AutoHotkey v1 script for InstallAware wizards (ProFantasy and many others).
# Delphi TLabels have no window handle, so pages are recognised by the windowed
# controls they have: product-key boxes are the edits that share one row.
INSTALLAWARE_AHK = r"""
#NoTrayIcon
#SingleInstance Force
#Persistent
SetTitleMatchMode, RegEx
SetControlDelay, 40
DetectHiddenWindows, Off

serialParts := StrSplit(%SERIAL_PARTS%, "|")
userName := %USER_NAME%
userCompany := %USER_COMPANY%
acceptEula := %ACCEPT_EULA%
logFile := %LOG_FILE%
titleRe := %TITLE_RE%
lastPage := ""
pageSince := A_TickCount
lastMsg := ""
seen := false
waited := 0
FileDelete, %logFile%
Log("driver started")

Loop {
    ; Message boxes (Windows' #32770 or Delphi's TMessageForm). The 7-Zip
    ; self-extractor's progress window is also #32770, so only read them;
    ; an error message ends the run.
    for i, cls in ["#32770", "TMessageForm"] {
        IfWinExist, ahk_class %cls%
        {
            WinGetText, msg, ahk_class %cls%
            msg := RegExReplace(msg, "\s+", " ")
            if (msg != lastMsg) {
                Log("MESSAGE: " . msg)
                lastMsg := msg
            }
            if RegExMatch(msg, "i)could not be located|not found|invalid|error|failed")
                Finish(2)
        }
    }
    hwnd := FindWizard()
    if (!hwnd) {
        Sleep, 500
        waited += 500
        if (Mod(waited, 60000) = 0)
            LogWindows()
        if (seen && waited > 120000) {
            Log("installer window gone")
            Finish(5)
        }
        if (!seen && waited > 1800000) {
            Log("installer never showed its wizard")
            Finish(6)
        }
        continue
    }
    seen := true
    waited := 0
    page := Describe(hwnd)
    if (page != lastPage) {
        Log("PAGE: " . page)
        lastPage := page
        pageSince := A_TickCount
    } else if (A_TickCount - pageSince > 600000 && !InStr(ControlsOf(hwnd), "Progress")) {
        ; Nothing on this page lets us go on (and it isn't installing): say so instead of waiting.
        Log("no way past this page after 10 minutes")
        Finish(7)
    }
    if RegExMatch(page, "i)\b(remove|uninstall|repair)\b") {
        Log("maintenance page (already installed?): not touching it")
        Finish(3)
    }
    if (HandleLicense(hwnd) = "stop")
        Finish(4)
    FillUserInfo(hwnd)
    UntickLaunch(hwnd)
    if (ClickButton(hwnd, "i)^&?Finish$|^&?Close$")) {
        Log("finished")
        Finish(0)
    }
    ClickButton(hwnd, "i)^&?Next|^&?Install$|^&?Continue$")
    Sleep, 700
}

FindWizard() {
    ; InstallAware's wizard is a TPoolTemplate window titled
    ; "<Product> - InstallAware Wizard" (older builds: "<Product> Setup").
    ; Its verify/progress boxes share the title but are #32770: skip those.
    global titleRe
    WinGet, hwnd, ID, ahk_class TPoolTemplate
    if (hwnd)
        return hwnd
    WinGet, ids, List, %titleRe%
    Loop, %ids%
    {
        id := ids%A_Index%
        WinGetClass, c, ahk_id %id%
        if (c != "#32770" && c != "TMessageForm")
            return id
    }
    return 0
}

Describe(hwnd) {
    WinGet, ctrls, ControlList, ahk_id %hwnd%
    out := ""
    Loop, Parse, ctrls, `n
    {
        ControlGet, vis, Visible,, %A_LoopField%, ahk_id %hwnd%
        if (!vis)
            continue
        ControlGetText, t, %A_LoopField%, ahk_id %hwnd%
        if (t != "" && !InStr(A_LoopField, "Edit"))
            out .= A_LoopField . "=" . RegExReplace(t, "\s+", " ") . "; "
    }
    return out
}

ClickButton(hwnd, re) {
    WinGet, ctrls, ControlList, ahk_id %hwnd%
    Loop, Parse, ctrls, `n
    {
        if !InStr(A_LoopField, "Button")
            continue
        ControlGet, vis, Visible,, %A_LoopField%, ahk_id %hwnd%
        ControlGet, en, Enabled,, %A_LoopField%, ahk_id %hwnd%
        ControlGetText, t, %A_LoopField%, ahk_id %hwnd%
        if (vis && en && RegExMatch(t, re)) {
            ControlClick, %A_LoopField%, ahk_id %hwnd%
            Log("clicked " . t)
            return true
        }
    }
    return false
}

ControlsOf(hwnd) {
    WinGet, ctrls, ControlList, ahk_id %hwnd%
    return ctrls
}

HandleLicense(hwnd) {
    ; "I accept" is a pair of radio buttons, or in some InstallAware themes one checkbox.
    global acceptEula
    WinGet, ctrls, ControlList, ahk_id %hwnd%
    Loop, Parse, ctrls, `n
    {
        if !RegExMatch(A_LoopField, "Radio|Check")
            continue
        ControlGetText, t, %A_LoopField%, ahk_id %hwnd%
        if (RegExMatch(t, "i)accept") && !RegExMatch(t, "i)not|don't|do not")) {
            ControlGet, checked, Checked,, %A_LoopField%, ahk_id %hwnd%
            if (checked)
                return "ok"
            if (!acceptEula) {
                Log("license page: EULA acceptance not pre-approved, leaving it to the user")
                return "stop"
            }
            Control, Check,, %A_LoopField%, ahk_id %hwnd%
            Log("accepted license: " . t)
            return "ok"
        }
    }
    return "none"
}

FillUserInfo(hwnd) {
    global serialParts, userName, userCompany
    WinGet, ctrls, ControlList, ahk_id %hwnd%
    edits := []
    Loop, Parse, ctrls, `n
    {
        if !InStr(A_LoopField, "Edit")
            continue
        ControlGet, vis, Visible,, %A_LoopField%, ahk_id %hwnd%
        if (!vis)
            continue
        ControlGetPos, x, y,,, %A_LoopField%, ahk_id %hwnd%
        edits.Push({c: A_LoopField, x: x, y: y})
    }
    if (edits.Length() = 0)
        return
    rows := {}
    for i, e in edits {
        key := Round(e.y / 6)
        if !rows.HasKey(key)
            rows[key] := []
        rows[key].Push(e)
    }
    keyRow := ""
    singles := []
    for k, r in rows {
        if (r.Length() >= 2)
            keyRow := r
        else
            singles.Push(r[1])
    }
    if (keyRow = "")
        return  ; not a product-key page (e.g. target folder)
    ; Sort the key boxes left to right and the single edits top to bottom.
    keyRow := SortBy(keyRow, "x"), singles := SortBy(singles, "y")
    if (singles.Length() >= 1)
        SetIfEmpty(hwnd, singles[1].c, userName)
    if (singles.Length() >= 2)
        SetIfEmpty(hwnd, singles[2].c, userCompany)
    if (serialParts.Length() = keyRow.Length()) {
        for i, e in keyRow
            ControlSetText, % e.c, % serialParts[i], ahk_id %hwnd%
    } else {
        ; Different grouping than the boxes: let the first box receive it all
        ; (many key dialogs split pasted keys themselves).
        joined := ""
        for i, p in serialParts
            joined .= p
        ControlSetText, % keyRow[1].c, %joined%, ahk_id %hwnd%
        Log("serial has " . serialParts.Length() . " groups but page has " . keyRow.Length() . " boxes")
    }
    Log("filled product key (" . keyRow.Length() . " boxes)")
}

SetIfEmpty(hwnd, ctrl, value) {
    ControlGetText, cur, %ctrl%, ahk_id %hwnd%
    if (cur = "" && value != "")
        ControlSetText, %ctrl%, %value%, ahk_id %hwnd%
}

UntickLaunch(hwnd) {
    WinGet, ctrls, ControlList, ahk_id %hwnd%
    Loop, Parse, ctrls, `n
    {
        if !InStr(A_LoopField, "Check")
            continue
        ControlGetText, t, %A_LoopField%, ahk_id %hwnd%
        if RegExMatch(t, "i)launch|run|start|readme|view") {
            ControlGet, checked, Checked,, %A_LoopField%, ahk_id %hwnd%
            if (checked) {
                Control, Uncheck,, %A_LoopField%, ahk_id %hwnd%
                Log("unticked: " . t)
            }
        }
    }
}

SortBy(arr, field) {
    n := arr.Length()
    Loop, % n - 1
    {
        Loop, % n - A_Index
        {
            j := A_Index
            if (arr[j][field] > arr[j + 1][field])
                tmp := arr[j], arr[j] := arr[j + 1], arr[j + 1] := tmp
        }
    }
    return arr
}

LogWindows() {
    WinGet, ids, List
    Loop, %ids%
    {
        id := ids%A_Index%
        WinGetTitle, t, ahk_id %id%
        WinGetClass, c, ahk_id %id%
        Log("  window [" . c . "] " . t)
    }
}

Log(msg) {
    global logFile
    FormatTime, ts,, HH:mm:ss
    FileAppend, %ts% %msg%`n, %logFile%
}

Finish(code) {
    Log("exit " . code)
    ExitApp, %code%
}
"""


def installaware_script(serial, *, name="", company="", accept_eula=False,
                        log_file=r"C:\users\Public\hi-driver.log", title_re=r"(Setup|InstallAware Wizard)$"):
    values = {
        "SERIAL_PARTS": ahk_string("|".join(split_serial(serial))),
        "USER_NAME": ahk_string(name),
        "USER_COMPANY": ahk_string(company),
        "ACCEPT_EULA": "1" if accept_eula else "0",
        "LOG_FILE": ahk_string(log_file),
        "TITLE_RE": ahk_string(title_re),
    }
    script = INSTALLAWARE_AHK
    for k, v in values.items():
        script = script.replace(f"%{k}%", v)
    return script.lstrip()


PROFILES = {"installaware": installaware_script}


# ---------------------------------------------------------------- running

def fetch_ahk(cache_dir):
    exe = Path(cache_dir) / "ahk" / AHK_EXE
    if exe.exists():
        return exe
    exe.parent.mkdir(parents=True, exist_ok=True)
    zpath = exe.parent / "ahk.zip"
    urllib.request.urlretrieve(AHK_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extract(AHK_EXE, exe.parent)
    return exe


def run(prefix, wine, installer, args, script, cache_dir, timeout=3 * 3600):
    """Run INSTALLER in PREFIX with the AutoHotkey driver alongside it. Returns the driver's exit code."""
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG="-all", **install.wine_env(wine))
    ahk = fetch_ahk(cache_dir)
    public = Path(prefix) / "drive_c" / "users" / "Public"
    public.mkdir(parents=True, exist_ok=True)
    script_path = public / "hi-driver.ahk"
    script_path.write_text(script, encoding="utf-8-sig")  # AHK v1 wants a BOM for UTF-8
    log_path = public / "hi-driver.log"
    driver = subprocess.Popen([wine, str(ahk), str(script_path)], env=env)
    setup = None
    try:
        time.sleep(2)
        setup = subprocess.Popen([wine, str(installer), *args], env=env)
        # The driver decides when the wizard is done (finish, error, or window gone);
        # self-extracting installers exit before their wizard does.
        try:
            return driver.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return 1
    finally:
        for proc in (driver, setup):
            if proc and proc.poll() is None:
                proc.terminate()
        # The wizard itself is a child the launcher left behind (self-extractors do that):
        # end everything still running in this prefix.
        server = Path(wine).with_name("wineserver")
        server = str(server) if server.exists() else shutil.which("wineserver")
        if server:
            subprocess.run([server, "-k"], env=env)
        if log_path.exists():
            sys.stderr.write(log_path.read_text(errors="replace"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("script", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--profile", choices=sorted(PROFILES), default="installaware")
        sp.add_argument("--serial", default="")
        sp.add_argument("--name", default="")
        sp.add_argument("--company", default="")
        sp.add_argument("--accept-eula", action="store_true",
                        help="tick the installer's 'I accept' (only if you have read and accept the vendor's EULA)")
    r = sub.choices["run"]
    r.add_argument("--prefix", required=True)
    r.add_argument("--wine", default=shutil.which("wine") or "wine")
    r.add_argument("--cache", default=str(Path.home() / ".cache" / "humble-integrator"))
    r.add_argument("installer")
    r.add_argument("args", nargs="*")
    a = p.parse_args(argv)
    script = PROFILES[a.profile](a.serial, name=a.name, company=a.company, accept_eula=a.accept_eula)
    if a.cmd == "script":
        print(script)
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # still clean up when stopped
    sys.exit(run(a.prefix, a.wine, a.installer, a.args, script, a.cache))


if __name__ == "__main__":
    main()
