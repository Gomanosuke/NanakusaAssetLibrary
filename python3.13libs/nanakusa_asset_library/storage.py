"""Portable data and adjacent thumbnail locations (no Houdini dependency)."""
from pathlib import Path
import os
from .core import is_usd_package


def default_data_dir():
    explicit = os.environ.get('NAL_DATA_DIR')
    if explicit:
        return Path(explicit).expanduser()
    root = os.environ.get('NAL_ASSET_ROOT')
    if root:
        return Path(root).expanduser().parent / 'data'
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share') / 'NanakusaAssetLibrary' / 'data'


def thumbnail_destination(row, data_dir):
    source = Path(row['root_path']) / row['relpath']
    if row['kind'] == 'usd':
        return source.parent / 'thumbnail.png' if is_usd_package(row) else source.with_name(source.stem+'_thumbnail.png')
    if row['kind'] == 'model':
        name = source.stem
        if source.name.lower().endswith(('.bgeo.sc', '.geo.sc')):
            name = source.with_suffix('').stem
        return source.with_name(name + '_thumbnail.png')
    return Path(data_dir) / 'thumbnails' / (
        'square_v2_' + row['id'] + '_' + str(int(row['mtime']*1000000)) + '_' + str(row['size']) + '.png')


def embedded_cache_stem(row, data_dir):
    """Where a preview image taken out of a .usdz is cached (never written next to the asset)."""
    return Path(data_dir) / 'thumbnails' / (
        'usdz_' + row['id'] + '_' + str(int(row['mtime']*1000000)) + '_' + str(row['size']))


def is_usdz(row):
    return row['kind'] == 'usd' and row['relpath'].lower().endswith('.usdz')


def thumbnail_candidates(row, data_dir):
    source = Path(row['root_path']) / row['relpath']
    result = [Path(row['thumbnail'])] if row.get('thumbnail') else []
    result.append(thumbnail_destination(row, data_dir))
    if row['kind'] == 'usd':
        result.append(thumbnail_destination(row, data_dir).with_suffix('.jpg'))
    elif row['kind'] == 'model':
        result.append(thumbnail_destination(row, data_dir).with_suffix('.jpg'))
    result.append(source.with_suffix('.preview.jpg'))
    if row['kind'] == 'texture' and source.suffix.lower() in {'.png','.jpg','.jpeg','.bmp','.tga'}:
        result.append(source)
    if is_usdz(row):
        # Lowest priority: a generated or chosen thumbnail always wins over the package's own preview.
        stem = embedded_cache_stem(row, data_dir)
        result.extend(stem.with_name(stem.name + ext) for ext in ('.png', '.jpg', '.jpeg'))
    return result
