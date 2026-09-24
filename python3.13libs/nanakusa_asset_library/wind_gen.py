"""Standalone worker: make a looping wind animation of a USD plant as a new asset "<name>_Anim.usd".

The source file is never edited. The new entry layer references it and adds only:
- one UsdSkel Skeleton per plant (a variant or a whole file), built from the plant's own geometry;
- skin weights (primvars:skel:jointIndices / jointWeights, 3 influences) on every mesh - render
  meshes, other LODs and proxies - so the "LOD" / "variant" sets and purpose=proxy keep working;
- a SkelAnimation per plant whose joint rotations come from a value clip ("anim/<name>_Anim_clip.usd")
  that is looped for any frame (Houdini's 1, a shot's 1001...), so a placed or instanced copy never
  stops moving.
UsdSkel keeps the data small (joint rotations per frame instead of every point) and deforms normals
with the points; Karma CPU / XPU and Houdini's viewport skin it, also inside a Point Instancer.

Rig (built on the most detailed LOD of each plant, in a Y-up working space):
1. Connected pieces of the mesh are split into "rooted" ones (reaching down to the plant's base:
   blades, stems, leaves) and loose ones (florets, seeds and debris floating next to a stem in scanned
   plants). Loose pieces follow the nearest rooted point rigidly, so they stay attached.
2. In each rooted piece, the geodesic distance from its root (the lowest point nearest the plant's
   axis) is cut into bands; each connected cluster of a band becomes a joint, parented to the cluster
   its shortest paths come from. This follows arching leaves and also branching stems (a tree).
3. A point is skinned to its joint and the next joint along the distance, linearly, so the surface
   bends smoothly and keeps its length (joints only rotate).
Other LODs and proxies take the weights of the nearest point of the reference LOD.

Motion (no prevailing direction - it should look right however an instance is rotated):
- Wind = gusts arriving from several directions (plane waves travelling through the plant), with a
  slow calm/gusty envelope, plus a turbulent field whose phase changes over ~ a plant's size, so
  neighbouring blades differ.
- Each rooted piece responds like damped oscillators: a main bend at its own natural frequency
  (longer and top-heavy pieces are slower), a faster bend of the outer part, and a small twist.
  Everything is T-periodic and the response is solved in the frequency domain (FFT), so the loop is
  exact - no seam, no warm-up.
Amplitudes are normalised (RMS of the tip angle), so the same strength looks alike on every asset.

    wind_gen.py <source.usd> <output _Anim.usd> <result.json> [strength] [loop seconds]

Writes the entry and clip under temporary names next to their final paths (see temp_paths) and
reports them in result.json; the caller (ui.WindJob) moves them into place after it succeeds.
"""
import hashlib
import heapq
import itertools
import json
import math
import os
from pathlib import Path
import sys
import time

import numpy as np

STRENGTH = 1.0
LOOP_SECONDS = 10.0
MAX_COMBINATIONS = 128
SKEL_ROOT_FALLBACK_NOTE = 'SkelRoot is the top prim (no common group below it): a placement that types the prim itself would stop the skinning.'
SCOPE = 'NAL_wind'           # holds the Skeletons and SkelAnimations, under the SkelRoot
PHASE_SET, PHASES = 'wind_phase', 4   # the same motion, started a quarter loop apart
CLIP_FOLDER = 'anim'
TEMP_MARK = '.nanakusa_generate_tmp'   # core.GENERATE_TMP: never listed as an asset
LOOP_REPEAT = (-100, 1000)   # the clip is mapped over these loop indices (frames -24000..240000 at 10 s / 24 fps)

# Rig
BANDS = 14                   # geodesic bands over the plant height (joint spacing ~ height / BANDS)
MIN_CLUSTER_POINTS = 6
ROOT_TOLERANCE = 0.05        # of the height: pieces reaching this close to the base are rooted
GROUND_FADE = 0.06           # of the height: below this, points blend into the unmoving ground joint
INFLUENCES = 3               # two joints along the piece + the ground
GROUP_GAP = 0.006            # of the height: loose pieces this close form one group (a seed head)
# Motion (per unit strength). Angles in radians.
TIP_RMS = 0.20               # main bend: RMS of the tip angle
TIP_MAX = 0.60               # soft limit of the main bend
FLUTTER_RMS = 0.05           # faster bend of the outer part
TWIST_RMS = 0.08             # twist about the blade at its tip
BASE_SHARE = 0.10            # part of the main bend taken at the root (the whole piece pivots a little)
BEND_POWER = 0.6             # the rest grows towards the tip like s^(1+p)
REF_FREQUENCY = 1.1          # Hz, main bend of a 0.5 m piece
DAMPING = 0.32               # main bend (grass in air is well damped: gusts show, not a metronome)
OUTER_DAMPING = 0.22
TURBULENCE_SHARE = 0.8       # of the main bend's force: how differently neighbouring pieces move
GUST_SPEED = 3.0             # m/s
MAX_FREQUENCY = 2.5          # Hz: small plants would otherwise jitter


def size_response(metres):
    """How much a plant of this height sways, relative to a 0.5-1 m one: a seedling sits in the
    slow air near the ground and is stiff for its size; a shrub or tree bends less at its top."""
    if metres < 0.5:
        return max(math.sqrt(metres / 0.5), 0.3)
    if metres > 1.0:
        return max(1.0 / math.sqrt(metres), 0.3)
    return 1.0


def temp_paths(output):
    """(entry, clip) temporary paths for the final `output` and its clip."""
    output = Path(output)
    clip = clip_path(output)
    return (output.with_name(output.stem + TEMP_MARK + output.suffix),
            clip.with_name(clip.stem + TEMP_MARK + clip.suffix))


def clip_path(output):
    output = Path(output)
    return output.parent / CLIP_FOLDER / (output.stem + '_clip.usd')


def output_for(source):
    """Where the animated version of `source` goes, so the library lists it as an asset of its own
    without changing how anything else is listed:
    - a USD layer sharing its folder with others (Big / Small + ./textures): next to it;
    - a package entry (named after its folder): a new package folder beside that folder;
    - a .usdz: a new package folder next to it (a loose .usd would turn a plain folder into a
      folder of shared layers and hide its subfolders)."""
    source = Path(source)
    name = source.stem + '_Anim'
    if source.stem.lower() == source.parent.name.lower():
        return source.parent.parent / name / (name + '.usd')
    if source.suffix.lower() == '.usdz':
        return source.parent / name / (name + '.usd')
    return source.with_name(name + '.usd')


def is_output(path):
    """True for a file this module made (its clip sits in ./anim next to it)."""
    path = Path(path)
    return path.stem.endswith('_Anim') and clip_path(path).is_file()


# ---------------------------------------------------------------- geometry helpers

def components(count, a, b):
    """Connected-component label (0..n-1) of each of `count` nodes joined by edges a[i]-b[i]."""
    label = np.arange(count)
    if len(a):
        while True:
            low = np.minimum(label[a], label[b])
            new = label.copy()
            np.minimum.at(new, a, low)
            np.minimum.at(new, b, low)
            new = new[new[new]]
            if np.array_equal(new, label):
                break
            label = new
    return np.unique(label, return_inverse=True)[1]


