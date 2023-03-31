#!/usr/bin/env python3
from subprocess import Popen, PIPE
from pathlib import Path

packages = ["telegram", "requests_cache", "flask", "waitress", "pixivpy3", "saucerer"]
data_packages = ["cloudscraper"]
ext_blacklist = [".sqlite", ".json", ".pem"]

def run(args, *_args, **kwargs):
    print(">", args)
    proc = Popen(*_args, args=args, shell=True, stderr=PIPE, stdin=PIPE, stdout=PIPE, **kwargs)
    for line in proc.stdout:
        print(line.decode(), end="")
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("Return code is {}".format(proc.returncode))

def strip_all():
    dist = Path("./ayayaxyz.dist")
    for path in dist.rglob("*.*"):
        if path.suffix in ext_blacklist:
            continue
        run(f"strip -s -p -x -v -o '{str(path)}' '{str(path)}'")

def upx_all():
    try:
        run("upx -9 -v ./ayayaxyz.dist/*")
    except RuntimeError:
        pass

def main():
    run(f"nuitka3 --follow-imports --include-package={','.join(packages)} --include-package-data={','.join(data_packages)} --lto=yes --standalone ayayaxyz")

if __name__ == "__main__":
    main()
    strip_all()
    upx_all()
