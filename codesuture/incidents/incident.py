from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from enum import Enum
import uuid


class Severity(Enum):
    CRITICAL = "CRITICAL"  # Callable replaced, security-sensitive, data integrity
    HIGH = "HIGH"          # HTTP transaction replayed, side effects possible
    MEDIUM = "MEDIUM"      # Standard guard applied, safe default
    LOW = "LOW"            # Cached fingerprint hit, known pattern


class IncidentStatus(Enum):
    PATCHED = "patched"           # Guard applied, running with default
    REPLAYED = "replayed"         # HTTP transaction replayed successfully
    REWOUND = "rewound"           # Frame rewound, re-executing
    FALLBACK = "fallback"         # Fallback response sent (500 JSON)
    SKIPPED = "skipped"           # Could not safely patch
    PERSISTED = "persisted"       # Patch saved to disk
    EXPIRED = "expired"           # TTL exceeded
    FIXED = "fixed"               # Engineer applied permanent fix


@dataclass
class IncidentRecord:
    # Identity
    incident_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    fingerprint: str = ""

    # Crash context
    exception_type: str = ""
    exception_message: str = ""
    module: str = ""
    function: str = ""
    line_number: int = 0
    file_path: str = ""
    stack_trace: List[str] = field(default_factory=list)

    # Patch details
    severity: Severity = Severity.MEDIUM
    status: IncidentStatus = IncidentStatus.PATCHED
    guard_type: str = ""
    target_variable: str = ""
    default_value: Any = None
    default_rationale: str = ""
    bytecode_diff: Dict[str, int] = field(default_factory=dict)

    # Engineer guidance
    suggested_fix: Optional[str] = None
    fix_confidence: Optional[str] = None
    review_priority: str = "NORMAL"

    # Metadata
    python_version: str = field(default_factory=lambda: f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}.{__import__('sys').version_info.micro}")
    codesuture_version: str = ""
    ttl_days: int = 7
    hit_count: int = 1
    thread_name: str = ""

    # Links
    related_incidents: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict."""
        d = {}
        for f in self.__dataclass_fields__:
            val = getattr(self, f)
            if isinstance(val, Enum):
                d[f] = val.value
            else:
                d[f] = val
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'IncidentRecord':
        """Deserialize from dict, converting enums back."""
        if 'severity' in data and isinstance(data['severity'], str):
            data['severity'] = Severity(data['severity'])
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = IncidentStatus(data['status'])
        # Only pass known fields
        known = {f.name for f in __import__('dataclasses').fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)
