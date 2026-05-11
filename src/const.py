"""Dyson product-type capability map.

Different Dyson models expose different feature sets. Rather than branch
on every model code in the entity layer, we declare per-model capability
flags once and let the entity exposers ask `caps.has_heat`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Pure Cool (cool-only fan/purifier)
COOL_TYPES = {"438", "438K", "438E", "438M", "455", "469", "475", "520"}
# Pure Hot+Cool (adds heat)
HOT_COOL_TYPES = {"527", "527E", "527K", "455"}
# Pure Humidify+Cool (adds humidifier)
HUMIDIFY_TYPES = {"358", "358E", "358K"}
# Big+Quiet (jet axial fan purifier)
BIG_QUIET_TYPES = {"664"}
# Formaldehyde-capable variants (subset — TP09/HP09/PH04 etc.)
HCHO_TYPES = {"438K", "438M", "527K", "358K"}


@dataclass(frozen=True)
class DeviceCaps:
    has_heat: bool
    has_humidifier: bool
    has_hcho: bool
    has_oscillation: bool
    has_air_quality: bool


def caps_for(product_type: str) -> DeviceCaps:
    return DeviceCaps(
        has_heat=product_type in HOT_COOL_TYPES,
        has_humidifier=product_type in HUMIDIFY_TYPES,
        has_hcho=product_type in HCHO_TYPES,
        has_oscillation=True,
        has_air_quality=product_type not in {"455", "469", "475", "520"},
    )


# Oscillation angle presets surfaced as a Select entity. Each value is the
# total sweep span centered on 180° (with the 350° preset clamped to 5-355
# to stay within the firmware's allowed range).
OSC_OFF = "Off"
OSC_PRESETS = (OSC_OFF, "45°", "90°", "180°", "350°")

OSC_PRESET_RANGES: dict[str, tuple[int, int]] = {
    "45°": (158, 202),
    "90°": (135, 225),
    "180°": (90, 270),
    "350°": (5, 355),
}


def osc_preset_from_angles(low: int | None, high: int | None, on: bool) -> str:
    """Bucket the device's reported low/high oscillation angles into the
    nearest preset label. Used to populate the Select entity's CURRENT_OPTION."""
    if not on or low is None or high is None:
        return OSC_OFF
    span = high - low
    best = min(OSC_PRESET_RANGES.items(), key=lambda kv: abs((kv[1][1] - kv[1][0]) - span))
    return best[0]


# Direction = the centre point of the oscillation arc (where the fan
# 'faces'). 12 angle steps every 30°, approximating a dial as closely as
# the UC remote's discrete-choice widget allows.
DIRECTION_PRESETS: tuple[str, ...] = tuple(f"{a}°" for a in range(0, 360, 30))
DIRECTION_TO_CENTRE: dict[str, int] = {f"{a}°": a for a in range(0, 360, 30)}


def direction_from_centre(centre: int) -> str:
    """Bucket the device's reported centre angle into the closest 30° step.
    Used for displaying CURRENT_OPTION on the Direction picker."""
    best = min(DIRECTION_TO_CENTRE.items(), key=lambda kv: abs(kv[1] - centre))
    return best[0]


def compose_oscillation(centre: int, span: int) -> tuple[int, int]:
    """Build a (low, high) pair centred on `centre` with total width `span`,
    clamped into the firmware's allowed 0..350 range."""
    half = span // 2
    low = max(0, centre - half)
    high = min(350, centre + half)
    # If clamping shifted one edge, slide the other to preserve span.
    if high - low < span and low == 0:
        high = min(350, span)
    elif high - low < span and high == 350:
        low = max(0, 350 - span)
    return low, high
