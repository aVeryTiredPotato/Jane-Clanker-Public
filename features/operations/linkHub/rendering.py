from __future__ import annotations

from typing import Any

import discord

from runtime import webhooks as webhookRuntime


def _entryIcon(entryType: str) -> str:
    normalized = str(entryType or "").strip().upper()
    if normalized == "WEBHOOK":
        return "[JUMP]"
    return "[DOC]"


def _buildEntryLines(entries: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        title = str(entry.get("title") or "Untitled").strip() or "Untitled"
        url = str(entry.get("url") or "").strip()
        note = str(entry.get("note") or "").strip()
        line = f"{_entryIcon(str(entry.get('entryType') or ''))} [{title}]({url})"
        if note:
            line = f"{line} - {note}"
        lines.append(line[:900])
    return lines


def buildHubOverviewEmbed(hub: dict[str, Any], sections: list[dict[str, Any]]) -> discord.Embed:
    title = str(hub.get("title") or "Internal Master Directory").strip()[:256] or "Internal Master Directory"
    description = str(hub.get("description") or "").strip()[:1500]
    sectionCount = len(sections)
    entryCount = sum(len(section.get("entries", [])) for section in sections)

    embed = discord.Embed(
        title=title,
        description=description or "This board tracks internal docs, jump links, and webhook references.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Sections", value=str(sectionCount), inline=True)
    embed.add_field(name="Links", value=str(entryCount), inline=True)
    embed.add_field(name="Channel", value=f"<#{int(hub.get('channelId') or 0)}>", inline=True)

    if sections:
        lines = [
            f"- {str(section.get('title') or 'Untitled').strip()} ({len(section.get('entries', []))})"
            for section in sections
        ]
        chunks = webhookRuntime.buildEmbedFieldChunks(lines, emptyText="No sections yet.", overflowNoun="sections")
        for index, chunk in enumerate(chunks[:3], start=1):
            fieldName = "Section List" if index == 1 else f"Section List ({index})"
            embed.add_field(name=fieldName, value=chunk, inline=False)
    else:
        embed.add_field(name="Section List", value="No sections yet.", inline=False)

    embed.add_field(
        name="Controls",
        value=(
            "Use the buttons below to add sections, add links, rename sections, "
            "remove outdated links, or refresh the board."
        )[:1024],
        inline=False,
    )
    return embed


def buildSectionEmbed(hub: dict[str, Any], section: dict[str, Any]) -> discord.Embed:
    title = str(section.get("title") or "Section").strip()[:256] or "Section"
    description = str(section.get("description") or "").strip()[:1200]
    entries = list(section.get("entries", []))

    embed = discord.Embed(
        title=title,
        description=description or f"Reference links for {str(hub.get('title') or 'this board').strip()}.",
        color=discord.Color.dark_teal(),
    )
    embed.add_field(name="Entries", value=str(len(entries)), inline=True)

    lines = _buildEntryLines(entries)
    chunks = webhookRuntime.buildEmbedFieldChunks(
        lines,
        emptyText="No links in this section yet.",
        overflowNoun="links",
        maxChunks=10,
    )
    for index, chunk in enumerate(chunks, start=1):
        fieldName = "Links" if index == 1 else f"Links ({index})"
        embed.add_field(name=fieldName, value=chunk, inline=False)
    return embed
