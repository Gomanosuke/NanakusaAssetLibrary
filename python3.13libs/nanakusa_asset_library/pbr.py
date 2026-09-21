"""Conservative filename-based grouping for dropped PBR images."""
from functools import lru_cache
from pathlib import Path
import re

ALIASES = {
    'base_color': ('base_color','basecolor','albedo','diffuse','diff','color','col'),
    'roughness': ('roughness','rough','rgh'),
    'metalness': ('metalness','metallic','metal'),
    'normal': ('normal_opengl','normal_directx','normal_dx','normaldx','nor_dx','normal_gl','normalgl','nor_gl','normal','nrm'),
    'opacity': ('opacity','alpha'),
    'displacement': ('displacement','height','disp'),
    'emission': ('emission','emissive'),
    'ao': ('ambient_occlusion','ambientocclusion','occlusion','ao'),
}

_PATTERNS = [(channel, alias, re.compile(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z])'))
             for channel, aliases in ALIASES.items() for alias in aliases]


@lru_cache(maxsize=200000)
def _find(name):
    """(channel, start, end) of the channel token; the last one wins ('Metal_Plate_normal' is a normal map).

    Cached: the list view classifies every image on each refresh. The substring test skips
    almost every regular expression.
    """
    best=None
    for channel,alias,pattern in _PATTERNS:
        if alias not in name:continue
        for match in pattern.finditer(name):
            if best is None or (match.end(),match.end()-match.start())>(best[2],best[2]-best[1]):
                best=(channel,match.start(),match.end())
    return best

def identify(path):
    p=Path(path);name=p.stem.lower()
    found=_find(name)
    if found:
        channel,start,end=found
        key=(name[:start]+'_'+name[end:]).strip('_.- ')
        key=re.sub(r'(^|[_. -])(\d+k|\d{3,5}x\d{3,5}|100[1-9])(?=$|[_. -])','_',key)
        key=re.sub(r'[_. -]+','_',key).strip('_') or p.parent.name
        return channel,key
    return None,p.stem

def group_images(paths):
    groups={}
    for source in paths:
        path=Path(source)
        channel,key=identify(path)
        if channel=='normal' and re.search(r'(normaldx|(^|[_ .-])(dx|directx))($|[_ .-])',path.stem.lower()):
            raise ValueError('Use OpenGL normal maps; DirectX normals need conversion: '+path.name)
        # Unrecognized images remain independent base-color materials.
        identity=(str(path.parent.resolve()),key) if channel else (str(path.resolve()),'single')
        group=groups.setdefault(identity,{'label':key,'maps':{}})
        channel=channel or 'base_color'
        if channel in group['maps']:
            raise ValueError('Ambiguous '+channel+' maps in '+key+'. Select one resolution/tile per channel.')
        group['maps'][channel]=path.resolve().as_posix()
    return list(groups.values())


def _split(path):
    stem=Path(path).stem
    found=_find(stem.lower())
    return (found[0],stem,found) if found else (None,stem,None)

@lru_cache(maxsize=200000)
def stack_info(relpath):
    """(folder, set key, display label, channel) of an image that belongs to a PBR set, else None.

    Unlike identify(), the resolution stays in the key so 1K and 2K sets are separate stacks.
    """
    channel,stem,match=_split(relpath)
    if channel is None:return None
    clean=lambda text:re.sub(r'[_. -]+','_',text).strip('_')
    label=clean(stem[:match[1]]+'_'+stem[match[2]:]) or Path(relpath).parent.name
    return Path(relpath).parent.as_posix().lower(),label.lower(),label,channel

def stack_entries(rows,enabled=True):
    """Group texture rows that are channels of one PBR set into stacks.

    Returns entries in list order: {'rows': [...], 'rep': row, 'label': str, 'channels': [...]}.
    A stack needs two or more images with distinct channels; anything else stays single.
    """
    groups={};order=[]
    for row in rows:
        info=stack_info(row['relpath']) if enabled and row['kind']=='texture' else None
        key=(row['root_id'],)+info[:2] if info else ('single',row['id'])
        if key not in groups:groups[key]=[];order.append(key)
        groups[key].append((row,info))
    entries=[]
    for key in order:
        members=groups[key]
        channels=[info[3] for row,info in members if info]
        if key[0]!='single' and len(members)>=2 and len(set(channels))==len(channels):
            rep=next((row for row,info in members if info[3]=='base_color'),members[0][0])
            ordered=sorted(members,key=lambda m:list(ALIASES).index(m[1][3]))
            entries.append({'rows':[row for row,info in ordered],'rep':rep,'label':members[0][1][2],'channels':[info[3] for row,info in ordered]})
        else:
            entries.extend({'rows':[row],'rep':row,'label':row['label'],'channels':[]} for row,info in members)
    return entries
