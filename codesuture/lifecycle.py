"""Patch lifecycle state machine.

Tracks each patch through its full lifecycle from detection to permanent fix.
Each state transition is logged, and engineers can query patches by state.
"""

import os
import json
import threading
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum


class PatchState(Enum):
    DETECTED = "detected"       # Exception caught, analyzing
    PATCHED = "patched"         # Bytecode guard applied in memory
    REPLAYED = "replayed"       # HTTP transaction replayed
    PERSISTED = "persisted"     # Patch saved to disk
    SUGGESTED = "suggested"     # Fix suggestion generated
    VERIFIED = "verified"       # Suggestion verified in sandbox
    FIXED = "fixed"             # Engineer applied permanent fix
    EXPIRED = "expired"         # TTL exceeded
    ROLLED_BACK = "rolled_back" # Engineer rolled back the patch


@dataclass
class PatchLifecycle:
    """Tracks a single patch's lifecycle."""
    patch_id: str = ""          # func_module:func_name:guard_type
    function_name: str = ""
    module: str = ""
    guard_type: str = ""
    current_state: PatchState = PatchState.DETECTED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ttl_days: int = 7
    transitions: List[Dict] = field(default_factory=list)

    def transition_to(self, new_state: PatchState, reason: str = ""):
        """Record a state transition."""
        now = datetime.now(timezone.utc).isoformat()
        self.transitions.append({
            'from': self.current_state.value,
            'to': new_state.value,
            'at': now,
            'reason': reason,
        })
        self.current_state = new_state
        self.updated_at = now

    def is_expired(self) -> bool:
        """Check if the patch has exceeded its TTL."""
        try:
            created = datetime.fromisoformat(self.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - created).days >= self.ttl_days
        except Exception:
            return False

    def age_days(self) -> int:
        """Return age in days."""
        try:
            created = datetime.fromisoformat(self.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - created).days
        except Exception:
            return 0

    def to_dict(self) -> dict:
        d = {}
        for f in self.__dataclass_fields__:
            val = getattr(self, f)
            if isinstance(val, Enum):
                d[f] = val.value
            else:
                d[f] = val
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'PatchLifecycle':
        if 'current_state' in data and isinstance(data['current_state'], str):
            data['current_state'] = PatchState(data['current_state'])
        known = {f.name for f in __import__('dataclasses').fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class LifecycleManager:
    """Manages lifecycle state for all patches."""

    def __init__(self, store_dir: str = '.codesuture_incidents'):
        self.store_dir = store_dir
        self._lifecycle_file = os.path.join(store_dir, 'lifecycle.json')
        self._lock = threading.Lock()
        self._patches: Dict[str, PatchLifecycle] = {}
        os.makedirs(store_dir, exist_ok=True)
        self._load()

    def _load(self):
        """Load lifecycle state from disk."""
        if os.path.isfile(self._lifecycle_file):
            try:
                with open(self._lifecycle_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for pid, pdata in data.items():
                    self._patches[pid] = PatchLifecycle.from_dict(pdata)
            except Exception:
                pass

    def _save(self):
        """Persist lifecycle state to disk."""
        data = {pid: p.to_dict() for pid, p in self._patches.items()}
        with open(self._lifecycle_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)

    def _make_id(self, module: str, function: str, guard_type: str) -> str:
        return f"{module}:{function}:{guard_type}"

    def track(self, module: str, function: str, guard_type: str,
              state: PatchState = PatchState.PATCHED, ttl_days: int = 7,
              reason: str = "") -> PatchLifecycle:
        """Track a patch lifecycle event."""
        pid = self._make_id(module, function, guard_type)
        with self._lock:
            if pid not in self._patches:
                patch = PatchLifecycle(
                    patch_id=pid,
                    function_name=function,
                    module=module,
                    guard_type=guard_type,
                    current_state=PatchState.DETECTED,
                    ttl_days=ttl_days,
                )
                patch.transition_to(state, reason)
                self._patches[pid] = patch
            else:
                patch = self._patches[pid]
                if patch.current_state != state:
                    patch.transition_to(state, reason)
            self._save()
            return patch

    def get(self, patch_id: str) -> Optional[PatchLifecycle]:
        return self._patches.get(patch_id)

    def get_by_state(self, state: PatchState) -> List[PatchLifecycle]:
        return [p for p in self._patches.values() if p.current_state == state]

    def get_by_function(self, function: str) -> List[PatchLifecycle]:
        return [p for p in self._patches.values()
                if function.lower() in p.function_name.lower()]

    def get_stale(self, days: int = 5) -> List[PatchLifecycle]:
        """Find patches PERSISTED for more than N days without a FIXED transition."""
        results = []
        for p in self._patches.values():
            if p.current_state in (PatchState.PERSISTED, PatchState.SUGGESTED,
                                   PatchState.PATCHED):
                if p.age_days() > days:
                    results.append(p)
        return results

    def get_expired(self) -> List[PatchLifecycle]:
        """Find patches that have exceeded their TTL."""
        return [p for p in self._patches.values() if p.is_expired()]

    def mark_fixed(self, function: str, reason: str = "Permanent fix applied") -> bool:
        """Mark a function's patches as FIXED."""
        found = False
        with self._lock:
            for p in self._patches.values():
                if function.lower() in p.function_name.lower():
                    p.transition_to(PatchState.FIXED, reason)
                    found = True
            if found:
                self._save()
        return found

    def mark_rolled_back(self, function: str, reason: str = "Manual rollback") -> bool:
        """Mark a function's patches as ROLLED_BACK."""
        found = False
        with self._lock:
            for p in self._patches.values():
                if function.lower() in p.function_name.lower():
                    p.transition_to(PatchState.ROLLED_BACK, reason)
                    found = True
            if found:
                self._save()
        return found

    def get_all(self) -> List[PatchLifecycle]:
        return list(self._patches.values())

    def summary(self) -> Dict[str, int]:
        """Count patches by state."""
        counts = {s.value: 0 for s in PatchState}
        for p in self._patches.values():
            counts[p.current_state.value] += 1
        counts['total'] = len(self._patches)
        return counts
