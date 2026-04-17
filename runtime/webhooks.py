from __future__ import annotations

import logging
from typing import Any

import discord

from . import taskBudgeter

log = logging.getLogger(__name__)

DISCORD_MESSAGE_CONTENT_MAX = 2000
DISCORD_EMBED_FIELD_VALUE_MAX = 1024
DISCORD_EMBED_FIELD_MAX = 25
DISCORD_WEBHOOK_EMBED_MAX = 10


def clipWebhookContent(value: object, *, limit: int = DISCORD_MESSAGE_CONTENT_MAX) -> str:
    text = str(value or "")
    if len(text) <= int(limit):
        return text
    return text[: max(0, int(limit) - 3)].rstrip() + "..."


def buildEmbedFieldChunks(
    lines: list[str],
    *,
    emptyText: str = "None",
    overflowNoun: str = "item(s)",
    maxChunks: int | None = None,
) -> list[str]:
    if not lines:
        return [str(emptyText or "None")]

    normalizedMaxChunks = max(1, int(maxChunks)) if maxChunks is not None else None
    chunks: list[str] = []
    currentLines: list[str] = []
    currentLen = 0

    for idx, rawLine in enumerate(lines):
        lineText = str(rawLine or "")
        if len(lineText) > DISCORD_EMBED_FIELD_VALUE_MAX:
            lineText = lineText[: DISCORD_EMBED_FIELD_VALUE_MAX - 3] + "..."

        addLen = len(lineText) + (1 if currentLines else 0)
        if currentLines and currentLen + addLen > DISCORD_EMBED_FIELD_VALUE_MAX:
            chunks.append("\n".join(currentLines))
            if normalizedMaxChunks is not None and len(chunks) >= normalizedMaxChunks:
                remaining = len(lines) - idx + 1
                suffix = f"... and {remaining} more {overflowNoun}."
                capped = chunks[-1]
                while capped and len(f"{capped}\n{suffix}") > DISCORD_EMBED_FIELD_VALUE_MAX:
                    capped = capped[:-1]
                if not capped.strip():
                    chunks[-1] = suffix[:DISCORD_EMBED_FIELD_VALUE_MAX]
                else:
                    capped = capped.rstrip()
                    if len(f"{capped}\n{suffix}") > DISCORD_EMBED_FIELD_VALUE_MAX:
                        capped = capped[: max(0, DISCORD_EMBED_FIELD_VALUE_MAX - len(suffix) - 4)].rstrip() + "..."
                    chunks[-1] = f"{capped}\n{suffix}"
                return chunks
            currentLines = [lineText]
            currentLen = len(lineText)
            continue

        currentLines.append(lineText)
        currentLen += addLen

    if currentLines:
        chunks.append("\n".join(currentLines))
    return chunks


async def _getOwnedWebhook(
    *,
    botClient: discord.Client,
    channel: discord.abc.Messageable | discord.abc.GuildChannel,
    webhookName: str,
    reason: str,
) -> tuple[discord.Webhook | None, discord.Thread | None]:
    if not getattr(botClient, "user", None):
        return None, None

    thread = channel if isinstance(channel, discord.Thread) else None
    webhookHostChannel = channel.parent if isinstance(channel, discord.Thread) else channel
    if not isinstance(webhookHostChannel, discord.TextChannel):
        return None, thread

    me = webhookHostChannel.guild.me
    if me is None or not webhookHostChannel.permissions_for(me).manage_webhooks:
        return None, thread

    webhooks = await taskBudgeter.runDiscord(lambda: webhookHostChannel.webhooks())
    webhook = next(
        (
            hook
            for hook in webhooks
            if hook.user and hook.user.id == botClient.user.id and str(hook.name or "") == webhookName
        ),
        None,
    )
    if webhook is None:
        webhook = await taskBudgeter.runDiscord(
            lambda: webhookHostChannel.create_webhook(
                name=webhookName[:80],
                reason=reason,
            )
        )
    return webhook, thread


