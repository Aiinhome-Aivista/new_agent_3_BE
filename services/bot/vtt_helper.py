import re
from dataclasses import dataclass, asdict
from typing import List, Optional

@dataclass
class VTTSegment:
    start_time: str
    end_time: str
    start_seconds: float
    end_seconds: float
    speaker: str
    text: str

    def to_dict(self):
        return asdict(self)

def format_seconds_to_time(seconds: float) -> str:
    """Formats seconds into MM:SS or HH:MM:SS string."""
    seconds = int(round(seconds))
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

