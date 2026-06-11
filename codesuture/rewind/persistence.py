"""
codesuture.rewind.persistence
Save and load rewind crash-timeline dumps to/from disk as JSON.

Each dump is written to the ``.codesuture_rewind/`` directory so it
survives process restarts and can be reviewed offline.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional


REWIND_DIR: str = '.codesuture_rewind'


def save_rewind_dump(
    func_name: str,
    snapshots: list,
    crash_info: Optional[dict] = None,
) -> str:
    """Persist a list of snapshots (or dicts) to a JSON file.

    Parameters
    ----------
    func_name:
        Qualified name of the function that crashed.
    snapshots:
        Iterable of :class:`~codesuture.rewind.buffer.FrameSnapshot`
        objects **or** plain dicts (``to_dict()`` output).
    crash_info:
        Optional metadata about the crash (exception type, message, etc.).

    Returns
    -------
    str
        Absolute path of the file that was written.
    """
    os.makedirs(REWIND_DIR, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    safe_name = func_name.replace('.', '_').replace('<', '').replace('>', '')
    filename = f'rewind_{safe_name}_{timestamp}.json'
    filepath = os.path.join(REWIND_DIR, filename)

    data: dict = {
        'function': func_name,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'crash_info': crash_info or {},
        'timeline': [
            s.to_dict() if hasattr(s, 'to_dict') else s for s in snapshots
        ],
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)

    return filepath


def load_latest_rewind(func_name: Optional[str] = None) -> Optional[dict]:
    """Load the most recent rewind dump, optionally filtered by function name.

    Returns ``None`` when no matching dump exists.
    """
    if not os.path.isdir(REWIND_DIR):
        return None

    files = sorted(
        [
            f
            for f in os.listdir(REWIND_DIR)
            if f.startswith('rewind_') and f.endswith('.json')
        ],
        reverse=True,
    )

    for fname in files:
        filepath = os.path.join(REWIND_DIR, fname)
        try:
            with open(filepath, encoding='utf-8') as f:
                data = json.load(f)
            if func_name is None or func_name in data.get('function', ''):
                return data
        except (json.JSONDecodeError, OSError):
            continue

    return None


def list_rewind_dumps() -> List[dict]:
    """Return metadata for every saved rewind dump (newest first)."""
    if not os.path.isdir(REWIND_DIR):
        return []

    results: List[dict] = []
    for fname in sorted(os.listdir(REWIND_DIR), reverse=True):
        if fname.startswith('rewind_') and fname.endswith('.json'):
            filepath = os.path.join(REWIND_DIR, fname)
            try:
                with open(filepath, encoding='utf-8') as f:
                    data = json.load(f)
                results.append({
                    'file': fname,
                    'function': data.get('function', '?'),
                    'timestamp': data.get('timestamp', '?'),
                    'events': len(data.get('timeline', [])),
                })
            except Exception:
                continue

    return results
