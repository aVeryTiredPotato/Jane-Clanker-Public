from __future__ import annotations

import logging
import re
from collections import OrderedDict

import discord
from discord import app_commands

import config
from runtime import permissions as runtimePermissions

_kebabCasePattern = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def isKebabCase(value: str) -> bool:
    return bool(_kebabCasePattern.fullmatch(str(value or "").strip()))


def collectUserVisibleCommandNamingViolations(tree: app_commands.CommandTree) -> list[str]:
    violations: list[str] = []

    def _walk(command: app_commands.Command | app_commands.Group, prefixParts: list[str]) -> None:
        commandName = str(getattr(command, "name", "") or "").strip()
        pathParts = prefixParts + ([commandName] if commandName else [])
        commandPath = "/" + " ".join(pathParts).strip()

        if commandName and not isKebabCase(commandName):
            violations.append(f"{commandPath}: command name '{commandName}' is not kebab-case")

        parameters = getattr(command, "parameters", None) or []
        for parameter in parameters:
            optionName = str(getattr(parameter, "display_name", None) or getattr(parameter, "name", "") or "").strip()
            if optionName and not isKebabCase(optionName):
                violations.append(f"{commandPath}: option name '{optionName}' is not kebab-case")

        childCommands = getattr(command, "commands", None)
        if childCommands:
            for child in childCommands:
                _walk(child, pathParts)

    rootCommands = tree.get_commands(type=discord.AppCommandType.chat_input)
    for rootCommand in rootCommands:
        _walk(rootCommand, [])
    return violations


def logUserVisibleCommandNamingSanity(tree: app_commands.CommandTree) -> None:
    violations = collectUserVisibleCommandNamingViolations(tree)
    if not violations:
        return

    preview = "\n".join(f"- {line}" for line in violations[:12])
    if len(violations) > 12:
        preview += f"\n- ...and {len(violations) - 12} more."
    logging.warning(
        "Command naming sanity: %d issue(s) found.\n%s",
        len(violations),
        preview,
    )


def collectSlashHelpEntries(
    tree: app_commands.CommandTree,
    *,
    guild: discord.abc.Snowflake | None = None,
) -> list[tuple[str, str]]:
    entriesByPath: OrderedDict[str, str] = OrderedDict()

    def _walk(
        command: app_commands.Command | app_commands.Group,
        prefixParts: list[str],
    ) -> None:
        commandName = str(getattr(command, "name", "") or "").strip()
        if not commandName:
            return
        pathParts = prefixParts + [commandName]
        childCommands = getattr(command, "commands", None)
        if childCommands:
            for child in childCommands:
                _walk(child, pathParts)
            return

        path = "/" + " ".join(pathParts)
        description = str(getattr(command, "description", "") or "").strip() or "No description."
        entriesByPath[path] = description

    for rootCommand in tree.get_commands(type=discord.AppCommandType.chat_input):
        _walk(rootCommand, [])

    if guild is not None:
        for guildCommand in tree.get_commands(guild=guild, type=discord.AppCommandType.chat_input):
            _walk(guildCommand, [])

    entries = list(entriesByPath.items())
    entries.sort(key=lambda row: row[0].lower())
    return entries


