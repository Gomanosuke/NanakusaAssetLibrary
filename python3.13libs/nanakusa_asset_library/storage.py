"""Portable data and adjacent thumbnail locations (no Houdini dependency)."""
from pathlib import Path
import os


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
        return source.parent / 'thumbnail.png'
    if row['kind'] == 'model':
        name = source.stem
        if source.name.lower().endswith(('.bgeo.sc', '.geo.sc')):
            name = source.with_suffix('').stem
        return source.with_name(name + '_thumbnail.png')
    return Path(data_dir) / 'thumbnails' / (
        'square_v2_' + row['id'] + '_' + str(int(row['mtime']*1000000)) + '_' + str(row['size']) + '.png')


def thumbnail_candidates(row, data_dir):
    source = Path(row['root_path']) / row['relpath']
    result = [Path(row['thumbnail'])] if row.get('thumbnail') else []
    result.append(thumbnail_destination(row, data_dir))
    if row['kind'] == 'usd':
        result.append(source.parent / 'thumbnail.jpg')
    elif row['kind'] == 'model':
        result.append(thumbnail_destination(row, data_dir).with_suffix('.jpg'))
    result.append(source.with_suffix('.preview.jpg'))
    if row['kind'] == 'texture' and source.suffix.lower() in {'.png','.jpg','.jpeg','.bmp','.tga'}:
        result.append(source)
    return result
