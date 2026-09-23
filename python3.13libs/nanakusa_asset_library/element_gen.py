"""Standalone hython worker: add an "element" variant set to a multi-object USD pack.

Some source packs (typically Sketchfab downloads) bundle several unrelated top-level objects in
one file - e.g. a handful of alternate mushroom models, all sharing the same origin - rather than
one assembled object made of several parts. Placed as-is, every one of them shows at once,
overlapping. This adds a variant set that isolates exactly one at a time, defaulting to the first,
so a downstream Set Variant LOP or Stage Manager's Inspector can pick another. The set lives on the
asset's top prim (see _anchor), not on the branch prim it switches, so any placement carries it on
the placed prim itself.

Only run this on an asset you have actually looked at and confirmed is that kind of pack: a
modular kit (e.g. railing segments meant to all be visible together) matches the same "several
top-level objects with geometry" shape and would be broken by this the same way - there is no
way to tell the two apart from the file alone, so this is deliberately a manual, per-asset action
in ui.py (no "generate missing" bulk sweep).

Shares its file-handling (plain USD vs .usdz extract/repackage, edit only the asset's own entry
layer, write to a new file and never Save() the source) with proxy_gen.py; see generate_plain and
generate_usdz there.
"""
from pathlib import Path
import sys

if __package__ in (None, ''):
    # Run directly by hython as a worker subprocess (see _MeshGenerateJob in ui.py), this file has
    # no parent package, so the relative import below would fail with "attempted relative import
    # with no known parent package". Make python3.13libs importable and import proxy_gen from
    # there instead; this branch is a no-op when element_gen is imported normally as part of the
    # nanakusa_asset_library package (tests, ui.py).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nanakusa_asset_library import proxy_gen
else:
    from . import proxy_gen

VARIANT_SET = 'element'


def _has_mesh(prim):
    from pxr import Usd, UsdGeom
    return any(p.IsA(UsdGeom.Mesh) for p in Usd.PrimRange(prim))


def _is_proxy_mesh(prim):
    from pxr import UsdGeom
    # proxy_gen.py adds a purpose=proxy mesh as a plain sibling of the render mesh it stands in
    # for; that pairing is not a "pack" of alternates, so without this a perfectly ordinary
    # single-object asset with a proxy would look exactly like a 2-element pack (the mesh and its
    # own proxy) and only one of the two would end up shown.
    return prim.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(prim).GetPurposeAttr().Get() == UsdGeom.Tokens.proxy


def _find_pack_root(stage):
    """Breadth-first search for the shallowest prim with 2+ children that each have geometry -
    the point a bundled pack actually branches into separate top-level objects. Returns
    (prim, [candidate children]) or (None, None) if the stage never branches that way.
    """
    level = [prim for prim in stage.GetPseudoRoot().GetChildren() if prim.IsActive()]
    while level:
        next_level = []
        for prim in level:
            children = [c for c in prim.GetChildren() if c.IsActive()]
            candidates = [c for c in children if _has_mesh(c) and not _is_proxy_mesh(c)]
            if len(candidates) >= 2:
                return prim, candidates
            next_level.extend(children)
        level = next_level
    return None, None


def _anchor(stage, root):
    """The prim that carries the variant set: the asset's top prim above the branch point `root`
    (its default prim when `root` lies under it). A Reference / Stage Manager placement maps that
    prim onto the placed prim, so the switch shows up on the placed prim itself - Stage Manager's
    Inspector lists and edits only the placed prim's own variant sets, never deeper ones."""
    default = stage.GetDefaultPrim()
    if default and root.GetPath().HasPrefix(default.GetPath()):
        return default
    top = root
    while not top.GetParent().IsPseudoRoot():
        top = top.GetParent()
    return top