def mesh_edges(counts, indices):
    """Unique undirected edges (a, b) of a polygon mesh."""
    counts = np.asarray(counts, dtype=np.int64)
    indices = np.asarray(indices, dtype=np.int64)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    following = np.arange(len(indices)) + 1
    following[starts + counts - 1] = starts
    a, b = indices, indices[following]
    pair = np.sort(np.stack([a, b], 1), 1)
    pair = np.unique(pair[pair[:, 0] != pair[:, 1]], axis=0)
    return pair[:, 0], pair[:, 1]


def _brute(reference, query):
    out = np.empty(len(query), dtype=np.int64)
    r2 = (reference ** 2).sum(1)
    step = max(1, int(2e7 // max(len(reference), 1)))
    for i in range(0, len(query), step):
        q = query[i:i + step]
        out[i:i + step] = (r2[None, :] - 2 * q @ reference.T).argmin(1)
    return out


def nearest(reference, query, cell=None):
    """Index into `reference` of the nearest point to each `query` point. Exact: buckets on a grid
    sized to the reference density; a query whose answer might lie beyond its 3x3x3 block of cells
    is searched again on a coarser grid."""
    reference = np.asarray(reference, dtype=np.float64)
    query = np.asarray(query, dtype=np.float64)
    if len(query) == 0:
        return np.zeros(0, dtype=np.int64)
    if len(reference) * len(query) <= 4e6:
        return _brute(reference, query)
    low = np.minimum(reference.min(0), query.min(0)) - 1e-9
    extent = max(float((np.maximum(reference.max(0), query.max(0)) - low).max()), 1e-9)
    if cell is None:
        cell = extent / 32
        for _ in range(4):
            c = np.floor((reference - low) / cell).astype(np.int64)
            occupied = len(np.unique(c[:, 0] * 1_000_003 ** 2 + c[:, 1] * 1_000_003 + c[:, 2]))
            cell *= float(np.clip((16.0 * occupied / len(reference)) ** 0.5, 0.25, 4.0))
    if cell >= extent:
        return _brute(reference, query)
    rc = np.floor((reference - low) / cell).astype(np.int64) + 1
    qc = np.floor((query - low) / cell).astype(np.int64) + 1
    size = np.maximum(rc.max(0), qc.max(0)) + 2
    key = lambda c: (c[..., 0] * size[1] + c[..., 1]) * size[2] + c[..., 2]
    ref_key = key(rc)
    order = np.argsort(ref_key, kind='stable')
    sorted_key = ref_key[order]
    cells, inverse = np.unique(key(qc), return_inverse=True)
    q_order = np.argsort(inverse, kind='stable')
    q_start = np.r_[0, np.cumsum(np.bincount(inverse, minlength=len(cells)))]
    first = qc[q_order[q_start[:-1]]]
    # The 3 cells along z are consecutive keys: 9 ranges per query cell.
    shifts = np.array([(i, j, 0) for i in (-1, 0, 1) for j in (-1, 0, 1)])
    base = key(first[:, None, :] + shifts[None])
    lo = np.searchsorted(sorted_key, base - 1, 'left')
    hi = np.searchsorted(sorted_key, base + 1, 'right')
    best = np.full(len(query), -1, dtype=np.int64)
    best_d = np.full(len(query), np.inf)
    for u in range(len(cells)):
        ranges = [order[l:h] for l, h in zip(lo[u], hi[u]) if h > l]
        if not ranges:
            continue
        candidates = np.concatenate(ranges)
        members = q_order[q_start[u]:q_start[u + 1]]
        d = ((query[members, None, :] - reference[None, candidates, :]) ** 2).sum(-1)
        pick = d.argmin(1)
        best[members] = candidates[pick]
        best_d[members] = d[np.arange(len(members)), pick]
    unsure = np.nonzero(best_d > cell * cell)[0]
    if len(unsure):
        best[unsure] = nearest(reference, query[unsure], cell * 3)
    return best


def geodesic(count, a, b, points, sources):
    """Shortest-path distance along edges from the nearest of `sources`, and each node's predecessor."""
    length = np.linalg.norm(points[a] - points[b], axis=1)
    src = np.r_[a, b]
    dst = np.r_[b, a]
    weight = np.r_[length, length]
    order = np.argsort(src, kind='stable')
    dst, weight = dst[order].tolist(), weight[order].tolist()
    start = np.r_[0, np.cumsum(np.bincount(src, minlength=count))].tolist()
    distance = [math.inf] * count
    previous = [-1] * count
    heap = []
    for s in sources:
        distance[s] = 0.0
        heap.append((0.0, int(s)))
    heapq.heapify(heap)
    while heap:
        d, n = heapq.heappop(heap)
        if d > distance[n]:
            continue
        for e in range(start[n], start[n + 1]):
            m = dst[e]
            nd = d + weight[e]
            if nd < distance[m]:
                distance[m] = nd
                previous[m] = n
                heapq.heappush(heap, (nd, m))
    return np.asarray(distance), np.asarray(previous)


def first_per(group, score):
    """{group label: index with the lowest finite score in that group}."""
    order = np.lexsort((score, group))
    if not len(order):
        return {}
    first = np.r_[True, group[order][1:] != group[order][:-1]]
    return {int(group[i]): int(i) for i in order[first] if np.isfinite(score[i])}


# ---------------------------------------------------------------- rig

class Rig:
    """Joints (a forest) and 2-influence weights for the points of one plant (working space, Y up).

    Pieces reaching the base are chained from their root; other large pieces (leaves on a stem,
    scanned blades that do not quite touch it) get their own chain, rooted where they come nearest
    to a rooted piece and parented to the joint there; small loose pieces are rigidly attached."""

    def __init__(self, points, a, b, meters):
        self.points = points
        self.meters = meters
        n = len(points)
        self.height = float(np.ptp(points[:, 1])) or 1e-6
        ground = float(points[:, 1].min())
        piece = components(n, a, b)
        pieces = piece.max() + 1 if n else 0
        low = np.full((pieces, 3), np.inf)
        high = np.full((pieces, 3), -np.inf)
        np.minimum.at(low, piece, points)
        np.maximum.at(high, piece, points)
        band = self.height / BANDS
        span = high[:, 1] - low[:, 1]
        rooted = (low[:, 1] < ground + ROOT_TOLERANCE * self.height) & (span > 1.5 * band)
        if not rooted.any():
            rooted[np.argmax(span)] = True
        # Root of a rooted piece: among its points near its bottom, the one nearest the plant's axis.
        near_bottom = (points[:, 1] < low[piece, 1] + 0.25 * band) & rooted[piece]
        axis = points[near_bottom][:, [0, 2]].mean(0)
        radial = np.linalg.norm(points[:, [0, 2]] - axis, axis=1)
        root_of = first_per(piece, np.where(near_bottom, radial, np.inf))
        rooted[:] = False
        rooted[list(root_of)] = True
        # Pieces lying on the ground (roots, litter) do not move: bound to a joint that never turns.
        on_ground = ~rooted & (high[:, 1] < ground + band)
        # Branch pieces: rooted where they come nearest to a rooted piece.
        branch = ~rooted & ~on_ground & (np.linalg.norm(high - low, axis=1) > 2 * band)
        attach_point = {}
        on_rooted = np.nonzero(rooted[piece])[0]
        on_branch = np.nonzero(branch[piece])[0]
        if len(on_branch):
            near = on_rooted[nearest(points[on_rooted], points[on_branch])]
            gap = ((points[on_branch] - points[near]) ** 2).sum(1)
            for p, k in first_per(piece[on_branch], gap).items():
                root_of[p] = int(on_branch[k])
                attach_point[p] = int(near[k])
        chained = rooted | branch
        on = chained[piece]
        inside = on[a] & on[b]
        distance, previous = geodesic(n, a[inside], b[inside], points, [root_of[p] for p in sorted(root_of)])
        on &= np.isfinite(distance)
        distance = np.where(on, distance, 0.0)
        # Bands and clusters (connected parts of a band inside one piece).
        level = np.where(on, np.floor(distance / band), -1).astype(np.int64)
        same = on[a] & on[b] & (level[a] == level[b])
        cluster = components(n, a[same], b[same])
        # Parent cluster: the cluster most shortest paths into this one come from.
        link = on & (previous >= 0)
        child_c, parent_c = cluster[link], cluster[previous[link]]
        cross = child_c != parent_c
        clusters = cluster.max() + 1
        count = np.bincount(cluster[on], minlength=clusters)
        mean_d = np.bincount(cluster[on], weights=distance[on], minlength=clusters) / np.maximum(count, 1)
        parent = {}
        if cross.any():
            pairs, votes = np.unique(np.stack([child_c[cross], parent_c[cross]], 1), axis=0, return_counts=True)
            for k in np.argsort(votes, kind='stable'):
                parent[int(pairs[k, 0])] = int(pairs[k, 1])   # highest vote last
        # Merge tiny clusters into their parent, shallowest first.
        rep = np.arange(clusters)
        for c in np.argsort(mean_d):
            if count[c] and c in parent and count[c] < MIN_CLUSTER_POINTS:
                target = rep[parent[c]]
                rep[c] = target
                count[target] += count[c]
        for _ in range(64):
            nxt = rep[rep]
            if np.array_equal(nxt, rep):
                break
            rep = nxt
        cluster = np.where(on, rep[cluster], -1)
        cl_count = np.bincount(cluster[on], minlength=clusters)
        sums = np.zeros((clusters, 3))
        np.add.at(sums, cluster[on], points[on])
        cl_dist = np.bincount(cluster[on], weights=distance[on], minlength=clusters) / np.maximum(cl_count, 1)
        cl_piece = np.zeros(clusters, dtype=np.int64)
        cl_piece[cluster[on]] = piece[on]
        by_piece = {}
        for c in np.nonzero(cl_count)[0]:
            by_piece.setdefault(int(cl_piece[c]), []).append(int(c))

        def cluster_parent(c):
            p = parent.get(c)
            p = None if p is None else int(rep[p])
            return None if p == c else p

        self.position, self.distance, self.parent, self.piece, self.size, self.piece_root = [], [], [], [], [], []
        self.children = []   # same-piece children (the chain), not the branches hanging from a joint
        index = np.zeros((n, 2), dtype=np.int64)
        weight = np.zeros((n, 2))
        joint_of_cluster = np.full(clusters, -1, dtype=np.int64)

        def add_joint(position, dist, parent_joint, p, size, is_root):
            self.position.append(position)
            self.distance.append(dist)
            self.parent.append(parent_joint)
            self.piece.append(p)
            self.size.append(size)
            self.piece_root.append(is_root)
            self.children.append([])
            if parent_joint >= 0 and not is_root:
                self.children[parent_joint].append(len(self.position) - 1)
            return len(self.position) - 1

        def build(piece_ids, parent_of_piece):
            if not piece_ids:
                return
            root_joint = {}
            for p in piece_ids:
                root_joint[p] = add_joint(points[root_of[p]], 0.0, parent_of_piece(p), p, 0, True)
            chain = sorted((c for p in piece_ids for c in by_piece.get(p, [])), key=lambda c: cl_dist[c])
            for c in chain:
                cp = cluster_parent(c)
                pj = joint_of_cluster[cp] if cp is not None and joint_of_cluster[cp] >= 0 else root_joint[int(cl_piece[c])]
                joint_of_cluster[c] = add_joint(sums[c] / cl_count[c], cl_dist[c], int(pj), int(cl_piece[c]), int(cl_count[c]), False)
            # Weights: between the point's joint and the previous / next one along the distance.
            mine = np.nonzero(on & np.isin(piece, list(piece_ids)))[0]
            position = np.asarray(self.position)
            dist_all = np.asarray(self.distance)
            parent_all = np.asarray(self.parent)
            j = joint_of_cluster[cluster[mine]]
            d = distance[mine]
            dj = dist_all[j]
            pj = np.where(np.asarray(self.piece_root)[j], j, parent_all[j])   # never blend across pieces
            dp = dist_all[pj]
            below = d <= dj
            f = np.clip((d - dp) / np.maximum(dj - dp, 1e-9), 0, 1)
            m = mine[below]
            index[m, 0], index[m, 1] = j[below], pj[below]
            weight[m, 0], weight[m, 1] = f[below], 1 - f[below]
            m, ja, da = mine[~below], j[~below], d[~below]
            nxt = np.full(len(m), -1)
            for jj in np.unique(ja):
                kids = self.children[jj]
                sel = np.nonzero(ja == jj)[0]
                if len(kids) == 1:
                    nxt[sel] = kids[0]
                elif kids:
                    nxt[sel] = np.asarray(kids)[((points[m[sel], None, :] - position[kids][None]) ** 2).sum(-1).argmin(1)]
            has = nxt >= 0
            dn = np.where(has, dist_all[np.maximum(nxt, 0)], da)
            g = np.where(has, np.clip((da - dist_all[ja]) / np.maximum(dn - dist_all[ja], 1e-9), 0, 1), 0)
            index[m, 0], index[m, 1] = ja, np.where(has, nxt, ja)
            weight[m, 0], weight[m, 1] = 1 - g, g

        base = np.r_[axis[0], ground, axis[1]]
        add_joint(base, 0.0, -1, -1, 0, True)   # joint 0: the ground (never animated)
        build(sorted(p for p in root_of if rooted[p]), lambda p: -1)
        branches = sorted(p for p in root_of if branch[p] and on[root_of[p]])
        # A branch hangs from the joint that moves its attachment point the most.
        build(branches, lambda p: int(index[attach_point[p], np.argmax(weight[attach_point[p]])]))
        # Loose pieces (and unreachable points) follow the nearest chained point, rigidly per piece.
        grounded = on_ground[piece] & ~on
        index[grounded] = 0
        weight[grounded] = (1.0, 0.0)
        on_idx = np.nonzero(on)[0]
        loose = np.nonzero(~on & ~grounded)[0]
        self.attached = np.zeros(len(self.position))
        if len(loose):
            anchor = self._anchor_loose(points, piece, loose, on_idx, GROUP_GAP * self.height)
            index[loose] = index[anchor]
            weight[loose] = weight[anchor]
            np.add.at(self.attached, index[anchor, 0], 1)
        self.position = np.asarray(self.position, dtype=np.float64)
        self.distance = np.asarray(self.distance)
        self.parent = np.asarray(self.parent, dtype=np.int64)
        self.piece = np.asarray(self.piece, dtype=np.int64)
        self.size = np.asarray(self.size)
        self.piece_root = np.asarray(self.piece_root, dtype=bool)
        assert all(self.parent[j] < j for j in range(len(self.parent)))
        # Nothing at ground level moves (roots, the base of the tuft): fade into the ground joint.
        fade = np.clip((points[:, 1] - ground) / max(GROUND_FADE * self.height, 1e-9), 0, 1)
        fade = fade * fade * (3 - 2 * fade)
        self.index = np.concatenate([index, np.zeros((n, 1), dtype=np.int64)], 1)
        self.weight = np.concatenate([weight * fade[:, None], 1 - fade[:, None]], 1)
        self.pieces = sorted(set(self.piece.tolist()) - {-1})
        self.loose_points = len(loose)
        self.ground_points = int(grounded.sum())
        self.branch_pieces = len(branches)

    @staticmethod
    def _anchor_loose(points, piece, loose, on_idx, gap_limit):
        """The chained point each loose point follows. Loose pieces lying within `gap_limit` of each
        other (the florets of one seed head) form a group that follows one chained piece - the one
        they are nearest to overall - so a head that touches another stem is not torn between two;
        each piece of the group then hangs rigidly from its nearest point of that chained piece."""
        near = on_idx[nearest(points[on_idx], points[loose])]
        gap = np.sqrt(((points[loose] - points[near]) ** 2).sum(1))
        # Groups: pieces sharing a grid cell (of size gap_limit) or neighbouring ones.
        cell = np.floor((points[loose] - points[loose].min(0)) / max(gap_limit, 1e-9)).astype(np.int64) + 1
        size = cell.max(0) + 2
        key = (cell[:, 0] * size[1] + cell[:, 1]) * size[2] + cell[:, 2]
        pieces_here = piece[loose]
        pairs = np.unique(np.stack([key, pieces_here], 1), axis=0)
        first_in_cell = {}
        for k, p in pairs.tolist():
            first_in_cell.setdefault(k, p)
        ids = {p: i for i, p in enumerate(np.unique(pieces_here).tolist())}
        a, b = [], []
        for k, p in pairs.tolist():
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        q = first_in_cell.get(k + (dx * size[1] + dy) * size[2] + dz)
                        if q is not None and q != p:
                            a.append(ids[p]); b.append(ids[q])
        group = components(len(ids), np.asarray(a, dtype=np.int64), np.asarray(b, dtype=np.int64))
        point_group = group[np.asarray([ids[p] for p in pieces_here.tolist()])]
        # Each group follows the chained piece its points are closest to (votes weighted by closeness).
        target = piece[near]
        votes = {}
        for g, t, w in zip(point_group.tolist(), target.tolist(), (1.0 / (gap + 0.1 * gap_limit)).tolist()):
            votes[(g, t)] = votes.get((g, t), 0.0) + w
        chosen = {}
        for (g, t), w in votes.items():
            if w > chosen.get(g, (None, -1.0))[1]:
                chosen[g] = (t, w)
        anchor = np.empty(len(loose), dtype=np.int64)
        for g in np.unique(point_group):
            members = np.nonzero(point_group == g)[0]
            stem = on_idx[piece[on_idx] == chosen[int(g)][0]]
            hit = stem[nearest(points[stem], points[loose[members]])]
            d = ((points[loose[members]] - points[hit]) ** 2).sum(1)
            for p, k in first_per(pieces_here[members], d).items():
                anchor[members[pieces_here[members] == p]] = hit[k]
        return anchor

    def joint_names(self):
        names = []
        for j, p in enumerate(self.parent):
            token = 'ground' if self.piece[j] < 0 else ('b%d' % self.piece[j]) if self.piece_root[j] else ('j%d' % j)
            names.append(token if p < 0 else names[p] + '/' + token)
        return names


# ---------------------------------------------------------------- motion

def response(force, freqs, natural, damping):
    """Steady-state response (unit static gain) of a damped oscillator to a periodic force sampled
    over one loop (axis 0 = time)."""
    spectrum = np.fft.rfft(force, axis=0)
    w, w0 = 2 * np.pi * freqs, 2 * np.pi * natural
    h = w0 ** 2 / (w0 ** 2 - w ** 2 + 2j * damping * w0 * w)
    shape = (-1,) + (1,) * (force.ndim - 1)
    return np.fft.irfft(spectrum * h.reshape(shape), n=force.shape[0], axis=0)


class Wind:
    """Periodic wind force F(x, t) for one plant: gusts from all around plus turbulence."""

    def __init__(self, rng, loop, size):
        self.loop = loop
        cycles = max(2, int(round(loop * 0.8)))
        count = 6
        angle = np.arange(count) * 2 * np.pi / count + rng.uniform(-0.4, 0.4, count)
        self.g_dir = np.stack([np.cos(angle), np.zeros(count), np.sin(angle)], 1)
        self.g_n = rng.integers(1, cycles + 1, count)
        self.g_amp = self.g_n ** -0.5 * rng.uniform(0.7, 1.3, count)
        self.g_phase = rng.uniform(0, 2 * np.pi, count)
        self.g_k = self.g_dir * (2 * np.pi * self.g_n / loop / GUST_SPEED)[:, None]
        self.env = [(1, 0.6, rng.uniform(0, 2 * np.pi)), (int(rng.integers(2, 4)), 0.25, rng.uniform(0, 2 * np.pi))]
        count = 10
        self.t_n = rng.integers(max(3, int(0.5 * loop)), max(4, int(3.0 * loop)) + 1, count)
        self.t_amp = (self.t_n / loop) ** -0.8
        direction = rng.normal(size=(count, 3))
        direction[:, 1] *= 0.35
        self.t_dir = direction / np.linalg.norm(direction, axis=1, keepdims=True)
        wave = rng.normal(size=(count, 3))
        wave /= np.linalg.norm(wave, axis=1, keepdims=True)
        self.t_k = wave * (2 * np.pi / (size * rng.uniform(0.3, 1.5, count)))[:, None]
        self.t_phase = rng.uniform(0, 2 * np.pi, count)
        self.t_twist = rng.normal(size=count)

    def envelope(self, t):
        e = np.ones_like(t)
        for n, a, p in self.env:
            e += a * np.sin(2 * np.pi * n * t / self.loop + p)
        return np.maximum(e, 0.15)

    def gust(self, x, t):
        """(len(t), len(x), 3)"""
        phase = 2 * np.pi * self.g_n[None, None, :] * t[:, None, None] / self.loop - (x @ self.g_k.T)[None] + self.g_phase
        wave = (np.sin(phase) * self.g_amp)[..., None] * self.g_dir[None, None]
        return wave.sum(2) * self.envelope(t)[:, None, None]

    def turbulence(self, x, t, twist=False):
        phase = 2 * np.pi * self.t_n[None, None, :] * t[:, None, None] / self.loop - (x @ self.t_k.T)[None] + self.t_phase
        wave = np.sin(phase) * self.t_amp
        if twist:
            return (wave * self.t_twist).sum(2)
        return (wave[..., None] * self.t_dir[None, None]).sum(2)


def soft_limit(v, limit):
    m = np.linalg.norm(v, axis=-1, keepdims=True)
    return v * (limit * np.tanh(m / limit) / np.maximum(m, 1e-12))


def animate(rig, seed, frames, fps, strength):
    """Local joint rotation vectors (frames+1, joints, 3) in the working space; the last frame
    equals the first (the loop point)."""
    rng = np.random.default_rng(seed)
    loop = frames / fps
    t = np.arange(frames) / fps
    freqs = np.fft.rfftfreq(frames, 1 / fps)
    size = rig.height * rig.meters
    wind = Wind(rng, loop, max(size, 0.05))
    strength = strength * size_response(size)
    x = rig.position * rig.meters
    joints = len(x)
    s = np.zeros(joints)
    length = {}
    for p in rig.pieces:
        on = rig.piece == p
        length[p] = max(rig.distance[on].max(), 1e-6)
        s[on] = rig.distance[on] / length[p]
    moving = rig.piece >= 0
    # Segment direction at rest (towards the children, else from the parent).
    u = np.zeros((joints, 3))
    for j in range(joints):
        kids = rig.children[j]
        if kids:
            u[j] = (rig.position[kids] * rig.size[kids, None]).sum(0) / max(rig.size[kids].sum(), 1) - rig.position[j]
        elif rig.parent[j] >= 0:
            u[j] = rig.position[j] - rig.position[rig.parent[j]]
    u /= np.maximum(np.linalg.norm(u, axis=1, keepdims=True), 1e-12)
    # Per piece: where the wind acts, how it responds.
    pieces = rig.pieces
    centre = np.zeros((len(pieces), 3))
    natural = np.zeros(len(pieces))
    flex = np.zeros(len(pieces))
    for i, p in enumerate(pieces):
        on = rig.piece == p
        upper = on & (s > 0.5)
        centre[i] = x[upper if upper.any() else on].mean(0)
        own = rig.size[on].sum()
        load = 1 + 0.6 * rig.attached[on].sum() / max(own, 1)   # florets and seed heads make it heavier
        metres = length[p] * rig.meters
        natural[i] = np.clip(REF_FREQUENCY * math.sqrt(0.5 / max(metres, 0.02)) / math.sqrt(load) * rng.uniform(0.85, 1.15), 0.25, MAX_FREQUENCY)
        flex[i] = np.clip(math.sqrt(length[p] / rig.height), 0.45, 1.2) * math.sqrt(load) ** 0.5 * rng.uniform(0.8, 1.2)
    piece_index = {p: i for i, p in enumerate(pieces)}
    pj = np.asarray([piece_index.get(p, 0) for p in rig.piece])
    # Main bend: one response per piece.
    force = wind.gust(centre, t) + TURBULENCE_SHARE * wind.turbulence(centre, t)
    main = np.stack([response(force[:, i], freqs, natural[i], DAMPING) for i in range(len(pieces))], 1)
    main *= TIP_RMS / max(np.sqrt((main ** 2).sum(-1).mean()), 1e-12)
    main = soft_limit(main * flex[None, :, None] * strength, TIP_MAX * math.sqrt(strength))
    # Outer bend: per joint, faster, driven more by the turbulence.
    force = 0.5 * wind.gust(x, t) + wind.turbulence(x, t)
    outer = np.empty_like(force)
    for i in range(len(pieces)):
        on = pj == i
        outer[:, on] = response(force[:, on], freqs, min(2.8 * natural[i], 2 * MAX_FREQUENCY), OUTER_DAMPING)
    outer *= FLUTTER_RMS * strength / max(np.sqrt((outer ** 2).sum(-1).mean()), 1e-12)
    # Twist about the blade.
    twist_force = wind.turbulence(centre, t, twist=True)
    twist = np.stack([response(twist_force[:, i], freqs, min(2.2 * natural[i], 4.0), 0.15) for i in range(len(pieces))], 1)
    twist *= TWIST_RMS * strength / max(np.sqrt((twist ** 2).mean()), 1e-12)
    # Share of each quantity taken by each joint (increments of a profile along the piece).
    root = rig.piece_root   # a branch's root hangs from another piece: its profile starts again at 0
    ps = np.where(root, 0.0, s[np.maximum(rig.parent, 0)])
    bend = lambda v: BASE_SHARE + (1 - BASE_SHARE) * v ** (1 + BEND_POWER)
    inc_main = np.where(root, BASE_SHARE, bend(s) - bend(ps))
    outer_profile = lambda v: np.clip((v - 0.3) / 0.7, 0, 1) ** 2
    inc_outer = outer_profile(s) - outer_profile(ps)
    inc_twist = s ** 2 - ps ** 2
    rot = (inc_main[None, :, None] * np.cross(u[None], main[:, pj])
           + inc_outer[None, :, None] * np.cross(u[None], outer)
           + (inc_twist[None, :] * twist[:, pj])[..., None] * u[None])
    rot[:, ~moving] = 0
    return np.concatenate([rot, rot[:1]], 0)


def quaternions(rotvec):
    """(..., 3) rotation vectors -> (..., 4) quaternions (real first)."""
    angle = np.linalg.norm(rotvec, axis=-1, keepdims=True)
    half = angle / 2
    axis = rotvec / np.maximum(angle, 1e-12)
    return np.concatenate([np.cos(half), axis * np.sin(half)], -1)


def skin(rig, rotvec_frame, points, index, weight):
    """Linear blend skinning of `points` (working space) for one frame of local rotation vectors -
    the same maths UsdSkel does, used to check the result here."""
    joints = len(rig.position)
    q = quaternions(rotvec_frame)
    w_, x_, y_, z_ = q.T
    rot = np.stack([
        1 - 2 * (y_ * y_ + z_ * z_), 2 * (x_ * y_ - z_ * w_), 2 * (x_ * z_ + y_ * w_),
        2 * (x_ * y_ + z_ * w_), 1 - 2 * (x_ * x_ + z_ * z_), 2 * (y_ * z_ - x_ * w_),
        2 * (x_ * z_ - y_ * w_), 2 * (y_ * z_ + x_ * w_), 1 - 2 * (x_ * x_ + y_ * y_)], 1).reshape(-1, 3, 3)
    world_r = np.zeros((joints, 3, 3))
    world_t = np.zeros((joints, 3))
    for j in range(joints):
        p = rig.parent[j]
        if p < 0:
            world_r[j] = rot[j]
            world_t[j] = rig.position[j]
        else:
            world_r[j] = world_r[p] @ rot[j]
            world_t[j] = world_t[p] + world_r[p] @ (rig.position[j] - rig.position[p])
    out = np.zeros_like(points)
    for k in range(index.shape[1]):
        j = index[:, k]
        local = points - rig.position[j]
        out += weight[:, k, None] * (np.einsum('nij,nj->ni', world_r[j], local) + world_t[j])
    return out


# ---------------------------------------------------------------- USD

def up_matrix(up):
    """Rotation taking the stage's space to the Y-up working space (rows act on column vectors)."""
    if up == 'Z':
        return np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]])
    return np.eye(3)