def slashPermissionHint(path: str) -> str:
    normalizedPath = str(path or "").strip().lower()

    instructorRoleId = runtimePermissions.toPositiveInt(getattr(config, "instructorRoleId", 0))
    recruiterRoleId = runtimePermissions.toPositiveInt(getattr(config, "recruiterRoleId", 0))
    bgModRoleId = runtimePermissions.toPositiveInt(getattr(config, "moderatorRoleId", 0))
    mrRoleId = runtimePermissions.toPositiveInt(getattr(config, "middleRankRoleId", 0))
    hrRoleId = runtimePermissions.toPositiveInt(getattr(config, "highRankRoleId", 0))
    cohostRoles = runtimePermissions.normalizeRoleIds(getattr(config, "cohostAllowedRoleIds", []))
    appAdminRoles = runtimePermissions.normalizeRoleIds(getattr(config, "divisionApplicationsAdminRoleIds", []))
    appGlobalReviewerRoles = runtimePermissions.normalizeRoleIds(getattr(config, "divisionApplicationsGlobalReviewerRoleIds", []))
    ribbonManagerRoles = runtimePermissions.normalizeRoleIds(getattr(config, "ribbonManagerRoleIds", []))
    anrdSubmitterRoles = runtimePermissions.normalizeRoleIds(getattr(config, "anrdPaymentSubmitterRoleIds", []))
    divisionClockinRoles = runtimePermissions.normalizeRoleIds(getattr(config, "divisionClockinAllowedRoleIds", []))
    orbatSubmitterRoles = runtimePermissions.normalizeRoleIds(getattr(config, "orbatSubmitterRoleIds", []))
    orbatReviewerRoles = runtimePermissions.normalizeRoleIds(getattr(config, "orbatReviewerRoleIds", []))
    groupPatrolHostRoles = runtimePermissions.normalizeRoleIds(getattr(config, "recruitmentPatrolGroupHostRoleIds", []))
    voiceChatAllRoles = runtimePermissions.normalizeRoleIds(getattr(config, "_canCreateVoiceChatAll", []))
    voiceChatBasicRoles = runtimePermissions.normalizeRoleIds(getattr(config, "_canCreateVoiceChatBasic", []))
    projectHodRoles = runtimePermissions.normalizeRoleIds(getattr(config, "projectHodRoleIds", []))
    projectAssistantDirectorRoles = runtimePermissions.normalizeRoleIds(getattr(config, "projectAssistantDirectorRoleIds", []))
    allowPublicOrbatLookup = bool(getattr(config, "allowPublicOrbatLookup", False))

    applicationPanelRoles = list(appAdminRoles)
    for roleId in appGlobalReviewerRoles:
        if roleId not in applicationPanelRoles:
            applicationPanelRoles.append(roleId)
    if hrRoleId > 0 and hrRoleId not in applicationPanelRoles:
        applicationPanelRoles.append(hrRoleId)

    hints: dict[str, str] = {
        "/orientation": (
            f"Instructor role required ({runtimePermissions.formatRoleIds([instructorRoleId] if instructorRoleId > 0 else [])})."
        ),
        "/cohost": f"Cohost roles required ({runtimePermissions.formatRoleIds(cohostRoles)}).",
        "/recruitment": (
            f"Recruiter role required ({runtimePermissions.formatRoleIds([recruiterRoleId] if recruiterRoleId > 0 else [])})."
        ),
        "/recruitment-patrol": (
            "Solo: recruiter role "
            f"({runtimePermissions.formatRoleIds([recruiterRoleId] if recruiterRoleId > 0 else [])}). "
            "Group: host roles "
            f"({runtimePermissions.formatRoleIds(groupPatrolHostRoles)})"
            + (" with recruiter fallback." if groupPatrolHostRoles else ".")
        ),
        "/bg-check": (
            "BG-certified roles required "
            f"({runtimePermissions.formatRoleIds(sorted(runtimePermissions.getBgCheckCertifiedRoleIds()))})."
        ),
        "/bg-flag": f"Moderator role required ({runtimePermissions.formatRoleIds([bgModRoleId] if bgModRoleId > 0 else [])}).",
        "/orbat-request": (
            "Requires ORBAT submitter or reviewer roles "
            f"({runtimePermissions.formatRoleIds(orbatSubmitterRoles + [roleId for roleId in orbatReviewerRoles if roleId not in orbatSubmitterRoles])})."
        ),
        "/orbat": (
            "Public lookup enabled." if allowPublicOrbatLookup else
            f"Reviewer roles required ({runtimePermissions.formatRoleIds(orbatReviewerRoles)})."
        ),
        "/orbat-pending": f"Reviewer roles required ({runtimePermissions.formatRoleIds(orbatReviewerRoles)}).",
        "/loa-request": (
            "Requires ORBAT submitter or reviewer roles "
            f"({runtimePermissions.formatRoleIds(orbatSubmitterRoles + [roleId for roleId in orbatReviewerRoles if roleId not in orbatSubmitterRoles])})."
        ),
        "/applications": (
            "Application admins, global reviewers, or HR roles "
            f"({runtimePermissions.formatRoleIds(applicationPanelRoles)}), with admin/manage-server bypass."
        ),
        "/applications-hub-post": f"Application admin roles required ({runtimePermissions.formatRoleIds(appAdminRoles)}).",
        "/applications-hub-post-all": f"Application admin roles required ({runtimePermissions.formatRoleIds(appAdminRoles)}).",
        "/apps pending": (
            "Application admins or global reviewers "
            f"({runtimePermissions.formatRoleIds(appAdminRoles + [roleId for roleId in appGlobalReviewerRoles if roleId not in appAdminRoles])})."
        ),
        "/apps stats": (
            "Application admins or global reviewers "
            f"({runtimePermissions.formatRoleIds(appAdminRoles + [roleId for roleId in appGlobalReviewerRoles if roleId not in appAdminRoles])})."
        ),
        "/apps reopen": (
            "Application admins or global reviewers "
            f"({runtimePermissions.formatRoleIds(appAdminRoles + [roleId for roleId in appGlobalReviewerRoles if roleId not in appAdminRoles])})."
        ),
        "/apps force-approve": f"Application admin roles required ({runtimePermissions.formatRoleIds(appAdminRoles)}).",
        "/ribbon status": f"Ribbon manager roles required ({runtimePermissions.formatRoleIds(ribbonManagerRoles)}).",
        "/ribbon assets": f"Ribbon manager roles required ({runtimePermissions.formatRoleIds(ribbonManagerRoles)}).",
        "/ribbon sync-catalog": f"Ribbon manager roles required ({runtimePermissions.formatRoleIds(ribbonManagerRoles)}).",
        "/ribbon render": f"Ribbon manager roles required ({runtimePermissions.formatRoleIds(ribbonManagerRoles)}).",
        "/ribbons request": "Open to all users (cooldown applies for non-managers).",
        "/ribbons profile": "Self profile is open; viewing other users requires ribbon manager roles.",
        "/request-payment": (
            "ANRD payment submitter roles required "
            f"({runtimePermissions.formatRoleIds(anrdSubmitterRoles)}), with admin/manage-server bypass."
        ),
        "/division-clockin": (
            "Division clock-in roles required "
            f"({runtimePermissions.formatRoleIds(divisionClockinRoles)}), with admin/manage-server bypass."
        ),
        "/project create": "Open to members in project-enabled servers.",
        "/project list": "Open to members in project-enabled servers.",
        "/project status": "Open to members in project-enabled servers.",
        "/project submit": "Project creator only, with HOD/admin override.",
        "/project approve": (
            "Project HOD roles required "
            f"({runtimePermissions.formatRoleIds(projectHodRoles)}), with admin/manage-server bypass."
        ),
        "/project deny": (
            "Project HOD roles required "
            f"({runtimePermissions.formatRoleIds(projectHodRoles)}), with admin/manage-server bypass."
        ),
        "/project finalize": (
            "Project Assistant Director roles required "
            f"({runtimePermissions.formatRoleIds(projectAssistantDirectorRoles)}), with admin/manage-server bypass."
        ),
        "/create-voice-chat": (
            "Shift/Supervisor comms require all-access voice roles "
            f"({runtimePermissions.formatRoleIds(voiceChatAllRoles)}); "
            "Gamenight/Breakroom require basic voice roles "
            f"({runtimePermissions.formatRoleIds(voiceChatBasicRoles)})."
        ),
        "/delete-voice-chat": (
            "Requires permission for the selected managed voice-chat type; all-access roles can delete all types."
        ),
        "/clean-voice-chats": (
            "All-access voice roles required "
            f"({runtimePermissions.formatRoleIds(voiceChatAllRoles)})."
        ),
        "/schedule-event": (
            "MR/HR roles required "
            f"({runtimePermissions.formatRoleIds([roleId for roleId in [mrRoleId, hrRoleId] if roleId > 0])}) "
            "or administrator/manage-server."
        ),
        "/events": "Open to everyone in recognized servers.",
        "/user-info": "Open to everyone in recognized servers.",
        "/server-info": "Open to everyone in recognized servers.",
        "/server-stats": "Open to everyone in recognized servers.",
        "/poll": (
            "MR/HR roles required "
            f"({runtimePermissions.formatRoleIds([roleId for roleId in [mrRoleId, hrRoleId] if roleId > 0])}) "
            "or administrator/manage-server."
        ),
        "/reminder add": "Open to everyone in recognized servers.",
        "/reminder team": (
            "MR/HR roles required "
            f"({runtimePermissions.formatRoleIds([roleId for roleId in [mrRoleId, hrRoleId] if roleId > 0])}) "
            "or administrator/manage-server."
        ),
        "/reminder list": "Open to everyone in recognized servers.",
        "/reminder cancel": "Open to everyone; administrators/manage-server may cancel others.",
        "/suggestion submit": "Open to everyone in recognized servers.",
        "/suggestion list": "Open to everyone in recognized servers.",
        "/suggestion status-board": (
            "Suggestion reviewer roles "
            f"({runtimePermissions.formatRoleIds(runtimePermissions.normalizeRoleIds(getattr(config, 'suggestionReviewerRoleIds', [])))}) "
            "or administrator/manage-server."
        ),
        "/notes-add": "Administrator/manage-server only. Test server only.",
        "/notes-list": "Administrator/manage-server only. Test server only.",
        "/notes-delete": "Administrator/manage-server only. Test server only.",
        "/federation-link": "Administrator/manage-server only. Test server only.",
        "/federation-unlink": "Administrator/manage-server only.",
        "/federation-list": "Administrator/manage-server only.",
        "/post-role-menu": "Administrator/manage-server only.",
        "/ops": "Configured ops allowlist only.",
        "/snapshot-menu": "Administrator/manage-server plus configured snapshot allowlist.",
        "/quarantine": "Administrator/manage-server plus configured recovery allowlist.",
        "/pause": "Configured runtime-control allowlist only.",
        "/restart": "Configured runtime-control allowlist only.",
        "/best-of": "Administrator only.",
        "/archive": "Administrator only.",
        "/curfew": "Administrator only.",
        "/jail": "Administrator only.",
        "/unjail": "Administrator only.",
        "/gambling": (
            "MR/HR roles required "
            f"({runtimePermissions.formatRoleIds([roleId for roleId in [mrRoleId, hrRoleId] if roleId > 0])}); "
            "casino category lock may also apply."
        ),
    }
    return hints.get(normalizedPath, "Permission varies by command checks.")


