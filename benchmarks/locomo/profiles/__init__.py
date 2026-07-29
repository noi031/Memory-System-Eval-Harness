"""LoCoMo evaluation profiles."""

from .schema import ProfileSettings, ProfileSpec
from .vikingbot_historical import (
    AGENT_PLUGIN,
    HISTORICAL_PROFILE,
    HISTORICAL_PROFILE_COMMIT,
    HISTORICAL_PROMPT_COMMIT,
    HISTORICAL_REFERENCE,
    HISTORICAL_SOURCE,
    HISTORICAL_SETTINGS,
    default_vikingbot_workspace,
)
from .vikingbot_v2 import (
    V2_ALIGNED_COMMIT,
    V2_ALIGNED_PROFILE,
    V2_ALIGNED_REFERENCE,
    V2_ALIGNED_SETTINGS,
    V2_ALIGNED_SOURCE,
)
from .legacy77 import (
    LEGACY_77_PROFILE,
    LEGACY_77_REFERENCE,
    LEGACY_77_SETTINGS,
    LEGACY_77_SOURCE,
)
from .vikingboat0411 import (
    VIKINGBOAT_0411_PROFILE,
    VIKINGBOAT_0411_REFERENCE,
    VIKINGBOAT_0411_SETTINGS,
    VIKINGBOAT_0411_SOURCE,
)
from .vikingboat0411_natural_no_tools import (
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_REFERENCE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_SETTINGS,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_SOURCE,
)


PROFILE_SETTINGS = {
    LEGACY_77_PROFILE: LEGACY_77_SETTINGS,
    HISTORICAL_PROFILE: HISTORICAL_SETTINGS,
    V2_ALIGNED_PROFILE: V2_ALIGNED_SETTINGS,
    VIKINGBOAT_0411_PROFILE: VIKINGBOAT_0411_SETTINGS,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE: (
        VIKINGBOAT_0411_NATURAL_NO_TOOLS_SETTINGS
    ),
}
PROFILE_SOURCES = {
    LEGACY_77_PROFILE: LEGACY_77_SOURCE,
    HISTORICAL_PROFILE: HISTORICAL_SOURCE,
    V2_ALIGNED_PROFILE: V2_ALIGNED_SOURCE,
    VIKINGBOAT_0411_PROFILE: VIKINGBOAT_0411_SOURCE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE: (
        VIKINGBOAT_0411_NATURAL_NO_TOOLS_SOURCE
    ),
}
PROFILE_REFERENCES = {
    LEGACY_77_PROFILE: LEGACY_77_REFERENCE,
    HISTORICAL_PROFILE: HISTORICAL_REFERENCE,
    V2_ALIGNED_PROFILE: V2_ALIGNED_REFERENCE,
    VIKINGBOAT_0411_PROFILE: VIKINGBOAT_0411_REFERENCE,
    VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE: (
        VIKINGBOAT_0411_NATURAL_NO_TOOLS_REFERENCE
    ),
}

PROFILE_SPECS = {
    name: ProfileSpec(
        name=name,
        reference=PROFILE_REFERENCES[name],
        source=PROFILE_SOURCES[name],
        settings=ProfileSettings.from_mapping(settings),
    )
    for name, settings in PROFILE_SETTINGS.items()
}


def profile_settings(profile: str):
    if profile == "one-shot":
        return dict(HISTORICAL_SETTINGS)
    try:
        return PROFILE_SPECS[profile].settings.as_dict()
    except KeyError as exc:
        raise ValueError(f"unknown LoCoMo QA profile: {profile}") from exc


def profile_spec(profile: str) -> ProfileSpec:
    if profile == "one-shot":
        return ProfileSpec(
            name=profile,
            reference=HISTORICAL_REFERENCE,
            source=HISTORICAL_SOURCE,
            settings=ProfileSettings.from_mapping(HISTORICAL_SETTINGS),
        )
    try:
        return PROFILE_SPECS[profile]
    except KeyError as exc:
        raise ValueError(f"unknown LoCoMo QA profile: {profile}") from exc


def profile_source(profile: str):
    return PROFILE_SOURCES.get(profile, {})


def profile_reference(profile: str) -> str:
    return PROFILE_REFERENCES.get(profile, "")

__all__ = [
    "AGENT_PLUGIN",
    "HISTORICAL_PROFILE",
    "HISTORICAL_PROFILE_COMMIT",
    "HISTORICAL_PROMPT_COMMIT",
    "HISTORICAL_REFERENCE",
    "HISTORICAL_SOURCE",
    "HISTORICAL_SETTINGS",
    "LEGACY_77_PROFILE",
    "LEGACY_77_REFERENCE",
    "LEGACY_77_SETTINGS",
    "LEGACY_77_SOURCE",
    "PROFILE_REFERENCES",
    "PROFILE_SPECS",
    "PROFILE_SETTINGS",
    "PROFILE_SOURCES",
    "ProfileSettings",
    "ProfileSpec",
    "V2_ALIGNED_COMMIT",
    "V2_ALIGNED_PROFILE",
    "V2_ALIGNED_REFERENCE",
    "V2_ALIGNED_SETTINGS",
    "V2_ALIGNED_SOURCE",
    "VIKINGBOAT_0411_PROFILE",
    "VIKINGBOAT_0411_REFERENCE",
    "VIKINGBOAT_0411_SETTINGS",
    "VIKINGBOAT_0411_SOURCE",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_PROFILE",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_REFERENCE",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_SETTINGS",
    "VIKINGBOAT_0411_NATURAL_NO_TOOLS_SOURCE",
    "default_vikingbot_workspace",
    "profile_reference",
    "profile_spec",
    "profile_settings",
    "profile_source",
]
