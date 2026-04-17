from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

import discord

import config
from features.staff.recruitment import rendering as recruitmentRendering
from features.staff.recruitment import service as recruitmentService
from features.staff.recruitment import sheets as recruitmentSheets
from runtime import orgProfiles
from runtime import taskBudgeter
from features.staff.sessions import roblox
from features.staff.sessions import service as sessionService

log = logging.getLogger(__name__)

GetChannelFn = Callable[[discord.Client, int], Awaitable[Optional[object]]]
DmUserFn = Callable[[discord.Client, int, str], Awaitable[bool]]
NotifyModsFn = Callable[[discord.Client, str], Awaitable[None]]
GroupUrlProviderFn = Callable[[], str]


async def updateRecruitmentSubmissionMessage(
    bot: discord.Client,
    submission: dict,
    *,
    getChannel: GetChannelFn,
) -> None:
    channelId = submission.get("channelId") or getattr(config, "recruitmentChannelId", None)
    messageId = submission.get("messageId")
    if not channelId or not messageId:
        return
    try:
        channel = await getChannel(bot, int(channelId))
        if channel is None:
            return
        msg = await channel.fetch_message(messageId)
    except (discord.NotFound, discord.Forbidden):
        return

    embed = recruitmentRendering.buildRecruitmentEmbed(submission)
    await msg.edit(embed=embed)


async def dmRecruiterBonus(
    bot: discord.Client,
    recruiterId: int,
    recruitId: int,
    bonusPoints: int,
    *,
    dmUser: DmUserFn,
) -> None:
    await dmUser(
        bot,
        recruiterId,
        f"<@{recruitId}> has passed orientation. {bonusPoints} bonus points were added to your total.",
    )


async def applyRecruitmentOrientationBonus(
    bot: discord.Client,
    recruitUserId: int,
    *,
    getChannel: GetChannelFn,
    dmUser: DmUserFn,
) -> None:
    if not getattr(config, "recruitmentAutoDetectOrientation", True):
        return
    bonus = int(getattr(config, "recruitmentPointsOrientationBonus", 0))
    if bonus <= 0:
        return

    updates = await recruitmentService.applyOrientationBonusForRecruit(recruitUserId, bonus)
    if not updates:
        return

    sheetUpdates: list[dict] = []
    lookupBySubmitterId: dict[int, Optional[str]] = {}
    for item in updates:
        submission = item["submission"]
        await updateRecruitmentSubmissionMessage(
            bot,
            submission,
            getChannel=getChannel,
        )
        if item.get("bonusCredited"):
            submitterId = int(submission["submitterId"])
            if submitterId not in lookupBySubmitterId:
                lookup = await roblox.fetchRobloxUser(submitterId)
                lookupBySubmitterId[submitterId] = lookup.robloxUsername or None
            robloxUsername = lookupBySubmitterId.get(submitterId)
            if robloxUsername:
                sheetUpdates.append(
                    {
                        "robloxUsername": robloxUsername,
                        "pointsDelta": int(bonus),
                        "patrolDelta": 0,
                    }
                )
            await dmRecruiterBonus(
                bot,
                submitterId,
                recruitUserId,
                bonus,
                dmUser=dmUser,
            )
    if sheetUpdates:
        try:
            await taskBudgeter.runSheetsThread(
                recruitmentSheets.applyApprovedLogsBatch,
                sheetUpdates,
                True,
            )
        except Exception:
            log.exception("Recruitment sheet bonus batch sync failed for recruit %s", recruitUserId)


async def reconcileRecruitmentOrientationBonusesForSession(
    bot: discord.Client,
    sessionId: int,
    *,
    getChannel: GetChannelFn,
    dmUser: DmUserFn,
) -> None:
    session = await sessionService.getSession(sessionId)
    if not session or session.get("sessionType") != "orientation":
        return

    attendees = await sessionService.getAttendees(sessionId)
    passingUserIds = {
        int(attendee["userId"])
        for attendee in attendees
        if attendee.get("examGrade") == "PASS" and attendee.get("bgStatus") == "APPROVED"
    }
    for recruitUserId in passingUserIds:
        await applyRecruitmentOrientationBonus(
            bot,
            recruitUserId,
            getChannel=getChannel,
            dmUser=dmUser,
        )


