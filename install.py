"""Install a package pointer; never copy assets into the source tree.

Run with Houdini's hython or regular Python:
    python install.py --prefs <houdini22.0-preferences> --library <asset-root>
Close/save Houdini before changing an existing installation.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

def install(prefs, library=None):
    source=Path(__file__).resolve().parent
    prefs=Path(prefs).expanduser().resolve()
    if library:
        root=Path(library).expanduser().resolve()
        if root==source or root.is_relative_to(source) or source.is_relative_to(root):
            raise ValueError('Source code and asset library must be independent folders')
        root.mkdir(parents=True,exist_ok=True)
    else:root=None
    data=prefs/'nanakusa_asset_library_data'
    data.mkdir(parents=True,exist_ok=True)
    packages=prefs/'packages'; packages.mkdir(parents=True,exist_ok=True)
    package=packages/'nanakusa_asset_library.json'
    settings=data/'settings.json'
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    for path in (package,settings):
        if path.exists():
            backup=data/'backups'/stamp/path.name
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,backup)
            if backup.read_bytes()!=path.read_bytes():raise RuntimeError('Backup verification failed')
    package.write_text(json.dumps({'path':source.as_posix()},indent=2),encoding='utf-8')
    config=json.loads(settings.read_text(encoding='utf-8-sig')) if settings.exists() else {}
    if root:
        for genre in ('USD','Texture','3DModel'):
            (root/genre).mkdir(exist_ok=True)
        config['publish_root']=root.as_posix()
        settings.write_text(json.dumps(config,indent=2,ensure_ascii=False),encoding='utf-8')
        sys.path.insert(0,str(source/'python3.13libs'))
        from nanakusa_asset_library.core import Library
        index=Library(data)
        if index.roots():index.backup_index()
        index.add_root(root)
    print('Installed package:',package)
    print('Source:',source)
    print('User settings/index:',data)
    print('Restart Houdini, then open NanakusaAssetLibrary from the Python Panel menu.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefs',required=True)
    parser.add_argument('--library')
    args=parser.parse_args()
    install(args.prefs,args.library)