def is_lod_set(name):
    return name.lower().startswith('lod')


def mesh_versions(stage, top):
    """Every mesh the file can show, per combination of the top prim's variant selections.
    Returns (set names, combos, {combo index: {path: key}}, {key: data})."""
    from pxr import Usd, UsdGeom, Sdf
    sets = list(top.GetVariantSets().GetNames())
    choices = [top.GetVariantSets().GetVariantSet(n).GetVariantNames() or [''] for n in sets]
    combos = list(itertools.product(*choices)) or [()]
    if len(combos) > MAX_COMBINATIONS:
        raise ValueError('too many variant combinations (%d)' % len(combos))
    top_world = UsdGeom.Xformable(top).ComputeLocalToWorldTransform(Usd.TimeCode.Default()) if top.IsA(UsdGeom.Xformable) else None
    session = stage.GetSessionLayer()
    seen, versions = {}, {}
    with Usd.EditContext(stage, session):
        for ci, combo in enumerate(combos):
            for name, value in zip(sets, combo):
                if value:
                    top.GetVariantSets().GetVariantSet(name).SetVariantSelection(value)
            active = {}
            for prim in Usd.PrimRange(top):
                if not prim.IsA(UsdGeom.Mesh):
                    continue
                mesh = UsdGeom.Mesh(prim)
                if mesh.GetPointsAttr().ValueMightBeTimeVarying():
                    raise ValueError('%s is already animated' % prim.GetPath())
                if prim.HasAPI('SkelBindingAPI'):
                    raise ValueError('%s is already skinned' % prim.GetPath())
                points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
                counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int64)
                indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int64)
                if not len(points):
                    continue
                world = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                if top_world is not None:
                    world = world * top_world.GetInverse()
                matrix = np.array(world, dtype=np.float64).reshape(4, 4)
                digest = hashlib.sha1(points.tobytes() + indices.tobytes() + matrix.tobytes()).hexdigest()
                key = (str(prim.GetPath()), digest)
                if key not in versions:
                    versions[key] = {'path': str(prim.GetPath()), 'points': points, 'counts': counts,
                                     'indices': indices, 'matrix': matrix,
                                     'proxy': UsdGeom.Imageable(prim).ComputePurpose() == UsdGeom.Tokens.proxy}
                active[str(prim.GetPath())] = key
            seen[ci] = active
        session.Clear()
    return sets, combos, seen, versions


