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

def group_entries(members):
    """List entries for the rows of one candidate stack (already in list order).

    Two or more images with distinct, recognised channels become a stack whose representative is the
    base color image; anything else stays single. Returns {'rows','rep','label','channels'} entries.
    """
    infos=[stack_info(m['relpath']) for m in members]
    channels=[i[3] for i in infos if i]
    if len(members)>=2 and all(infos) and len(set(channels))==len(channels):
        order=list(ALIASES)
        paired=sorted(zip(members,infos),key=lambda p:order.index(p[1][3]))
        rep=next((m for m,i in paired if i[3]=='base_color'),paired[0][0])
        return [{'rows':[m for m,i in paired],'rep':rep,'label':infos[0][2],'channels':[i[3] for m,i in paired]}]
    return [{'rows':[m],'rep':m,'label':m['label'],'channels':[]} for m in members]
