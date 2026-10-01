"""프론트엔드 받아 오기 -- 화면은 gentleMonster 저장소에 있다(`gentle_monster/apps/worldtrip/`).

이 저장소에 화면의 복사본을 두지 않는다. 두 벌이 생기면 어느 쪽이 진짜인지 모르게 된다.
`worldtrip app` 이 처음 뜰 때 받아서 캐시에 두고, `--update` 면 다시 받는다.

받는 길은 둘이다. 먼저 GitHub 의 tar.gz(표준 라이브러리만), 안 되면 git sparse clone.
받은 뒤에는 **index.html · app.js · app.css 가 다 있는지 확인**하고, 아니면 실패로 낸다 -- 반쯤 받은 화면을
'붙었다' 고 하지 않는다.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = os.environ.get("WORLDTRIP_FRONTEND_REPO", "cogito5170/gentleMonster")
SUBDIR = "gentle_monster/apps/worldtrip"
NEED = ("index.html", "app.js", "app.css")


class FetchError(RuntimeError):
    pass


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "worldtrip" / "frontend"


def complete(d: Path) -> bool:
    return all((d / f).is_file() for f in NEED)


def _via_tarball(ref: str, dest: Path) -> str:
    url = f"https://codeload.github.com/{REPO}/tar.gz/{ref}"
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1].startswith(SUBDIR + "/") or not m.isfile():
                continue
            rel = parts[1][len(SUBDIR) + 1:]
            if ".." in Path(rel).parts:
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tf.extractfile(m).read())
            n += 1
    if not n:
        raise FetchError(f"{url} 안에 {SUBDIR} 가 없다")
    return url


def _via_git(ref: str, dest: Path) -> str:
    url = f"https://github.com/{REPO}"
    with tempfile.TemporaryDirectory() as tmp:
        run = lambda *a: subprocess.run(a, cwd=tmp, check=True, capture_output=True, text=True, timeout=300)
        run("git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", ref, url, "r")
        subprocess.run(["git", "-C", f"{tmp}/r", "sparse-checkout", "set", SUBDIR], check=True, capture_output=True, timeout=300)
        sha = subprocess.run(["git", "-C", f"{tmp}/r", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        src = Path(tmp) / "r" / SUBDIR
        if not src.is_dir():
            raise FetchError(f"{url}@{ref} 에 {SUBDIR} 가 없다")
        shutil.copytree(src, dest, dirs_exist_ok=True)
    return f"{url}@{sha}"


def fetch(ref: str = "main", update: bool = False, log=print) -> Path:
    dest = cache_root() / ref.replace("/", "_")
    if complete(dest) and not update:
        return dest
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    errors = []
    for name in ("_via_tarball", "_via_git"):
        try:
            src = globals()[name](ref, tmp)
            break
        except Exception as e:  # noqa: BLE001 -- 다음 길을 시도하고, 모두 실패하면 까닭을 다 말한다
            errors.append(f"{name.strip('_')}: {type(e).__name__}: {e}")
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
        raise FetchError("프론트엔드를 못 받았다 -- " + " / ".join(errors))
    if not complete(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
        raise FetchError(f"받았지만 {NEED} 중 빠진 것이 있다 -- 반쯤 받은 화면은 붙이지 않는다")
    (tmp / ".source.json").write_text(json.dumps({"from": src, "ref": ref, "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    log(f"[worldtrip] 화면을 받았다: {src} -> {dest}")
    return dest