async def sendOwnedWebhookMessageDetailed(
    *,
    botClient: discord.Client,
    channel: discord.abc.Messageable | discord.abc.GuildChannel,
    webhookName: str,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    content: str | None = None,
    view: discord.ui.View | None = None,
    username: str | None = None,
    avatarUrl: str | None = None,
    reason: str = "Jane webhook message",
) -> discord.WebhookMessage | None:
    try:
        webhook, thread = await _getOwnedWebhook(
            botClient=botClient,
            channel=channel,
            webhookName=webhookName,
            reason=reason,
        )
        if webhook is None:
            return None

        kwargs: dict[str, Any] = {
            "wait": True,
            "allowed_mentions": discord.AllowedMentions(users=False, roles=False, everyone=False),
        }
        if content is not None:
            kwargs["content"] = clipWebhookContent(content)
        normalizedEmbeds = [item for item in list(embeds or []) if isinstance(item, discord.Embed)]
        if embed is not None:
            normalizedEmbeds.insert(0, embed)
        normalizedEmbeds = normalizedEmbeds[:DISCORD_WEBHOOK_EMBED_MAX]
        if len(normalizedEmbeds) > 1:
            kwargs["embeds"] = normalizedEmbeds
        elif len(normalizedEmbeds) == 1:
            kwargs["embed"] = normalizedEmbeds[0]
        if view is not None:
            kwargs["view"] = view
        if username:
            kwargs["username"] = username[:80]
        if avatarUrl:
            kwargs["avatar_url"] = avatarUrl
        if thread is not None:
            kwargs["thread"] = thread

        sentMessage = await taskBudgeter.runDiscord(lambda: webhook.send(**kwargs))
        if view is not None and hasattr(botClient, "add_view"):
            try:
                messageId = int(getattr(sentMessage, "id", 0) or 0)
                if messageId > 0:
                    botClient.add_view(view, message_id=messageId)
            except Exception:
                pass
        return sentMessage
    except Exception:
        log.exception("Failed to send owned webhook message for %s.", webhookName)
        return None


async def sendOwnedWebhookMessage(
    *,
    botClient: discord.Client,
    channel: discord.abc.Messageable | discord.abc.GuildChannel,
    webhookName: str,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    content: str | None = None,
    view: discord.ui.View | None = None,
    username: str | None = None,
    avatarUrl: str | None = None,
    reason: str = "Jane webhook message",
) -> bool:
    sentMessage = await sendOwnedWebhookMessageDetailed(
        botClient=botClient,
        channel=channel,
        webhookName=webhookName,
        embed=embed,
        embeds=embeds,
        content=content,
        view=view,
        username=username,
        avatarUrl=avatarUrl,
        reason=reason,
    )
    return sentMessage is not None


async def editOwnedWebhookMessage(
    *,
    botClient: discord.Client,
    message: discord.Message,
    webhookName: str,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    content: str | None = None,
    view: discord.ui.View | None = None,
    reason: str = "Jane webhook message edit",
) -> bool:
    try:
        webhook, thread = await _getOwnedWebhook(
            botClient=botClient,
            channel=message.channel,
            webhookName=webhookName,
            reason=reason,
        )
        if webhook is None:
            return False

        kwargs: dict[str, Any] = {
            "allowed_mentions": discord.AllowedMentions(users=False, roles=False, everyone=False),
        }
        if content is not None:
            kwargs["content"] = clipWebhookContent(content)
        normalizedEmbeds = [item for item in list(embeds or []) if isinstance(item, discord.Embed)]
        if embed is not None:
            normalizedEmbeds.insert(0, embed)
        normalizedEmbeds = normalizedEmbeds[:DISCORD_WEBHOOK_EMBED_MAX]
        if len(normalizedEmbeds) > 1:
            kwargs["embeds"] = normalizedEmbeds
        elif len(normalizedEmbeds) == 1:
            kwargs["embed"] = normalizedEmbeds[0]
        if view is not None:
            kwargs["view"] = view
        if thread is not None:
            kwargs["thread"] = thread

        editedMessage = await taskBudgeter.runDiscord(
            lambda: webhook.edit_message(int(message.id), **kwargs)
        )
        if view is not None and hasattr(botClient, "add_view"):
            try:
                messageId = int(getattr(editedMessage, "id", 0) or 0)
                if messageId > 0:
                    botClient.add_view(view, message_id=messageId)
            except Exception:
                pass
        return True
    except Exception:
        log.exception("Failed to edit owned webhook message for %s.", webhookName)
        return False


async def deleteOwnedWebhookMessage(
    *,
    botClient: discord.Client,
    message: discord.Message,
    webhookName: str,
    reason: str = "Jane webhook message delete",
) -> bool:
    try:
        webhook, thread = await _getOwnedWebhook(
            botClient=botClient,
            channel=message.channel,
            webhookName=webhookName,
            reason=reason,
        )
        if webhook is None:
            return False

        kwargs: dict[str, Any] = {}
        if thread is not None:
            kwargs["thread"] = thread
        await taskBudgeter.runDiscord(lambda: webhook.delete_message(int(message.id), **kwargs))
        return True
    except (discord.NotFound, discord.Forbidden):
        return False
    except discord.HTTPException:
        log.warning("Failed to delete owned webhook message for %s.", webhookName, exc_info=True)
        return False
    except Exception:
        log.exception("Failed to delete owned webhook message for %s.", webhookName)
        return False
