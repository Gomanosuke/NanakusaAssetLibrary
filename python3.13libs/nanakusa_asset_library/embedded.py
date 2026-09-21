"""Preview images stored inside a .usdz package (independent of Qt; pxr is optional).

Two conventions are read:
- the USD standard, UsdMediaAssetPreviewsAPI (default thumbnail of the default prim or the layer);
- common file names: thumbnail / preview at the package root, or any image in a
  Thumbnails / .thumbs folder. Texture images inside the package are never mistaken for previews.
The package itself is only read, never modified.
"""
from pathlib import Path
import os
import posixpath
import re
import zipfile

IMAGES = ('.png', '.jpg', '.jpeg')
MAX_BYTES = 32 * 1024 * 1024
_ROOT_NAMES = re.compile(r'^(thumbnail|preview)\.(png|jpe?g)$')
_FOLDERS = ('thumbnails', '.thumbs', 'thumbs')


def _standard_entry(source, names):
    """Entry named by UsdMediaAssetPreviewsAPI, if pxr can read the package."""
    try:
        from pxr import Usd, UsdMedia
    except ImportError:
        return None
    try:
        stage = Usd.Stage.Open(str(source), Usd.Stage.LoadNone)
        if stage is None:
            return None
        prim = stage.GetDefaultPrim()
        api = UsdMedia.AssetPreviewsAPI(prim) if prim else None
        thumbs = api.GetDefaultThumbnails() if api else None
        image = thumbs.defaultImage if thumbs is not None else None
        if not image:
            thumbs = UsdMedia.AssetPreviewsAPI.GetAssetDefaultPreviews(stage.GetRootLayer())
            image = thumbs.defaultImage if thumbs is not None else None
        path = image.path if image else ''
    except Exception:
        return None
    entry = posixpath.normpath(path.replace(chr(92), '/')) if path else ''
    if not entry or entry.startswith(('..', '/')):
        return None
    lookup = {n.lower(): n for n in names}
    found = lookup.get(entry.lower())
    return found if found and found.lower().endswith(IMAGES) else None


def _conventional_entry(names, sizes):
    images = [n for n in names if n.lower().endswith(IMAGES) and not n.endswith('/')]
    for name in images:
        if _ROOT_NAMES.match(name.lower()):
            return name
    inside = [n for n in images if any(part.lower() in _FOLDERS for part in n.split('/')[:-1])]
    if inside:
        named = [n for n in inside if 'thumbnail' in posixpath.basename(n).lower()]
        return max(named or inside, key=lambda n: sizes[n])
    return None


def thumbnail(source):
    """(extension, bytes) of the preview image inside a .usdz, or None."""
    source = Path(source)
    if source.suffix.lower() != '.usdz' or not source.is_file():
        return None
    try:
        with zipfile.ZipFile(source) as package:
            infos = {i.filename: i for i in package.infolist() if not i.is_dir()}
            sizes = {n: i.file_size for n, i in infos.items()}
            entry = _standard_entry(source, list(infos)) or _conventional_entry(list(infos), sizes)
            if entry is None or sizes[entry] == 0 or sizes[entry] > MAX_BYTES:
                return None
            return Path(entry).suffix.lower(), package.read(entry)
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return None


def extract(source, destination_stem):
    """Write the preview next to destination_stem (no extension); returns the file or None."""
    found = thumbnail(source)
    if found is None:
        return None
    suffix, data = found
    target = Path(str(destination_stem) + suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.pending')
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return target