def hiddenCommandHelpEntries() -> list[tuple[str, str, str]]:
    cohostRoles = runtimePermissions.normalizeRoleIds(getattr(config, "cohostAllowedRoleIds", []))
    bgRoles = sorted(runtimePermissions.getBgCheckCertifiedRoleIds())
    appControlRoles = runtimePermissions.normalizeRoleIds(getattr(config, "divisionApplicationsControlRoleIds", []))
    appAdminRoles = runtimePermissions.normalizeRoleIds(getattr(config, "divisionApplicationsAdminRoleIds", []))
    mrRoleId = runtimePermissions.toPositiveInt(getattr(config, "middleRankRoleId", 0))
    hrRoleId = runtimePermissions.toPositiveInt(getattr(config, "highRankRoleId", 0))

    return [
        (
            ":)help",
            "Show all available Jane commands and required permissions.",
            "Open to everyone.",
        ),
        (
            "?janeRuntime",
            "Show runtime diagnostics (ping, uptime, task states, process resources).",
            (
                "Guild owner, manage-server, administrator, or cohost roles "
                f"({runtimePermissions.formatRoleIds(cohostRoles)})."
            ),
        ),
        (
            "!janeTerminal",
            "Show a read-only terminal-style runtime snapshot.",
            "Configured terminal user only.",
        ),
        (
            "?bgleaderboard / ?bg-leaderboard",
            "Show BG reviewer approval/rejection leaderboard.",
            f"BG-certified roles required ({runtimePermissions.formatRoleIds(bgRoles)}).",
        ),
        (
            "?trainingstats / ?hoststats [@user|userId]",
            "Show tracked training/orientation host stats.",
            "Open to everyone in recognized servers.",
        ),
        (
            "!skin <user>",
            "Apply the skin nickname joke command.",
            f"Cohost roles required ({runtimePermissions.formatRoleIds(cohostRoles)}).",
        ),
        (
            "!kill <user>",
            "Schedule a fake reactor-themed execution message.",
            f"MR/HR roles required ({runtimePermissions.formatRoleIds([roleId for roleId in [mrRoleId, hrRoleId] if roleId > 0])}).",
        ),
        (
            "!casinotoggle [on|off]",
            "Toggle gambling category-lock enforcement at runtime.",
            "Administrator only.",
        ),
        (
            "!applications <divisionKey> <open|close|status>",
            "Open/close application state for a division and refresh hub cards.",
            (
                "Application control roles "
                f"({runtimePermissions.formatRoleIds(appControlRoles + [roleId for roleId in appAdminRoles if roleId not in appControlRoles])}) "
                "or administrator/manage-server."
            ),
        ),
        (
            "!copyserver",
            "Copy the configured source server snapshot into the current server.",
            "Lead-dev copyserver allowlist only.",
        ),
        (
            "!allowserver",
            "Add the current server to Jane's allowed command guild list.",
            "Configured ops allowlist only.",
        ),
        (
            "!mirrortraininghistory",
            "Run the training history mirror backfill once.",
            "Configured ops allowlist only.",
        ),
        (
            "!shutdown",
            "Close Jane's bot process cleanly.",
            "Configured ops allowlist only.",
        ),
        (
            "?perm-sim / ?permsim /command [@user]",
            "Hidden permission simulator (test-server scoped).",
            "Administrator/manage-server in configured test guild only.",
        ),
    ]


