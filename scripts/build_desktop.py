"""Build a local frameless WebView2 shell; no system installation or global settings."""
import hashlib
import argparse
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.4258.31"
SHA = "56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9"


def build(output=None):
    package = ROOT / "runtime/desktop/webview2" / (VERSION + ".nupkg")
    package.parent.mkdir(parents=True, exist_ok=True)
    if not package.exists():
        urllib.request.urlretrieve("https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/"
                                   + VERSION + "/microsoft.web.webview2." + VERSION + ".nupkg", package)
    if hashlib.sha256(package.read_bytes()).hexdigest() != SHA:
        raise ValueError("WebView2 SDK package checksum mismatch")
    output = (output or ROOT / "runtime/desktop/bin").resolve()
    output.relative_to(ROOT)
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package) as archive:
        for name in ["lib/net462/Microsoft.Web.WebView2.Core.dll",
                     "lib/net462/Microsoft.Web.WebView2.WinForms.dll",
                     "runtimes/win-x64/native/WebView2Loader.dll"]:
            (output / Path(name).name).write_bytes(archive.read(name))
    compiler = Path(__import__("os").environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
                    "/win32manifest:" + str(ROOT / "native/desktop/app.manifest"),
                    "/out:" + str(output / "MusicLearning.exe"),
                    "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Web.Extensions.dll",
                    *["/r:" + str(output / name) for name in
                      ["Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll"]],
                    str(ROOT / "native/desktop/Main.cs")], check=True)
    shutil.copyfile(ROOT / "native/desktop/App.config", output / "MusicLearning.exe.config")
    print(output / "MusicLearning.exe")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', type=Path)
    build(parser.parse_args().output_directory)
