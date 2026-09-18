#!/usr/bin/env python3
"""Create a reproducible, allow-listed local release source; never invoke Git/network."""
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile
import tomllib


def prepare(root):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    name = f"predator-sense-{version}"
    paths = [root / p for p in ("pyproject.toml", "README.md", "LICENSE")]
    paths += list((root / "src/predator_sense").rglob("*.py"))
    paths += list((root / "src/predator_sense/assets").glob("*.svg"))
    font_dir = root / "src/predator_sense/assets/fonts"
    if font_dir.is_dir():
        paths += list(font_dir.glob("*.ttf"))
        paths += list(font_dir.glob("*.txt"))
    paths += [p for p in (root / "packaging").iterdir() if p.is_file()]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(paths):
            data = path.read_bytes()
            member = tarfile.TarInfo(f"{name}/{path.relative_to(root)}")
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    data = gzip.compress(buffer.getvalue(), mtime=0)
    output = root / f"{name}.tar.gz"
    output.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    recipe = root / "PKGBUILD"
    text = re.sub(r"sha256sums=\('[^']*'\)", f"sha256sums=('{digest}')", recipe.read_text())
    recipe.write_text(text)
    print(f"{output.name}: {digest}")
    print("Regenerate metadata with makepkg --printsrcinfo > .SRCINFO")
    return output


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parent.parent)