def chunkHelpLines(lines: list[str], maxChars: int = 3500) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        piece = line + "\n"
        if len(current) + len(piece) > maxChars and current:
            chunks.append(current.rstrip())
            current = piece
            continue
        current += piece
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


_HELP_SECTION_DEFS: list[tuple[str, str, str]] = [
    ("overview", "Overview", "High-level guide to Jane's command sections."),
    ("general", "General", "Info lookups, polls, reminders, suggestions, and utility commands."),
    ("sessions", "Sessions", "Training, hosting, scheduled events, and session-adjacent commands."),
    ("recruitment", "Recruitment & ORBAT", "Recruitment actions, ORBAT tools, LOAs, and division clock-ins."),
    ("bg", "Background Checks", "Background-check flags, queue tools, and reviewer utilities."),
    ("applications", "Applications", "Division application posting, review operations, and hub controls."),
    ("awards", "Awards & Payments", "Ribbon workflows and ANRD payment processing."),
    ("moderation", "Moderation & Safety", "Archive, quarantine, curfew, jail, and recovery tooling."),
    ("misc", "Misc & Experimental", "Project, voice chat, ops, and uncategorized slash commands."),
    ("hidden", "Hidden / Text", "Prefix and hidden Jane commands."),
]


def _sectionMeta(sectionKey: str) -> tuple[str, str]:
    for key, title, description in _HELP_SECTION_DEFS:
        if key == sectionKey:
            return title, description
    return sectionKey.title(), ""