def _add_element_switch(stage):
    from pxr import UsdGeom

    root, candidates = _find_pack_root(stage)
    existing = [prim for prim in stage.Traverse() if prim.GetVariantSets().HasVariantSet(VARIANT_SET)]
    moved_from = None
    if existing:
        if root is not None and all(p.GetPath() == _anchor(stage, root).GetPath() for p in existing):
            return {'skipped': 'already has an element switch'}
        # Made by an older version on the branch prim deeper down: move it up to the top prim.
        # Removing first returns the file to its pre-switch state, so the new switch is built
        # (and later deleted) exactly like one added to an untouched asset.
        removed = _remove_element_switch(stage)
        if 'skipped' in removed:
            return {'skipped': 'already has an element switch (authored outside this file)'}
        moved_from = removed['removed_element_switch']
        root, candidates = _find_pack_root(stage)

    if root is None:
        return {'skipped': 'no multi-object pack found (need 2+ sibling objects with geometry)'}

    anchor = _anchor(stage, root)
    variant_set = anchor.GetVariantSets().AddVariantSet(VARIANT_SET)
    names = []
    for index, chosen in enumerate(candidates):
        name = f'Element{index}'
        names.append(name)
        variant_set.AddVariant(name)
        variant_set.SetVariantSelection(name)
        with variant_set.GetVariantEditContext():
            for other in candidates:
                token = UsdGeom.Tokens.inherited if other == chosen else UsdGeom.Tokens.invisible
                UsdGeom.Imageable(other).CreateVisibilityAttr().Set(token)
    variant_set.SetVariantSelection(names[0])
    result = {'element_switch': {'prim': str(anchor.GetPath()), 'branch': str(root.GetPath()), 'variants': names}}
    if moved_from:
        result['element_switch']['moved_from'] = moved_from
    return result


def _remove_element_switch(stage):
    """Undo _add_element_switch: drop the variant set, its selection and its variants entirely,
    leaving the prim exactly as if it had never been added (UsdVariantSets has no RemoveVariantSet
    in this USD version, so this edits the Sdf spec directly). Works for switches on the top prim
    and for ones an older version put on the branch prim.
    """
    from pxr import Sdf
    layer = stage.GetRootLayer()
    removed = []
    for prim in list(stage.Traverse()):
        if not prim.GetVariantSets().HasVariantSet(VARIANT_SET):
            continue
        path = prim.GetPath()
        spec = layer.GetPrimAtPath(path)
        if spec is None or VARIANT_SET not in spec.variantSets:
            continue   # authored on a referenced/payloaded layer, not this file's own root - nothing to remove here
        del spec.variantSets[VARIANT_SET]
        if VARIANT_SET in spec.variantSelections:
            del spec.variantSelections[VARIANT_SET]
        if VARIANT_SET in spec.variantSetNameList.prependedItems:
            spec.variantSetNameList.prependedItems.remove(VARIANT_SET)
        if VARIANT_SET in spec.variantSetNameList.explicitItems:
            spec.variantSetNameList.explicitItems.remove(VARIANT_SET)
        removed.append(str(path))
        # If the switch had to create an empty "over" just to hold itself (its prim is defined in
        # another layer), drop that too, so nothing of the switch is left behind.
        while spec is not None and spec.specifier == Sdf.SpecifierOver and spec.IsInert() \
                and spec.path != Sdf.Path.absoluteRootPath:
            parent = spec.nameParent
            if parent:
                del parent.nameChildren[spec.name]
            else:
                del layer.rootPrims[spec.name]
            spec = parent if parent and parent.path != Sdf.Path.absoluteRootPath else None
    if not removed:
        return {'skipped': 'no element switch found in this file'}
    return {'removed_element_switch': removed}


def generate(source, destination):
    if Path(source).suffix.lower() == '.usdz':
        return proxy_gen.generate_usdz(source, destination, _add_element_switch)
    return proxy_gen.generate_plain(source, destination, _add_element_switch)


def remove(source, destination):
    if Path(source).suffix.lower() == '.usdz':
        return proxy_gen.generate_usdz(source, destination, _remove_element_switch)
    return proxy_gen.generate_plain(source, destination, _remove_element_switch)


if __name__ == '__main__':
    import json
    mode = sys.argv[4] if len(sys.argv) > 4 else 'add'
    result = remove(sys.argv[1], sys.argv[2]) if mode == 'remove' else generate(sys.argv[1], sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(result), encoding='utf-8')