class Plan:
    """Which meshes form which plant, and where each mesh's binding must be authored."""

    def __init__(self, sets, combos, seen, versions):
        self.sets, self.combos, self.versions = sets, combos, versions
        lod = [i for i, n in enumerate(sets) if is_lod_set(n)]
        # Most detailed variant of each LOD set.
        self.reference = {}
        for i in lod:
            totals = {}
            for ci, combo in enumerate(combos):
                totals.setdefault(combo[i], 0)
                totals[combo[i]] += sum(len(versions[k]['points']) for k in seen[ci].values())
            self.reference[i] = max(totals, key=totals.get)

        def reference_combo(combo):
            return tuple(self.reference.get(i, v) for i, v in enumerate(combo))

        index_of = {c: i for i, c in enumerate(combos)}
        # Plants: groups of reference combos with the same render meshes; merge groups sharing a mesh.
        group_of_combo, parent = {}, {}

        def find(g):
            while parent[g] != g:
                parent[g] = parent[parent[g]]
                g = parent[g]
            return g

        keys = {}
        for ci, combo in enumerate(combos):
            ref = index_of[reference_combo(combo)]
            render = frozenset(k for k in seen[ref].values() if not versions[k]['proxy']) or frozenset(seen[ref].values())
            g = keys.setdefault(render, len(keys))
            parent.setdefault(g, g)
            group_of_combo[ci] = g
        owner = {}
        for ci in range(len(combos)):
            for key in seen[ci].values():
                g = find(group_of_combo[ci])
                if key in owner and find(owner[key]) != g:
                    parent[find(owner[key])] = g
                owner.setdefault(key, g)
        self.group_of_key = {k: find(g) for k, g in owner.items()}
        groups = sorted(set(self.group_of_key.values()))
        self.references = {g: [] for g in groups}
        for render, g in keys.items():
            for k in render:
                if k not in self.references[find(g)]:
                    self.references[find(g)].append(k)
        # Where each mesh version is authored: {path: [(set index or None, [variant names], key)]}
        self.sites = {}
        by_path = {}
        for ci, combo in enumerate(combos):
            for path, key in seen[ci].items():
                by_path.setdefault(path, []).append((combo, key))
        for path, entries in by_path.items():
            distinct = {k for _, k in entries}
            if len(distinct) == 1:
                self.sites[path] = [(None, None, entries[0][1])]
                continue
            for i in sorted(range(len(sets)), key=lambda i: not is_lod_set(sets[i])):
                mapping = {}
                if all(mapping.setdefault(c[i], k) == k for c, k in entries):
                    self.sites[path] = [(i, [v for v, k2 in mapping.items() if k2 == k], k) for k in distinct]
                    break
            else:
                raise ValueError('%s changes with more than one variant set' % path)

    def group_names(self):
        """A readable, stable name per plant (its first render mesh's name)."""
        names, used = {}, set()
        for g, keys in sorted(self.references.items()):
            base = Path(self.versions[keys[0]]['path']).name if keys else 'plant'
            name = ''.join(c if c.isalnum() or c == '_' else '_' for c in base) or 'plant'
            if name[0].isdigit():
                name = '_' + name
            unique, n = name, 1
            while unique in used:
                n += 1
                unique = '%s_%d' % (name, n)
            used.add(unique)
            names[g] = unique
        return names


