"""Shared utilities for CodeSuture."""

import os
import json
from typing import List

_STORE_CANDIDATES = [
    ".codesuture_cache", ".codesuture_store",
    ".codesuture", "codesuture_patches",
]

def find_patch_store(explicit_path: str = None) -> str | None:
    """Locate the patch store directory.

    If *explicit_path* is given and exists, use that.
    Otherwise probe the default candidates in order.
    Returns ``None`` if nothing is found.
    """
    if explicit_path and os.path.exists(explicit_path):
        return explicit_path
    for c in _STORE_CANDIDATES:
        if os.path.exists(c):
            return c
    return None

def load_all_patches(store_path: str) -> List[dict]:
    """Load every patch metadata JSON from *store_path*.

    Handles both directory trees (walk + collect ``.json`` files)
    and a single JSON file containing a list or dict of patches.
    """
    patches: list[dict] = []
    if os.path.isdir(store_path):
        for root, _dirs, files in os.walk(store_path):
            for fname in files:
                fpath = os.path.join(root, fname)
                if fname.endswith(".json") and os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                patches.extend(data)
                            elif isinstance(data, dict):
                                patches.append(data)
                    except Exception:
                        pass
    elif os.path.isfile(store_path):
        try:
            with open(store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    patches = data
                elif isinstance(data, dict):
                    patches = list(data.values())
        except Exception:
            pass
    return patches
