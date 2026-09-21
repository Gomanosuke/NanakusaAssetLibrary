"""Move assets and folders inside one library root without losing metadata.

Independent of Houdini and Qt. Files are renamed and never overwritten. The
index keeps each asset's id, so tags, favorites and notes follow the move.
If any step fails, completed renames are reverted and the index is unchanged.
"""
from pathlib import Path
import os
import shutil
from . import core, storage

GENRE_OF_KIND = {'usd': 'USD', 'model': '3DModel', 'texture': 'Texture'}


class MoveError(ValueError):
    pass


def _parts(rel):
    return [p for p in rel.split('/') if p]


def _key(path):
    return os.path.normcase(str(path))


def check_destination(root_path, dest_rel):
    """Return the destination folder if assets may be placed in it."""
    parts = _parts(dest_rel)
    if not parts or parts[0] not in core.GENRES:
        raise MoveError('Choose a folder under USD, Texture or 3DModel.')
    folder = core.inside(root_path, '/'.join(parts))
    if not folder.is_dir():
        raise MoveError('Folder not found: ' + '/'.join(parts))
    if parts[0] == 'USD':
        for i in range(2, len(parts) + 1):
            if core.package_entry(core.inside(root_path, '/'.join(parts[:i]))):
                raise MoveError('A USD package cannot contain other assets.')
    return folder


def _sidecars(row, data_dir, indexed):
    """Adjacent thumbnail files that belong to a single-file asset."""
    if core.is_usd_package(row):
        return []  # The package folder carries its own thumbnail.
    source = Path(row['root_path']) / row['relpath']
    found = []
    for candidate in storage.thumbnail_candidates(dict(row, thumbnail=''), data_dir):
        if (candidate != source and candidate.parent == source.parent and candidate.is_file()
                and _key(candidate) not in indexed and candidate not in found):
            found.append(candidate)
    return found


def _remap(thumbnail, pairs):
    """Follow a user-chosen thumbnail path when the file it names was moved."""
    if not thumbnail:
        return thumbnail
    # Compare resolved paths: a chosen thumbnail may spell the same folder differently
    # (Windows short names, drive-letter case). Call this before the files are renamed.
    chosen = Path(thumbnail).resolve()
    for old, new in pairs:
        old = old.resolve()
        if chosen == old:
            return str(new)
        try:
            return str(new / chosen.relative_to(old))
        except ValueError:
            pass
    return thumbnail


def _apply(library, root_id, ops, changes):
    done = []
    try:
        for op in ops:
            try:
                if op['mode'] == 'move':
                    op['src'].rename(op['dst'])
                else:
                    shutil.copy2(op['src'], op['dst'])
            except OSError as exc:
                raise MoveError('Could not move "%s": %s. Close programs that use it and try again.'
                                % (op['src'].name, exc)) from exc
            done.append(op)
        library.relocate(root_id, changes)
    except BaseException:
        stuck = []
        for op in reversed(done):
            try:
                if op['mode'] == 'move':
                    op['dst'].rename(op['src'])
                else:
                    op['dst'].unlink()
            except OSError:
                stuck.append(str(op['dst']))
        if stuck:
            raise MoveError('Move failed and could not be fully reverted. Check: ' + '; '.join(stuck))
        raise


def check_assets_target(rows, root_id, dest_rel):
    """Validate dropping assets on a folder; returns the destination folder."""
    if not rows:
        raise MoveError('Select assets to move.')
    if any(r['root_id'] != root_id for r in rows):
        raise MoveError('Assets cannot be moved to a different library.')
    dest_rel = '/'.join(_parts(dest_rel))
    dest = check_destination(rows[0]['root_path'], dest_rel)
    genre = dest_rel.split('/')[0]
    for row in rows:
        if GENRE_OF_KIND.get(row['kind']) != genre:
            raise MoveError('%s assets can only be moved under %s.' % (row['kind'], GENRE_OF_KIND.get(row['kind'], '?')))
    return dest


def check_folder_target(root_path, src_rel, dest_rel):
    """Validate dropping a folder on another folder; returns (source, destination)."""
    sparts, dparts = _parts(src_rel), _parts(dest_rel)
    src_rel, dest_rel = '/'.join(sparts), '/'.join(dparts)
    if len(sparts) < 2:
        raise MoveError('USD, Texture and 3DModel are fixed categories and cannot be moved.')
    if not dparts or dparts[0] != sparts[0]:
        raise MoveError('Folders can only be moved within ' + sparts[0] + '.')
    if dest_rel.lower() == src_rel.lower() or dest_rel.lower().startswith(src_rel.lower() + '/'):
        raise MoveError('A folder cannot be moved into itself.')
    if '/'.join(sparts[:-1]).lower() == dest_rel.lower():
        raise MoveError('The folder is already in that location.')
    dest = check_destination(root_path, dest_rel)
    src = core.inside(root_path, src_rel)
    if not src.is_dir():
        raise MoveError('Folder not found: ' + src_rel)
    return src, dest


