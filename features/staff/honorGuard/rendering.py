import discord
from typing import Dict

def statusIcon(status: str) -> str:
    if status == "APPROVED":
        return ":white_check_mark: Approved"
    if status == "REJECTED":
        return ":x: Rejected"
    if status == "NEEDS_INFO":
        return ":warning: Needs clarification"
    return ":o: Pending"

def buildPointAwardEmbed(submission: Dict) -> discord.Embed:
    embed = discord.Embed(
        title="Honor Guard Point Award",
    )
    embed.add_field(name="Awarder", value=f"<@{submission['submitterId']}>", inline=False)
    embed.add_field(name="Awarded User", value=f"<@{submission['recruitUserId']}>", inline=False)
    embed.add_field(name="Quota Points", value=str(submission['quotaPoints']), inline=True)
    embed.add_field(name="Event Points", value=str(submission['eventPoints']), inline=True)
    embed.add_field(name="Reason", value=submission['reason'], inline=False)
    embed.add_field(name="Status", value=statusIcon(submission['status']), inline=False)
    return embed