def _categorizeSlashPath(path: str) -> str:
    normalized = str(path or "").strip().lower()
    if normalized.startswith((
        "/user-info",
        "/server-info",
        "/server-stats",
        "/poll ",
        "/poll",
        "/reminder ",
        "/reminder",
        "/suggestion ",
        "/suggestion",
        "/post-role-menu",
    )):
        return "general"
    if normalized.startswith(("/orientation", "/cohost", "/schedule-event", "/events", "/best-of")):
        return "sessions"
    if normalized.startswith(("/recruitment", "/recruitment-patrol", "/orbat-request", "/orbat-pending", "/orbat", "/loa-request", "/division-clockin")):
        return "recruitment"
    if normalized.startswith(("/bg-check", "/bg-flag")):
        return "bg"
    if normalized.startswith(("/applications", "/applications-hub-post", "/applications-hub-post-all", "/apps ", "/apps")):
        return "applications"
    if normalized.startswith(("/ribbon ", "/ribbon", "/ribbons ", "/ribbons", "/request-payment")):
        return "awards"
    if normalized.startswith((
        "/snapshot-menu",
        "/quarantine",
        "/pause",
        "/restart",
        "/archive",
        "/curfew",
        "/jail",
        "/unjail",
        "/notes-",
        "/federation-",
    )):
        return "moderation"
    return "misc"