def move_assets(library, rows, dest_rel, data_dir):
    """Move indexed assets, with their thumbnails, into a folder of the same library."""
    if not rows:
        raise MoveError('Select assets to move.')
    root_ids = {r['root_id'] for r in rows}
    if len(root_ids) > 1:
        raise MoveError('Assets from different libraries cannot be moved together.')
    root_id = root_ids.pop()
    dest_rel = '/'.join(_parts(dest_rel))
    everything = library.assets(root_id=root_id, missing=True)
    fresh = {r['id']: r for r in everything}
    if any(r['id'] not in fresh for r in rows):
        raise MoveError('An asset is no longer in the index. Rescan and try again.')
    rows = [fresh[r['id']] for r in rows]
    root_path = rows[0]['root_path']
    dest = check_assets_target(rows, root_id, dest_rel)
    indexed = {_key(Path(r['root_path']) / r['relpath']) for r in everything}
    moving_ids = {r['id'] for r in rows}
    folders = {Path(r['relpath']).parent.as_posix() for r in rows}
    neighbours = [r for r in everything if Path(r['relpath']).parent.as_posix() in folders]
    owners = {}
    for r in neighbours:
        for sidecar in _sidecars(r, data_dir, indexed):
            owners.setdefault(_key(sidecar), set()).add(r['id'])
    ops, changes, catalog, claimed = [], [], [], set()

    def claim(dst):
        if dst.exists() or _key(dst) in claimed:
            raise MoveError('"%s" already exists in %s.' % (dst.name, dest_rel))
        claimed.add(_key(dst))

    for row in rows:
        source = Path(root_path) / row['relpath']
        package = core.is_usd_package(row)
        current = Path(row['relpath']).parent.parent if package else Path(row['relpath']).parent
        if current.as_posix().lower() == dest_rel.lower():
            continue
        pairs = []
        if package:
            folder = source.parent
            claim(dest / folder.name)
            ops.append({'src': folder, 'dst': dest / folder.name, 'mode': 'move'})
            pairs.append((folder, dest / folder.name))
            new_rel = dest_rel + '/' + folder.name + '/' + source.name
            new_source = dest / folder.name / source.name
        else:
            claim(dest / source.name)
            ops.append({'src': source, 'dst': dest / source.name, 'mode': 'move'})
            new_rel = dest_rel + '/' + source.name
            new_source = dest / source.name
            for sidecar in _sidecars(row, data_dir, indexed):
                target = dest / sidecar.name
                if _key(sidecar) not in {_key(op['src']) for op in ops}:
                    claim(target)
                    shared = owners[_key(sidecar)] - moving_ids
                    ops.append({'src': sidecar, 'dst': target, 'mode': 'copy' if shared else 'move'})
                pairs.append((sidecar, target))
        changes.append({'id': row['id'], 'relpath': new_rel, 'thumbnail': _remap(row['thumbnail'], pairs)})
        if row['kind'] == 'usd':
            catalog.append((source, new_source))
    if not ops:
        raise MoveError('The assets are already in that folder.')
    backup = library.backup_index()
    _apply(library, root_id, ops, changes)
    return {'moved': len(changes), 'backup': backup, 'catalog': catalog}


def move_folders(library, root_id, src_rels, dest_rel, data_dir):
    """Move folders (and every asset in them) under another folder of the same category.

    All folders move together or not at all. Folders already in the destination and
    folders nested inside another selected folder (they travel with it) are skipped.
    """
    dest_rel = '/'.join(_parts(dest_rel))
    root = next((r for r in library.roots() if r['id'] == root_id), None)
    if root is None:
        raise MoveError('Library is not registered.')
    root_path = root['path']
    rels = list(dict.fromkeys('/'.join(_parts(r)) for r in src_rels))
    if not rels:
        raise MoveError('Select folders to move.')
    lowered = [r.lower() for r in rels]
    rels = [r for r in rels if not any(r.lower().startswith(o + '/') for o in lowered)]
    ops, changes, catalog, claimed, new_rels = [], [], [], set(), []
    everything = library.assets(root_id=root_id, missing=True)
    for src_rel in rels:
        if '/'.join(_parts(src_rel)[:-1]).lower() == dest_rel.lower():
            continue
        src, dest = check_folder_target(root_path, src_rel, dest_rel)
        dst = dest / src.name
        if dst.exists() or _key(dst) in claimed:
            raise MoveError('"%s" already exists in %s.' % (src.name, dest_rel))
        claimed.add(_key(dst))
        ops.append({'src': src, 'dst': dst, 'mode': 'move'})
        new_rels.append(dest_rel + '/' + src.name)
        for row in everything:
            if row['relpath'].startswith(src_rel + '/'):
                tail = row['relpath'][len(src_rel) + 1:]
                changes.append({'id': row['id'], 'relpath': new_rels[-1] + '/' + tail,
                                'thumbnail': _remap(row['thumbnail'], [(src, dst)])})
                if row['kind'] == 'usd':
                    catalog.append((Path(root_path) / row['relpath'], dst / tail))
    if not ops:
        raise MoveError('The folders are already in that location.')
    backup = library.backup_index()
    _apply(library, root_id, ops, changes)
    return {'moved': len(changes), 'folders': len(ops), 'backup': backup, 'new_rels': new_rels,
            'new_rel': new_rels[0], 'catalog': catalog}


def move_folder(library, root_id, src_rel, dest_rel, data_dir):
    return move_folders(library, root_id, [src_rel], dest_rel, data_dir)