def common_parent(paths, top):
    from pxr import Sdf
    parts = [Sdf.Path(p).GetPrefixes() for p in paths]
    common = top.GetPath()
    for level in zip(*parts):
        if all(x == level[0] for x in level) and level[0].HasPrefix(top.GetPath()):
            common = level[0]
        else:
            break
    return common


def generate(source, output, strength=STRENGTH, loop_seconds=LOOP_SECONDS, verbose=False):
    from pxr import Usd, UsdGeom, UsdSkel, Sdf, Gf, Vt
    started = time.time()
    log = (lambda *a: print(*a, flush=True)) if verbose else (lambda *a: None)
    source, output = Path(source), Path(output)
    stage = Usd.Stage.Open(str(source), Usd.Stage.LoadAll)
    top = stage.GetDefaultPrim() or next(iter(stage.GetPseudoRoot().GetChildren()), None)
    if not top:
        return {'skipped': 'No top prim'}
    up = UsdGeom.GetStageUpAxis(stage)
    meters = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    fps = stage.GetTimeCodesPerSecond() or 24.0
    frames = max(24, int(round(loop_seconds * fps)))
    to_work = up_matrix(up)
    sets, combos, seen, versions = mesh_versions(stage, top)
    if not versions:
        return {'skipped': 'No meshes'}
    plan = Plan(sets, combos, seen, versions)
    names = plan.group_names()
    log('sets', sets, 'combos', len(combos), 'mesh versions', len(versions), 'plants', len(names), '%.1fs' % (time.time() - started))
    skel_root = common_parent([v['path'] for v in versions.values()], top)
    skel_root_is_top = skel_root == top.GetPath()
    # Rig and animate each plant.
    rigs, weights, anims = {}, {}, {}
    for g, keys in plan.references.items():
        pts, a_list, b_list, offset, spans = [], [], [], 0, {}
        for k in keys:
            v = versions[k]
            p = v['points'] @ v['matrix'][:3, :3] + v['matrix'][3, :3]
            a, b = mesh_edges(v['counts'], v['indices'])
            pts.append(p @ to_work.T)
            a_list.append(a + offset)
            b_list.append(b + offset)
            spans[k] = (offset, offset + len(p))
            offset += len(p)
        points = np.concatenate(pts)
        t0 = time.time()
        rig = Rig(points, np.concatenate(a_list), np.concatenate(b_list), meters)
        seed = int(hashlib.sha1((source.stem + '/' + names[g]).encode()).hexdigest()[:8], 16)
        rot = animate(rig, seed, frames, fps, strength)
        rigs[g] = rig
        anims[g] = rot
        for k, (lo, hi) in spans.items():
            weights[k] = (rig.index[lo:hi], rig.weight[lo:hi])
        log('plant', names[g], 'points', len(points), 'pieces', len(rig.pieces), 'joints', len(rig.position),
            'loose', rig.loose_points, '%.1fs' % (time.time() - t0))
    # Other LODs and proxies: nearest point of the plant's reference meshes.
    for key, v in versions.items():
        if key in weights:
            continue
        g = plan.group_of_key[key]
        rig = rigs[g]
        p = (v['points'] @ v['matrix'][:3, :3] + v['matrix'][3, :3]) @ to_work.T
        near = nearest(rig.points, p)
        weights[key] = (rig.index[near], rig.weight[near])
    # Checks on the skinning maths before writing anything.
    checks = check(plan, rigs, anims, weights, versions, to_work, frames)
    log('checks', checks)
    # ---- write
    entry_temp, clip_temp = temp_paths(output)
    clip_final = clip_path(output)
    clip_temp.parent.mkdir(parents=True, exist_ok=True)
    for path in (entry_temp, clip_temp):
        if path.exists():
            path.unlink()
    back = to_work.T   # working space -> stage space
    top_name = output.stem
    root_path = Sdf.Path('/' + top_name)
    rel_skel_root = skel_root.MakeRelativePath(top.GetPath())
    skel_root_path = root_path if skel_root_is_top else root_path.AppendPath(rel_skel_root)
    scope_path = skel_root_path.AppendChild(SCOPE)

    def remap(path):
        return root_path.AppendPath(Sdf.Path(path).MakeRelativePath(top.GetPath()))

    # Clip: rotations per plant, frames 0..frames (the last equals the first).
    clip_layer = Sdf.Layer.CreateNew(str(clip_temp))
    clip_stage = Usd.Stage.Open(clip_layer)
    clip_stage.SetTimeCodesPerSecond(fps)
    clip_stage.SetStartTimeCode(0)
    clip_stage.SetEndTimeCode(frames)
    for g, rot in anims.items():
        anim = UsdSkel.Animation.Define(clip_stage, Sdf.Path('/' + SCOPE).AppendChild(names[g] + '_anim'))
        attr = anim.CreateRotationsAttr()
        q = quaternions(np.einsum('ij,fnj->fni', back, rot)).astype(np.float32)
        for f in range(frames + 1):
            attr.Set(Vt.QuatfArray.FromNumpy(q[f][:, [1, 2, 3, 0]]) if hasattr(Vt.QuatfArray, 'FromNumpy')
                     else Vt.QuatfArray([Gf.Quatf(float(w), Gf.Vec3f(float(x), float(y), float(z))) for w, x, y, z in q[f]]), f)
    clip_layer.Save()
    # Entry.
    layer = Sdf.Layer.CreateNew(str(entry_temp))
    st = Usd.Stage.Open(layer)
    UsdGeom.SetStageUpAxis(st, up)
    UsdGeom.SetStageMetersPerUnit(st, meters)
    st.SetTimeCodesPerSecond(fps)
    st.SetFramesPerSecond(stage.GetFramesPerSecond() or fps)
    st.SetStartTimeCode(1)
    st.SetEndTimeCode(frames + 1)
    layer.customLayerData = {'nanakusa_wind': {'source': source.name, 'strength': float(strength),
                                               'loop_frames': frames, 'generator': 'wind_gen'}}
    root = st.DefinePrim(root_path)
    st.SetDefaultPrim(root)
    try:
        relative = os.path.relpath(source, output.parent).replace(os.sep, '/')
        relative = relative if relative.startswith('..') else './' + relative
    except ValueError:   # another drive
        relative = source.resolve().as_posix()
    root.GetReferences().AddReference(relative)
    if skel_root_is_top:
        root.SetTypeName('SkelRoot')
    else:
        st.DefinePrim(skel_root_path, 'SkelRoot')
    scope = UsdGeom.Scope.Define(st, scope_path)
    UsdGeom.Imageable(scope).CreatePurposeAttr(UsdGeom.Tokens.guide)
    skeleton_path = {}
    for g, rig in rigs.items():
        joints = rig.joint_names()
        skel = UsdSkel.Skeleton.Define(st, scope_path.AppendChild(names[g]))
        skel.CreateJointsAttr(joints)
        # Houdini's Scene View draws a Skeleton's bones (hundreds of lines per plant) even as a guide;
        # hidden, it still drives the skinning in the viewport and in Karma (checked 22.0.447).
        UsdGeom.Imageable(skel).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
        world = rig.position @ back.T / 1.0
        local = world - np.where(rig.parent[:, None] >= 0, world[np.maximum(rig.parent, 0)], 0)
        skel.CreateBindTransformsAttr(Vt.Matrix4dArray([Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(*map(float, p))) for p in world]))
        skel.CreateRestTransformsAttr(Vt.Matrix4dArray([Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(*map(float, p))) for p in local]))
        anim = UsdSkel.Animation.Define(st, scope_path.AppendChild(names[g] + '_anim'))
        anim.CreateJointsAttr(joints)
        anim.CreateTranslationsAttr(Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in local]))
        anim.CreateScalesAttr(Vt.Vec3hArray([Gf.Vec3h(1, 1, 1)] * len(joints)))
        # Declared without a value: a default here would win over the clip's samples.
        anim.GetPrim().CreateAttribute('rotations', Sdf.ValueTypeNames.QuatfArray)
        UsdSkel.BindingAPI.Apply(skel.GetPrim()).CreateAnimationSourceRel().SetTargets([anim.GetPath()])
        skeleton_path[g] = skel.GetPath()
    # Bindings, authored where each mesh version lives (inside a LOD variant if its points depend on it).
    top_sets = root.GetVariantSets()
    bound = 0
    for path, sites in plan.sites.items():
        for set_index, variants, key in sites:
            index, weight = weights[key]
            v = versions[key]
            g = plan.group_of_key[key]
            contexts = [None] if set_index is None else variants

            def author():
                prim = st.OverridePrim(remap(path))
                binding = UsdSkel.BindingAPI.Apply(prim)
                binding.CreateSkeletonRel().SetTargets([skeleton_path[g]])
                binding.CreateJointIndicesPrimvar(False, INFLUENCES).Set(Vt.IntArray.FromNumpy(index.astype(np.int32).ravel()))
                binding.CreateJointWeightsPrimvar(False, INFLUENCES).Set(Vt.FloatArray.FromNumpy(weight.astype(np.float32).ravel()))
                binding.CreateGeomBindTransformAttr(Gf.Matrix4d(*v['matrix'].ravel().tolist()))

            for variant in contexts:
                if variant is None:
                    author()
                else:
                    vset = top_sets.AddVariantSet(sets[set_index])
                    vset.AddVariant(variant)
                    vset.SetVariantSelection(variant)
                    with vset.GetVariantEditContext():
                        author()
            bound += 1
    # AddVariant/SetVariantSelection above authored selections in this layer: keep the source's choice.
    for name in top_sets.GetNames():
        spec = layer.GetPrimAtPath(root_path)
        if spec and name in spec.variantSelections:
            del spec.variantSelections[name]
    loop_clip(st, root_path, scope_path, clip_temp.name, frames)
    layer.Save()
    # Verify: composes, skinned meshes resolve, rotations vary over time and loop.
    verify = verify_stage(entry_temp, root_path, scope_path, names, frames)
    # Point the clip at its final name now the check is done.
    final = Usd.Stage.Open(str(entry_temp))
    loop_clip(final, root_path, scope_path, clip_final.name, frames)
    final.GetRootLayer().Save()
    return {'wind': {'plants': {names[g]: {'joints': int(len(r.position)), 'pieces': len(r.pieces), 'loose_points': int(r.loose_points)}
                                for g, r in rigs.items()},
                     'meshes': bound, 'frames': frames, 'fps': fps, 'strength': float(strength),
                     'skel_root': str(skel_root_path), 'note': SKEL_ROOT_FALLBACK_NOTE if skel_root_is_top else '',
                     'checks': checks, 'verify': verify, 'seconds': round(time.time() - started, 1)},
            'entry_temp': str(entry_temp), 'clip_temp': str(clip_temp),
            'entry': str(output), 'clip': str(clip_final)}


