from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import discord

import config
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime import permissions as runtimePermissions
from runtime import webhooks as runtimeWebhooks


_skinMentionRegex = re.compile(r"^<@!?(\d+)>$")
_janeGreetingRegex = re.compile(r"\b(hi|hello)\b", re.IGNORECASE)
_americaYaRegex = re.compile(r"\bamerica\W*ya\b", re.IGNORECASE)
_halloEverynyanRegex = re.compile(r"\bhallo\W*everynyan\b", re.IGNORECASE)
_hampterRegex = re.compile(r"\bhampter\b", re.IGNORECASE)
_bumLocatorRegex = re.compile(r"\bwho(?:'s| is)\s+a\s+bum\b", re.IGNORECASE)
_eyesRegex = re.compile(r"^\s*(?::eyes:|👀)\s*$", re.IGNORECASE)
_tanabataTreeRegex = re.compile("^\\s*(?::tanabata_tree:|\U0001F38B)\\s*$", re.IGNORECASE)
_recipePromptRegex = re.compile(r"how\s+do\s+i\s+make\s+(?P<item>[^?\n\r]+)", re.IGNORECASE)
_auraRegex = re.compile(r"\b(how much aura do you have|do you have aura|what amount of aura do you have)\b", re.IGNORECASE)

_americaYaUserIds = {
    768228862684299265,
    442586254916452353,
    696024277673050203,
}
_auraText = "I have an infinite amount of aura."
_unknownUserId = 1086979130572165231
_hampterUserIds = {
    1086979130572165231,
}
_hampterGifUrl = "https://cdn.discordapp.com/attachments/1233638887532920875/1297368325839654943/togif.gif?ex=69ae1fe8&is=69acce68&hm=fc09e5f5f23c4fcb62019c10979ce3920de078e3aeb03528ed1853a09338707d&"

_halloEverynyanUserIds = {
    1086979130572165231,
}
_halloEverynyanGifUrl = "https://tenor.com/view/hello-cat-gif-3451114628649780924"
_horseUserIds = {
    986748357936553995,
}
_horseGifUrl = "https://tenor.com/view/horse-fat-gif-13461358657664073641"
_eyesUserIds = {
    735528587737694248,
}
_eyesGifUrl = "https://media.discordapp.net/attachments/1430279695303184415/1455423665238839420/eye-side-eye-emoji.gif?ex=69b835aa&is=69b6e42a&hm=cfa65c1d8c1102945bc69894da0233ccadf5d0edf45db05664486ff545a55bbc&="
_tanabataTreeUserIds = {
    1468150318675136634,
}
_tanabataTreeGifUrl = "https://media.discordapp.net/attachments/1374350515617665088/1462696462482935808/image.gif?ex=69e12b7c&is=69dfd9fc&hm=bc65c13dc60b72fbaf8fc8aed05b5b1b27113bd0aec2004e3f8c55de813cad57&="
_perishUserIds = {
    776897552954949683,
}
_perishGifUrl = "https://media.discordapp.net/attachments/1373420224363102208/1471997910379008231/togif.gif?ex=69eb4722&is=69e9f5a2&hm=d43a9358b8f102bd8652947b2a0c17e86b303dd1a9163e8444208dec0f0feadc&="
_stimmerUserId = 641429806382317583
_momUserId = 331660652672319488
_bumUserId = 952282215033745448
_janeGreetingBlacklistedUserIds = {
    1220034130805260288,
}
_janeUserId = 1463176057422217348
_skinCooldownBypassRoleIds = normalization.normalizeIntSet(getattr(config, "skinCooldownBypassRoleIds", []))
_skinAllowedUserIds = normalization.normalizeIntSet(getattr(config, "skinAllowedUserIds", []))
_skinOneMinuteCooldownRoleIds = normalization.normalizeIntSet(
    getattr(config, "skinOneMinuteCooldownRoleIds", [1451056189625602341])
)
_skinDoubleCooldownRoleIds = normalization.normalizeIntSet(
    getattr(config, "skinDoubleCooldownRoleIds", [1432967050082385982])
)