async def attemptRobloxAutoAccept(
    bot: discord.Client,
    guild: Optional[discord.Guild],
    sessionId: int,
    targetUserId: int,
    *,
    dmUser: DmUserFn,
    notifyMods: NotifyModsFn,
    groupUrlProvider: GroupUrlProviderFn,
) -> str:
    attendee = await sessionService.getAttendee(sessionId, targetUserId)
    if not attendee:
        return "NO_ATTENDEE"
    if attendee["examGrade"] != "PASS" or attendee["bgStatus"] != "APPROVED":
        return "NOT_READY"
    if attendee.get("robloxJoinStatus") == "ACCEPTED":
        return "ACCEPTED"

    groupId = orgProfiles.getOrganizationValue(
        config,
        "robloxGroupId",
        guildId=int(getattr(guild, "id", 0) or 0),
        default=0,
    )
    if not groupId or not getattr(config, "robloxOpenCloudApiKey", ""):
        await sessionService.setRobloxStatus(
            sessionId,
            targetUserId,
            attendee.get("robloxUserId"),
            "ERROR",
            "Missing Roblox Open Cloud configuration.",
        )
        await notifyMods(
            bot,
            f"Roblox auto-accept skipped for <@{targetUserId}>: missing Open Cloud config.",
        )
        return "MISSING_CONFIG"

    await sessionService.setRobloxStatus(sessionId, targetUserId, attendee.get("robloxUserId"), "PENDING")
    lookup = await roblox.fetchRobloxUser(targetUserId, guildId=guild.id if guild else None)
    if not lookup.robloxId:
        status = "NO_ROVER" if lookup.error == "No Roblox account linked via RoVer." else "ERROR"
        await sessionService.setRobloxStatus(sessionId, targetUserId, None, status, lookup.error)

        verifyUrl = getattr(config, "roverVerifyUrl", "https://rover.link/verify")
        groupUrl = groupUrlProvider()
        dmMessage = (
            "We couldn't find your Roblox account via RoVer. "
            f"Please verify your Discord here: {verifyUrl} "
            f"then request to join the group: {groupUrl}"
        )
        dmOk = await dmUser(bot, targetUserId, dmMessage)
        modNote = (
            f"Roblox auto-accept failed for <@{targetUserId}>: no RoVer link. "
            f"{'DM sent.' if dmOk else 'DM failed.'}"
        )
        await notifyMods(bot, modNote)
        return status

    # If the user is already in the target group, mark as accepted and stop.
    groups = await roblox.fetchRobloxGroups(lookup.robloxId)
    if groups.status == 200:
        for entry in groups.groups:
            try:
                entryGroupId = int(entry.get("id")) if entry.get("id") is not None else None
            except (TypeError, ValueError):
                entryGroupId = None
            if entryGroupId == int(groupId):
                await sessionService.setRobloxStatus(sessionId, targetUserId, lookup.robloxId, "ACCEPTED")
                return "ACCEPTED"

    accept = await roblox.acceptJoinRequest(lookup.robloxId)
    if accept.ok:
        await sessionService.setRobloxStatus(sessionId, targetUserId, lookup.robloxId, "ACCEPTED")
        return "ACCEPTED"

    status = "NO_REQUEST"
    lowerError = (accept.error or "").lower()
    noRequestMarkers = (
        "not found",
        "unable to read the request as json",
        "application/octet-stream",
        "not a known json content type",
    )
    if accept.status not in (404,) and not any(marker in lowerError for marker in noRequestMarkers):
        status = "ERROR"

    await sessionService.setRobloxStatus(sessionId, targetUserId, lookup.robloxId, status, accept.error)

    groupUrl = groupUrlProvider()
    if status == "NO_REQUEST":
        dmMessage = (
            "We found your Roblox account, but there was no pending join request for the group. "
            f"Please request to join here: {groupUrl}"
        )
    else:
        dmMessage = (
            "We couldn't automatically accept your Roblox join request due to a system error. "
            "Staff has been notified."
        )

    dmOk = await dmUser(bot, targetUserId, dmMessage)
    if status == "NO_REQUEST":
        await notifyMods(
            bot,
            f"Roblox auto-accept skipped for <@{targetUserId}>: "
            f"no pending ANRO join request found. "
            f"{'DM sent to ask them to request to join.' if dmOk else 'DM failed.'}",
        )
    else:
        await notifyMods(
            bot,
            f"Roblox auto-accept failed for <@{targetUserId}> ({status}). "
            f"{'DM sent.' if dmOk else 'DM failed.'} "
            f"Error: {accept.error or 'unknown'}",
        )
    return status


async def deleteSessionMessage(
    bot: discord.Client,
    sessionId: int,
    *,
    getChannel: GetChannelFn,
) -> None:
    session = await sessionService.getSession(sessionId)
    if not session:
        return
    try:
        channel = await getChannel(bot, int(session["channelId"]))
        if channel is None:
            return
        msg = await channel.fetch_message(session["messageId"])
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    try:
        await msg.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return


async def postOrientationResults(
    bot: discord.Client,
    sessionId: int,
    *,
    getChannel: GetChannelFn,
) -> None:
    session = await sessionService.getSession(sessionId)
    if not session:
        return
    channelId = int(
        orgProfiles.getOrganizationValue(
            config,
            "trainingResultsChannelId",
            guildId=int(session.get("guildId") or 0),
            default=1377407562970038272,
        )
        or 0
    )
    if channelId <= 0:
        return
    attendees = await sessionService.getAttendees(sessionId)

    passMentions = [f"<@{a['userId']}>" for a in attendees if a.get("examGrade") == "PASS"]
    failMentions = [f"<@{a['userId']}>" for a in attendees if a.get("examGrade") == "FAIL"]
    passBlock = "\n".join(passMentions) if passMentions else "None"
    failBlock = "\n".join(failMentions) if failMentions else "None"
    hostMention = f"<@{session['hostId']}>"

    content = (
        "### Orientation Results\n"
        f"Host: {hostMention}\n\n"
        "**Certified Recipients (Pass):**\n"
        f"{passBlock}\n\n"
        "**Failed Attendees:**\n"
        f"{failBlock}"
    )

    channel = await getChannel(bot, channelId)
    if channel is None:
        return

    try:
        await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except (discord.Forbidden, discord.HTTPException):
        return


