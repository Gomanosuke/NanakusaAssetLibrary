"""Scan one library root in a separate process.

A scan of tens of thousands of files is pure Python work; running it inside Houdini would compete
with the panel for the interpreter lock. This script is started with the Python that ships with
Houdini (no license needed) and only touches the index database.
    scan_worker.py <data dir> <root id>
"""
from pathlib import Path
import json
import sys


def main(data_dir, root_id):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from nanakusa_asset_library.core import Library
    try:
        result = Library(data_dir).scan(root_id)
    except Exception as exc:
        result = {'cancelled': False, 'count': 0, 'errors': [str(exc)]}
    print('NAL_SCAN:' + json.dumps(result))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
