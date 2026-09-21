"""Conservative filename-based grouping for dropped PBR images."""
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

def identify(path):
    p=Path(path);name=p.stem.lower()
    for channel,aliases in ALIASES.items():
        for alias in aliases:
            match=re.search(r'(?<![a-z0-9])'+re.escape(alias)+r'(?![a-z])',name)
            if match:
                key=(name[:match.start()]+'_'+name[match.end():]).strip('_.- ')
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