_skinCommandNextAllowedAtByUser: dict[int, datetime] = {}
_janeGreetingNextAllowedAtByUser: dict[int, datetime] = {}
_recipeMapCache: dict[str, tuple[str, str]] | None = None
_sixtySevenStreakByChannelUser: dict[tuple[int, int], int] = {}
_killQuotes = [
    "{target} is being used to weigh down the reactor rods.",
    "{target} is being sent to manually inspect the turbine blades.",
    "{target} has won a trip to the spent fuel pool.",
    "{target} was sent to Turbine Hall with Turbines at 4000 RPM and climbing.",
    "{target} is being reassigned as biological shielding.",
    "{target} was locked in the control room with JRO's during a WN raid.",
    "{target} is being placed inside the control room microwave.",
    "{target} will be converted into a backup coolant system.",
]


def _normalizeText(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _isSixtySevenTrigger(content: str) -> bool:
    return str(content or "").strip() == "67"


async def _tryChannelSend(channel: discord.abc.Messageable, content: str) -> bool:
    return await interactionRuntime.safeChannelSend(channel, content=content) is not None


async def _getMemberById(guild: discord.Guild, userId: int) -> discord.Member | None:
    member = guild.get_member(int(userId))
    if member is not None:
        return member
    try:
        return await guild.fetch_member(int(userId))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def _skinCooldownSec() -> int:
    return max(0, int(getattr(config, "skinCommandCooldownSec", 300) or 300))


def _skinCooldownSecForMember(member: discord.Member) -> int:
    cooldownSec = 60 if any(int(role.id) in _skinOneMinuteCooldownRoleIds for role in member.roles) else _skinCooldownSec()
    if any(int(role.id) in _skinDoubleCooldownRoleIds for role in member.roles):
        cooldownSec *= 2
    return cooldownSec


def _isSkinCooldownBypassed(member: discord.Member) -> bool:
    if int(member.id) == _momUserId:
        return True
    if any(int(role.id) in _skinCooldownBypassRoleIds for role in member.roles):
        return True
    return bool(member.guild_permissions.administrator)


def _janeGreetingCooldownSec() -> int:
    return max(0, int(getattr(config, "janeGreetingCooldownSec", 120) or 120))


def _pruneCooldownMap(cooldownMap: dict[int, datetime], nowUtc: datetime) -> None:
    staleUserIds = [
        userId
        for userId, nextAllowedAt in cooldownMap.items()
        if nextAllowedAt <= nowUtc
    ]
    for userId in staleUserIds:
        cooldownMap.pop(userId, None)


def _resolveRecipeFilePath() -> Path:
    return Path(__file__).with_name("recipes.json")


def _loadRecipeMap() -> dict[str, tuple[str, str]]:
    global _recipeMapCache
    if _recipeMapCache is not None:
        return _recipeMapCache

    out: dict[str, tuple[str, str]] = {}
    path = _resolveRecipeFilePath()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.warning("Silly recipes file not found: %s", path)
        _recipeMapCache = out
        return out
    except Exception:
        logging.exception("Failed to load silly recipes file: %s", path)
        _recipeMapCache = out
        return out

    if not isinstance(raw, dict):
        _recipeMapCache = out
        return out

    for key, recipe in raw.items():
        keyText = str(key or "").strip()
        recipeText = str(recipe or "").strip()
        if not keyText or not recipeText:
            continue
        out[_normalizeText(keyText)] = (keyText, recipeText)
    _recipeMapCache = out
    return out


def _resolveRecipe(queryText: str) -> tuple[str, str] | None:
    queryNorm = _normalizeText(queryText)
    if not queryNorm:
        return None

    recipes = _loadRecipeMap()
    exact = recipes.get(queryNorm)
    if exact is not None:
        return exact

    containsMatches: list[tuple[int, tuple[str, str]]] = []
    for keyNorm, value in recipes.items():
        if keyNorm in queryNorm or queryNorm in keyNorm:
            containsMatches.append((abs(len(keyNorm) - len(queryNorm)), value))
    if not containsMatches:
        return None
    containsMatches.sort(key=lambda item: item[0])
    return containsMatches[0][1]


def _isAmericaYaTrigger(content: str) -> bool:
    raw = str(content or "")
    if _americaYaRegex.search(raw):
        return True
    return "americaya" in _normalizeText(raw)



def _isHalloEverynyanTrigger(content: str) -> bool:
    raw = str(content or "")
    if _halloEverynyanRegex.search(raw):
        return True
    return "halloeverynyan" in _normalizeText(raw)

def _isAuraTrigger(content: str) -> bool:
    raw = str(content or "")
    if _auraRegex.search(raw):
        return True
    return "have aura" in _normalizeText(raw)

def _isHampterTrigger(content: str) -> bool:
    raw = str(content or "")
    if _hampterRegex.search(raw):
        return True
    return "hampter" in _normalizeText(raw)


def _isBumLocatorTrigger(content: str) -> bool:
    raw = str(content or "")
    if _bumLocatorRegex.search(raw):
        return True
    normalized = _normalizeText(raw)
    return "whosabum" in normalized or "whoisabum" in normalized

def _isHorseTrigger(content: str) -> bool:
    return _normalizeText(str(content or "")) == "horse"


def _isEyesTrigger(content: str) -> bool:
    return bool(_eyesRegex.match(str(content or "")))


def _isTanabataTreeTrigger(content: str) -> bool:
    return bool(_tanabataTreeRegex.match(str(content or "")))


def _isPerishTrigger(content: str) -> bool:
    return _normalizeText(str(content or "")) == "perish"


_DirectSillyResponse = tuple[set[int], Callable[[str], bool], str]
_directSillyResponses: tuple[_DirectSillyResponse, ...] = (
    (_americaYaUserIds, _isAmericaYaTrigger, "Hallo!"),
    (_halloEverynyanUserIds, _isHalloEverynyanTrigger, _halloEverynyanGifUrl),
    (_horseUserIds, _isHorseTrigger, _horseGifUrl),
    (_eyesUserIds, _isEyesTrigger, _eyesGifUrl),
    (_tanabataTreeUserIds, _isTanabataTreeTrigger, _tanabataTreeGifUrl),
    (_perishUserIds, _isPerishTrigger, _perishGifUrl),
)


async def _maybeSendDirectSillyResponse(message: discord.Message, content: str) -> bool:
    authorId = int(getattr(message.author, "id", 0) or 0)
    for userIds, predicate, response in _directSillyResponses:
        if authorId in userIds and predicate(content):
            return await _tryChannelSend(message.channel, response)
    return False


async def _resolveMemberFromQuery(guild: discord.Guild, query: str) -> discord.Member | None:
    value = (query or "").strip()
    if not value:
        return None

    mentionMatch = _skinMentionRegex.match(value)
    if mentionMatch:
        userId = int(mentionMatch.group(1))
        return await _getMemberById(guild, userId)

    if value.isdigit():
        userId = int(value)
        return await _getMemberById(guild, userId)

    lowered = value.lower()
    if lowered.startswith("@"):
        lowered = lowered[1:].strip()

    exactMatches = [
        member
        for member in guild.members
        if member.display_name.lower() == lowered or member.name.lower() == lowered
    ]
    if len(exactMatches) == 1:
        return exactMatches[0]

    startsWithMatches = [
        member
        for member in guild.members
        if member.display_name.lower().startswith(lowered) or member.name.lower().startswith(lowered)
    ]
    if len(startsWithMatches) == 1:
        return startsWithMatches[0]
    return None


async def _resolveReplyTarget(message: discord.Message) -> discord.Member | None:
    if not message.guild:
        return None

    for mentioned in list(message.mentions):
        member = message.guild.get_member(int(getattr(mentioned, "id", 0) or 0))
        if member is not None:
            return member

    reference = getattr(message, "reference", None)
    if reference is None:
        return None

    resolved = getattr(reference, "resolved", None)
    if isinstance(resolved, discord.Message):
        authorId = int(getattr(getattr(resolved, "author", None), "id", 0) or 0)
        if authorId > 0:
            return await _getMemberById(message.guild, authorId)

    messageId = int(getattr(reference, "message_id", 0) or 0)
    if messageId <= 0:
        return None

    try:
        referencedMessage = await message.channel.fetch_message(messageId)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
        return None

    authorId = int(getattr(getattr(referencedMessage, "author", None), "id", 0) or 0)
    if authorId <= 0:
        return None
    return await _getMemberById(message.guild, authorId)


def _buildSkinnedNickname(currentDisplayName: str) -> str:
    match = re.match(r"^\[([^\]]+)\](.*)$", currentDisplayName or "")
    if not match:
        rawName = (currentDisplayName or "").strip()
        if not rawName:
            return "[BUM-SKINNED]"
        return f"[BUM-SKINNED] {rawName}"[:32]

    innerPrefix = match.group(1).strip()
    rest = match.group(2)
    firstPart = innerPrefix.split("-", 1)[0].strip() if innerPrefix else ""
    if not firstPart:
        firstPart = "BUM"
    return f"[{firstPart}-SKINNED]{rest}"[:32]


async def _sendSkinWebhook(
    message: discord.Message,
    botClient: discord.Client,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
) -> bool:
    if not message.guild or not botClient.user:
        return False
    sentMessage = await runtimeWebhooks.sendOwnedWebhookMessageDetailed(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Skinner",
        content=content,
        embed=embed,
        username="Jane Skinner",
        avatarUrl=botClient.user.display_avatar.url,
        reason="Skin command output",
    )
    return sentMessage is not None


async def _sendKillWebhook(
    message: discord.Message,
    botClient: discord.Client,
    *,
    embed: discord.Embed,
) -> bool:
    if not message.guild or not botClient.user:
        return False
    sentMessage = await runtimeWebhooks.sendOwnedWebhookMessageDetailed(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Clanker",
        embed=embed,
        username=str(botClient.user.display_name or botClient.user.name or "Jane Clanker"),
        avatarUrl=botClient.user.display_avatar.url,
        reason="Kill command output",
    )
    return sentMessage is not None


async def handleKillCommand(message: discord.Message, botClient: discord.Client) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    raw = message.content.strip()
    if raw.split(maxsplit=1)[0].lower() != "!kill":
        return False

    if not runtimePermissions.hasMiddleHighRankRole(message.author):
        await message.channel.send("Only MR/HR roles can use `!kill`.")
        return True

    parts = raw.split(maxsplit=1)
    target: discord.Member | None = None
    if len(parts) >= 2 and parts[1].strip():
        try:
            target = await _resolveMemberFromQuery(message.guild, parts[1].strip())
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            target = None
    else:
        target = await _resolveReplyTarget(message)

    if target is None:
        await message.channel.send("Usage: `!kill @user`")
        return True

    nowUtc = datetime.now(timezone.utc)
    scheduledAt = nowUtc + timedelta(seconds=random.randint(60, 3600))
    timestampText = f"<t:{int(scheduledAt.timestamp())}:R>"
    quote = random.choice(_killQuotes).format(target=target.mention)

    embed = discord.Embed(
        title="Execution Scheduled",
        description=f"**{quote}**\n\n-# {target.mention}'s execution takes place {timestampText}.",
        color=discord.Color.orange(),
        timestamp=nowUtc,
    )
    embed.set_footer(text=f"Command used by {message.author.display_name}")

    sentViaWebhook = await _sendKillWebhook(message, botClient, embed=embed)
    if not sentViaWebhook:
        await message.channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
    return True


async def handleSkinCommand(
    message: discord.Message,
    botClient: discord.Client,
    *,
    hasSkinPermission: Callable[[discord.Member], bool],
) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    raw = message.content.strip()
    if not raw.lower().startswith("!skin"):
        return False

    parts = raw.split(maxsplit=1)
    if int(message.author.id) not in _skinAllowedUserIds and not hasSkinPermission(message.author):
        await message.channel.send("You do not have permission to skin users.")
        return True

    if not _isSkinCooldownBypassed(message.author):
        nowUtc = datetime.now(timezone.utc)
        _pruneCooldownMap(_skinCommandNextAllowedAtByUser, nowUtc)
        nextAllowedAt = _skinCommandNextAllowedAtByUser.get(int(message.author.id))
        if nextAllowedAt and nextAllowedAt > nowUtc:
            remainingSec = max(1, int((nextAllowedAt - nowUtc).total_seconds()))
            mins, secs = divmod(remainingSec, 60)
            waitText = f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"
            await message.channel.send(f"You're on cooldown for `!skin`. Try again in {waitText}.")
            return True
        cooldownSec = _skinCooldownSecForMember(message.author)
        if cooldownSec > 0:
            _skinCommandNextAllowedAtByUser[int(message.author.id)] = nowUtc + timedelta(seconds=cooldownSec)

    target: discord.Member | None = None
    if len(parts) >= 2 and parts[1].strip():
        query = parts[1].strip()
        try:
            target = await _resolveMemberFromQuery(message.guild, query)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            target = None
    else:
        target = await _resolveReplyTarget(message)

    if target is None:
        await message.channel.send("Usage: `!skin username`")
        return True

    if bool(getattr(target, "bot", False)) or bool(getattr(target, "system", False)):
        await message.channel.send("I can't skin bots or app accounts.")
        return True

    if int(target.id) == _janeUserId:
        await message.channel.send("why would i skin myself????")
        return True

    if int(target.id) == _momUserId:
        await message.channel.send("That's my mom, dude :woman_standing:")
        return True

    if int(target.id) == _unknownUserId:
        await message.channel.send("Sorry, but no. Get skinned. heh.")

        newNickname = _buildSkinnedNickname(message.author.display_name)
        try:
            await message.author.edit(
                nick=newNickname,
                reason=f"Reverse skinned by {target} ({target.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            await message.channel.send("I couldn't change that nickname (role hierarchy or permissions).")
            return True

        return True

    newNickname = _buildSkinnedNickname(target.display_name)
    if target.display_name == newNickname:
        await message.channel.send(f"{target.mention} is already skinned.")
        return True

    try:
        await target.edit(
            nick=newNickname,
            reason=f"Skinned by {message.author} ({message.author.id})",
        )
    except (discord.Forbidden, discord.HTTPException):
        await message.channel.send("I couldn't change that nickname (role hierarchy or permissions).")
        return True

    jokes = [
        f"{target.mention} has been skinned. What a bum.",
        f"{target.mention} got skinned by {message.author.mention}. Tragic.",
        f"Skinning complete: {target.mention} has entered the leather era.",
        f"{target.mention} has been skinned. Somebody alert ANRO dermatology.",
        f"{message.author.mention} has claimed another victim, {target.mention} will never recover :pensive:",
    ]
    chosenLine = random.choice(jokes)
    embed = discord.Embed(
        title="Skinning has been completed",
        description=f"\n\n{chosenLine}",
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Command used by {message.author.display_name}")

    sentViaWebhook = await _sendSkinWebhook(message, botClient, embed=embed)
    if not sentViaWebhook:
        await message.channel.send(embed=embed)
    return True


async def handleCasinoToggleCommand(message: discord.Message) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    raw = message.content.strip()
    if not raw.lower().startswith("!casinotoggle"):
        return False

    if not message.author.guild_permissions.administrator:
        await message.channel.send("You do not have permission to use this command.")
        return True

    from silly import gamblingCog

    parts = raw.split(maxsplit=1)
    explicitArg = parts[1].strip().lower() if len(parts) > 1 else ""
    if explicitArg in {"on", "enable", "enabled", "true", "1"}:
        enabled = gamblingCog.setCategoryLockEnabled(True)
    elif explicitArg in {"off", "disable", "disabled", "false", "0"}:
        enabled = gamblingCog.setCategoryLockEnabled(False)
    else:
        enabled = gamblingCog.toggleCategoryLockEnabled()

    stateText = "ENABLED" if enabled else "DISABLED"
    await message.channel.send(
        f"Gambling category lock is now **{stateText}**."
    )
    return True


async def maybeHandleSixtySevenSpam(message: discord.Message) -> bool:
    if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
        return False

    channelId = int(getattr(message.channel, "id", 0) or 0)
    if channelId <= 0:
        return False

    authorId = int(message.author.id)
    streakKey = (channelId, authorId)
    if not _isSixtySevenTrigger(str(message.content or "")):
        _sixtySevenStreakByChannelUser.pop(streakKey, None)
        return False

    streakCount = int(_sixtySevenStreakByChannelUser.get(streakKey, 0) or 0) + 1
    _sixtySevenStreakByChannelUser[streakKey] = streakCount
    if streakCount < 5:
        return False

    _sixtySevenStreakByChannelUser.pop(streakKey, None)
    await _tryChannelSend(message.channel, "cease")

    try:
        await message.author.timeout(
            timedelta(seconds=15),
            reason='Sent "67" five times in a row.',
        )
    except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
        pass
    return True


async def maybeHandleSillyMentions(message: discord.Message, botClient: discord.Client) -> None:
    if message.author.bot:
        return
    botUser = botClient.user
    if botUser is None:
        return

    content = str(message.content or "")
    if await _maybeSendDirectSillyResponse(message, content):
        return

    isMentioningJane = any(int(user.id) == int(botUser.id) for user in message.mentions)
    if not isMentioningJane:
        return

    #hampter
    if int(message.author.id) in _hampterUserIds and _isHampterTrigger(content) and isMentioningJane:
        if await _tryChannelSend(message.channel, _hampterGifUrl):
            await interactionRuntime.safeMessageDelete(message)
        return

    if isMentioningJane and _isBumLocatorTrigger(content):
        locatingMessage = await interactionRuntime.safeChannelSend(
            message.channel,
            content=":compass: Locating...",
        )
        if locatingMessage is None:
            return

        async def _revealBum() -> None:
            await asyncio.sleep(5)
            await interactionRuntime.safeMessageEdit(
                locatingMessage,
                content=f"Bum located! <@{_bumUserId}>",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

        asyncio.create_task(_revealBum())
        return

    if isMentioningJane and _isAuraTrigger(content):
        await _tryChannelSend(message.channel, _auraText)
        return

    recipeMatch = _recipePromptRegex.search(content)
    if recipeMatch:
        requestedItem = str(recipeMatch.group("item") or "").strip()
        resolved = _resolveRecipe(requestedItem)
        if resolved is None:
            await _tryChannelSend(
                message.channel,
                "I don't have that recipe yet. Try one from my recipe list.",
            )
            return
        recipeName, recipeText = resolved
        await _tryChannelSend(message.channel, f"**{recipeName.title()} Recipe**\n{recipeText}")
        return

    if not _janeGreetingRegex.search(content):
        return

    if int(message.author.id) in _janeGreetingBlacklistedUserIds:
        return

    nowUtc = datetime.now(timezone.utc)
    _pruneCooldownMap(_janeGreetingNextAllowedAtByUser, nowUtc)
    nextAllowedAt = _janeGreetingNextAllowedAtByUser.get(int(message.author.id))
    if nextAllowedAt and nextAllowedAt > nowUtc:
        return
    cooldownSec = _janeGreetingCooldownSec()
    if cooldownSec > 0:
        _janeGreetingNextAllowedAtByUser[int(message.author.id)] = nowUtc + timedelta(seconds=cooldownSec)

    if int(message.author.id) == _momUserId:
        responseText = "Hi mom!"
    elif int(message.author.id) == _stimmerUserId:
        responseText = "Hey cash! Good to see you :D"
    else:
        responseText = f"Hi {message.author.mention}!"
    await _tryChannelSend(message.channel, responseText)