def loop_clip(stage, root_path, scope_path, clip_name, frames):
    """Loop the clip over (almost) any frame - stage [k*L + o, (k+1)*L + o] -> clip [0, L] - inside
    each variant of the "wind_phase" set (o = 0, L/4, L/2, 3L/4). Copies of one plant placed with
    different phases sway out of step (a Point Instancer gives each prototype one motion). Authored
    only in the variants: a clip set outside them would be stronger than all of them."""
    from pxr import Usd, Sdf, Gf, Vt
    vset = stage.GetPrimAtPath(root_path).GetVariantSets().AddVariantSet(PHASE_SET)
    first, last = LOOP_REPEAT
    for i in range(PHASES):
        name = 'phase_%d' % i
        offset = frames * i / PHASES
        vset.AddVariant(name)
        vset.SetVariantSelection(name)
        with vset.GetVariantEditContext():
            clips = Usd.ClipsAPI(stage.OverridePrim(scope_path))
            clips.SetClipAssetPaths([Sdf.AssetPath('./' + CLIP_FOLDER + '/' + clip_name)])
            clips.SetClipPrimPath('/' + SCOPE)
            clips.SetClipActive(Vt.Vec2dArray([Gf.Vec2d(first * frames + offset, 0)]))
            times = []
            for k in range(first, last):
                times += [Gf.Vec2d(k * frames + offset, 0), Gf.Vec2d((k + 1) * frames + offset, frames)]
            clips.SetClipTimes(Vt.Vec2dArray(times))
    vset.SetVariantSelection('phase_0')


