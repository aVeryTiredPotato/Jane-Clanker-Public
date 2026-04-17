from __future__ import annotations

from discord import Member, PermissionOverwrite
from discord.ext import commands

from features.staff.voiceChat.overwriteUtils import (
    addMemberOverwrite,
    buildConfiguredRoleOverwrites,
    makeOverwrite,
)

_LRperms = makeOverwrite(
    view_channel=True,
    connect=False,
    send_messages=False,
    mute_members=False,
    deafen_members=False,
    priority_speaker=False,
    move_members=False,
    mention_everyone=False,
    add_reactions=False,
    use_external_emojis=False,
    attach_files=False,
    embed_links=False,
    external_stickers=False,
    manage_messages=False,
    create_events=False,
    manage_events=False,
    use_soundboard=False,
    use_application_commands=False,
    use_external_apps=False,
    use_embedded_activities=False,
    stream=False,
)

_MRperms = makeOverwrite(
    view_channel=True,
    connect=True,
    send_messages=True,
    mute_members=True,
    deafen_members=True,
    priority_speaker=True,
    move_members=True,
    add_reactions=True,
    use_external_emojis=True,
    attach_files=True,
    embed_links=True,
    external_stickers=True,
    manage_messages=True,
    use_soundboard=True,
    stream=True,
    speak=True
)

_cohostPerms = makeOverwrite(
    priority_speaker=True,
)

_FormerPerms = makeOverwrite(
    view_channel=True,
    connect=True,
    send_messages=True,
)

_HRperms = makeOverwrite(
    manage_channels=True,
    use_external_emojis=True,
    use_voice_activation=True,
    priority_speaker=True,
    bypass_slowmode=True,
    view_channel=True,
    connect=True,
    send_messages=True,
    mute_members=True,
    deafen_members=True,
    move_members=True,
    add_reactions=True,
    attach_files=True,
    embed_links=True,
    external_stickers=True,
    manage_messages=True,
    use_soundboard=True,
    stream=True,
)

_everyonePerms = makeOverwrite(
    view_channel=False,
    connect=False,
    send_messages=False,
)

_diplomatPerms = makeOverwrite(
    view_channel=True,
    connect=False,
    send_messages=False,
    speak=False,
)

_NMPerms = _diplomatPerms

_NAperms = makeOverwrite(
    view_channel=False,
    connect=False,
    send_messages=False,
)

_NAMSPerms = makeOverwrite(
    view_channel=True,
    connect=True,
    send_messages=True,
    speak=True,
    mute_members=True,
    deafen_members=True,
    priority_speaker=True,
    use_application_commands=True,
    use_external_apps=True,
    use_soundboard=True,
    attach_files=True,
    embed_links=True,
    move_members=True,
)

_SUPERVISOR_ROLE_OVERWRITES: dict[int, PermissionOverwrite] = {
    1373417102115078215: _everyonePerms,
    1374142815109386331: _NAperms,
    1376949707053731851: _LRperms,
    1373714234893926500: _NMPerms,
    1375442086358417489: _diplomatPerms,
    1376949984750206986: _MRperms,
    1399386519256563793: _FormerPerms,
    1376949919100698814: _HRperms,
    1373432879446491207: _NMPerms,
    1441631068951416946: _NAMSPerms,
}


def getSupervisorCommsPerms(
    bot: commands.Bot,
    cohost1: Member | int | None = None,
    cohost2: Member | int | None = None,
) -> dict[object, PermissionOverwrite]:
    actualOverwrites, guild = buildConfiguredRoleOverwrites(
        bot=bot,
        configuredOverwrites=_SUPERVISOR_ROLE_OVERWRITES,
    )
    if guild is None:
        return actualOverwrites

    addMemberOverwrite(
        actualOverwrites,
        guild=guild,
        memberOrId=cohost1,
        overwrite=_cohostPerms,
    )
    addMemberOverwrite(
        actualOverwrites,
        guild=guild,
        memberOrId=cohost2,
        overwrite=_cohostPerms,
    )
    return actualOverwrites

