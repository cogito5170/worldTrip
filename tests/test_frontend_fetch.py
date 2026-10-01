"""화면 받아 오기 -- 진짜 네트워크 없이, 가짜 tar.gz 로 받기 · 고르기 · 반쯤 받은 것 거절을 본다."""
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests._util  # noqa: F401
from worldtrip import frontend as F


def tarball(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            data = body.encode()
            ti = tarfile.TarInfo(f"gentleMonster-main/{name}")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class 받기(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        os.environ["XDG_CACHE_HOME"] = self.cache

    def fake(self, files):
        return mock.patch.object(F.urllib.request, "urlopen", lambda url, timeout=0: _Resp(tarball(files)))

    def test_그_폴더만_받는다(self):
        sub = F.SUBDIR
        files = {f"{sub}/index.html": "<h1>x</h1>", f"{sub}/app.js": "1", f"{sub}/app.css": "a{}",
                 "gentle_monster/engine/judge.py": "secret", f"{sub}/../../evil.txt": "no"}
        with self.fake(files), mock.patch.object(F, "_via_git", side_effect=RuntimeError("git 을 쓰면 안 된다")):
            d = F.fetch("main", log=lambda *_: None)
        self.assertTrue(F.complete(d))
        self.assertEqual(sorted(p.name for p in d.iterdir()), [".source.json", "app.css", "app.js", "index.html"])
        self.assertFalse(any(Path(self.cache).rglob("evil.txt")))
        self.assertFalse(any(Path(self.cache).rglob("judge.py")))

    def test_반쯤_받은_화면은_붙이지_않는다(self):
        files = {f"{F.SUBDIR}/index.html": "<h1>x</h1>"}            # app.js · app.css 가 없다
        with self.fake(files), mock.patch.object(F, "_via_git", side_effect=RuntimeError("git 도 실패")):
            with self.assertRaises(F.FetchError) as cm:
                F.fetch("main", log=lambda *_: None)
        self.assertIn("빠진 것", str(cm.exception))
        self.assertFalse((F.cache_root() / "main").exists())

    def test_둘_다_실패하면_까닭을_다_말한다(self):
        with mock.patch.object(F.urllib.request, "urlopen", side_effect=OSError("tar 막힘")), \
                mock.patch.object(F, "_via_git", side_effect=RuntimeError("git 막힘")):
            with self.assertRaises(F.FetchError) as cm:
                F.fetch("main", log=lambda *_: None)
        self.assertIn("tar 막힘", str(cm.exception))
        self.assertIn("git 막힘", str(cm.exception))

    def test_캐시가_있으면_다시_안_받는다(self):
        d = F.cache_root() / "main"
        d.mkdir(parents=True)
        for f in F.NEED:
            (d / f).write_text("x")
        with mock.patch.object(F.urllib.request, "urlopen", side_effect=AssertionError("받으면 안 된다")):
            self.assertEqual(F.fetch("main", log=lambda *_: None), d)


if __name__ == "__main__":
    unittest.main()