def check(plan, rigs, anims, weights, versions, to_work, frames):
    """Skin every reference mesh at a few frames with numpy and measure what matters: how far points
    move, whether edges stretch (tearing where weights disagree) and whether anything sinks below
    the plant's base."""
    result = {}
    for g, keys in plan.references.items():
        rig, rot = rigs[g], anims[g]
        worst_stretch, max_move, sink = 0.0, 0.0, 0.0
        for k in keys:
            v = versions[k]
            p = ((v['points'] @ v['matrix'][:3, :3] + v['matrix'][3, :3]) @ to_work.T)
            a, b = mesh_edges(v['counts'], v['indices'])
            rest = np.linalg.norm(p[a] - p[b], axis=1)
            base = p[:, 1].min()
            idx, w = weights[k]
            for f in np.linspace(0, frames - 1, 7).astype(int):
                q = skin(rig, rot[f], p, idx, w)
                edge = np.linalg.norm(q[a] - q[b], axis=1)
                # Absolute change of edge length (fraction of the plant height): tearing shows as
                # a gap, while a big relative change of a sub-millimetre edge does not show at all.
                worst_stretch = max(worst_stretch, float(np.abs(edge - rest).max()) / rig.height)
                max_move = max(max_move, float(np.linalg.norm(q - p, axis=1).max()))
                sink = max(sink, float(base - q[:, 1].min()))
        loop_gap = float(np.abs(rot[0] - rot[-1]).max())
        result[str(g)] = {'stretch': round(worst_stretch, 4), 'max_move': round(max_move / rig.height, 3),
                          'sink': round(sink / rig.height, 4), 'loop_gap': loop_gap}
    return result


