"""Show files in the system file browser with the files themselves selected (no Houdini dependency)."""
from pathlib import Path
import os
import subprocess

WINDOWS = os.name == 'nt'


def group_by_folder(paths):
    """{folder: [files]} in first-seen order, duplicates dropped."""
    groups = {}
    for path in paths:
        path = Path(path)
        files = groups.setdefault(path.parent, [])
        if path not in files:
            files.append(path)
    return groups


def _select_with_shell(folder, files):
    """Windows: one Explorer window on `folder` with every file in `files` selected
    (SHOpenFolderAndSelectItems; `explorer /select,` can select only one). True on success."""
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.WinDLL('shell32')   # own instances: never change argtypes on the shared ctypes.windll
    ole32 = ctypes.WinDLL('ole32')
    shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                           wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    def parse(path):
        pidl = ctypes.c_void_p()
        if shell32.SHParseDisplayName(str(path), None, ctypes.byref(pidl), 0, None) != 0:
            return None
        return pidl

    # Houdini's UI thread normally has COM already (S_FALSE, or RPC_E_CHANGED_MODE for another
    # apartment - the shell call still works); only undo an initialisation made here.
    started = ole32.CoInitializeEx(None, 0x2) in (0, 1)   # COINIT_APARTMENTTHREADED
    pidls = []
    try:
        folder_pidl = parse(folder)
        if folder_pidl is None:
            return False
        pidls.append(folder_pidl)
        items = [p for p in (parse(f) for f in files) if p is not None]
        pidls.extend(items)
        if not items:
            return False
        array = (ctypes.c_void_p * len(items))(*[p.value for p in items])
        return shell32.SHOpenFolderAndSelectItems(folder_pidl, len(items), array, 0) == 0
    finally:
        for pidl in pidls:
            ole32.CoTaskMemFree(pidl)
        if started:
            ole32.CoUninitialize()


def select_in_file_browser(paths, open_folder):
    """Open the folder of every path with those files selected. `open_folder(folder)` is the
    fallback (and the only way off Windows): it just opens the folder."""
    for folder, files in group_by_folder(paths).items():
        if WINDOWS:
            try:
                if _select_with_shell(folder, files):
                    continue
            except Exception:
                pass
            try:
                # Explorer parses its own command line: the path must be quoted after the comma.
                subprocess.Popen('explorer /select,"%s"' % files[0])
                continue
            except OSError:
                pass
        open_folder(folder)