def buildHelpSections(
    tree: app_commands.CommandTree,
    *,
    guild: discord.abc.Snowflake | None = None,
) -> list[dict[str, object]]:
    orderedSections: "OrderedDict[str, dict[str, object]]" = OrderedDict()
    for sectionKey, title, description in _HELP_SECTION_DEFS:
        orderedSections[sectionKey] = {
            "key": sectionKey,
            "title": title,
            "description": description,
            "items": [],
        }

    for path, description in collectSlashHelpEntries(tree, guild=guild):
        sectionKey = _categorizeSlashPath(path)
        section = orderedSections.setdefault(
            sectionKey,
            {"key": sectionKey, "title": sectionKey.title(), "description": "", "items": []},
        )
        section["items"].append(
            {
                "name": path,
                "description": description,
                "permission": slashPermissionHint(path),
            }
        )

    hiddenSection = orderedSections["hidden"]
    for trigger, description, permissionText in hiddenCommandHelpEntries():
        hiddenSection["items"].append(
            {
                "name": trigger,
                "description": description,
                "permission": permissionText,
            }
        )

    overviewItems: list[dict[str, object]] = []
    for sectionKey, title, description in _HELP_SECTION_DEFS:
        if sectionKey == "overview":
            continue
        section = orderedSections.get(sectionKey) or {}
        items = section.get("items") or []
        if not items:
            continue
        overviewItems.append(
            {
                "name": title,
                "description": f"{description} ({len(items)} command{'s' if len(items) != 1 else ''})",
                "permission": "Use the section menu below to open this category.",
            }
        )
    orderedSections["overview"]["items"] = overviewItems

    out: list[dict[str, object]] = []
    for sectionKey, _, _ in _HELP_SECTION_DEFS:
        section = orderedSections.get(sectionKey)
        if not section:
            continue
        items = list(section.get("items") or [])
        if sectionKey != "overview" and not items:
            continue
        items.sort(key=lambda row: str(row.get("name") or "").lower())
        out.append(
            {
                "key": sectionKey,
                "title": section.get("title") or _sectionMeta(sectionKey)[0],
                "description": section.get("description") or _sectionMeta(sectionKey)[1],
                "items": items,
            }
        )
    return out


def buildHelpSectionEmbed(section: dict[str, object], *, currentIndex: int, totalSections: int) -> discord.Embed:
    title = str(section.get("title") or "Jane Help").strip() or "Jane Help"
    description = str(section.get("description") or "").strip()
    items = list(section.get("items") or [])

    embed = discord.Embed(
        title=f"Jane Help - {title}",
        description=description or "Use the section picker below to browse Jane's commands.",
        color=discord.Color.blurple(),
    )

    if not items:
        embed.add_field(name="Commands", value="(none)", inline=False)
    else:
        for item in items[:25]:
            name = str(item.get("name") or "Command").strip() or "Command"
            commandDescription = str(item.get("description") or "").strip() or "No description."
            permission = str(item.get("permission") or "").strip() or "Permission varies by command checks."
            embed.add_field(
                name=name[:256],
                value=f"{commandDescription}\n`Perm:` {permission}"[:1024],
                inline=False,
            )

    embed.set_footer(text=f"Section {currentIndex}/{totalSections}")
    return embed