def verify_stage(entry, root_path, scope_path, names, frames):
    from pxr import Usd, UsdSkel
    stage = Usd.Stage.Open(str(entry))
    errors = [str(e) for e in stage.GetCompositionErrors()]
    anim = UsdSkel.Animation(stage.GetPrimAtPath(scope_path.AppendChild(next(iter(names.values())) + '_anim')))
    rotations = anim.GetRotationsAttr()
    a, b, c = rotations.Get(3), rotations.Get(3 + frames), rotations.Get(1003)
    if a is None or not rotations.ValueMightBeTimeVarying():
        raise RuntimeError('the animation clip does not resolve')
    if list(a) != list(b):
        raise RuntimeError('the clip does not loop')
    cache = UsdSkel.Cache()
    roots = [p for p in stage.Traverse() if p.IsA(UsdSkel.Root)]
    skinned = 0
    for r in roots:
        cache.Populate(UsdSkel.Root(r), Usd.PrimDefaultPredicate)
        for binding in cache.ComputeSkelBindings(UsdSkel.Root(r), Usd.PrimDefaultPredicate):
            skinned += len(binding.GetSkinningTargets())
    if not skinned:
        raise RuntimeError('no mesh is skinned')
    return {'composition_errors': errors[:5], 'skinned_meshes_in_default_selection': skinned}


def main():
    source, output, result_path = sys.argv[1:4]
    strength = float(sys.argv[4]) if len(sys.argv) > 4 else STRENGTH
    loop_seconds = float(sys.argv[5]) if len(sys.argv) > 5 else LOOP_SECONDS
    try:
        result = generate(source, output, strength, loop_seconds)
    except ValueError as exc:
        result = {'skipped': str(exc)}
    Path(result_path).write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    main()
