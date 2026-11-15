"""Selection of the numbered SPHEREx wide surveys."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal

Epoch = Literal["1", "2", "3", "4", "all"]
DR1 = "spherex_dr1"
QR3 = "spherex_qr3"
WIDE_COLLECTIONS = (DR1, QR3)
SPECTRAL_COLLECTIONS = (*WIDE_COLLECTIONS, *(f"{release}_deep" for release in WIDE_COLLECTIONS))


@dataclass(frozen=True)
class TimeWindow:
    """Inclusive ObsCore time bounds, in UTC MJD."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if not (isfinite(self.start) and isfinite(self.end) and self.start <= self.end):
            raise ValueError("Expected finite ordered MJD bounds.")


@dataclass(frozen=True)
class Survey:
    collection: str
    pointing_angle_deg: float
    window: TimeWindow | None = None

    def includes(self, collection: str, angle: float | None, mjd: float) -> bool:
        if collection != self.collection or angle is None:
            return False
        if self.window and not self.window.start <= mjd <= self.window.end:
            return False
        difference = (angle - self.pointing_angle_deg + 180) % 360 - 180
        return abs(difference) < 1e-4


# DR1 covers Surveys 1–2 and QR3 covers 3–4. The planned ecliptic position
# angle distinguishes the two passes in each pair.
SURVEYS = {
    "1": Survey(DR1, 0),
    "2": Survey(DR1, 180),
    "3": Survey(QR3, 0),
    "4": Survey(QR3, 180),
}


def configure_surveys(raw_windows: str) -> Mapping[str, Survey]:
    """Apply optional inclusive UTC MJD bounds to the published survey mapping."""
    try:
        windows = json.loads(raw_windows)
        if not isinstance(windows, dict):
            raise ValueError
        surveys = SURVEYS.copy()
        for epoch, bounds in windows.items():
            if epoch not in surveys or not isinstance(bounds, list) or len(bounds) != 2:
                raise ValueError
            if any(type(value) not in (int, float) for value in bounds):
                raise ValueError
            surveys[epoch] = replace(surveys[epoch], window=TimeWindow(*bounds))
        return surveys
    except (ValueError, OverflowError) as error:
        raise ValueError(
            'STARDRIFT_EPOCH_WINDOWS must map epochs "1" through "4" to ordered UTC MJD pairs.'
        ) from error
