import discord
from discord.ext import commands
from discord import app_commands
from features.staff.clockins import ClockinEngine, OrientationClockinAdapter
from features.staff.sessions.views import SessionView, updateSessionMessage
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions
import config

class SessionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._orientationAdapter = OrientationClockinAdapter()
        self._orientationEngine = ClockinEngine(bot, self._orientationAdapter)

    async def _safeEphemeral(self, interaction: discord.Interaction, message: str) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message,
            ephemeral=True,
        )

    def canStart(self, member: discord.Member) -> bool:
        if config.instructorRoleId is None:
            return True
        return any(r.id == config.instructorRoleId for r in member.roles)

    def canStartBgCheckQueue(self, member: discord.Member) -> bool:
        return runtimePermissions.hasBgCheckCertifiedRole(member)

    @app_commands.command(name="orientation", description="Start an orientation session.")
    @app_commands.rename(maxAttendeeLimit="max-attendee-limit")
    @app_commands.describe(password="Password attendees must enter to join.", maxAttendeeLimit="Number of attendees allowed in the session.")
    async def orientation(self, interaction: discord.Interaction, password: str, maxAttendeeLimit: int = 30):
        if not interaction.guild or not interaction.channel:
            return await self._safeEphemeral(interaction, "This command can only be used inside a server channel.")
        if not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used inside a server channel.")
        if not self.canStart(interaction.user):
            return await self._safeEphemeral(interaction, "You do not have permission to start orientation sessions.")
        if not maxAttendeeLimit > 0:
            return await self._safeEphemeral(interaction, "Attendee limit must be greater than 0.")

        # Create placeholder message first so we can store messageId
        await self._safeEphemeral(interaction, "Creating orientation session...")

        channel = interaction.channel
        host = interaction.user
        guildId = int(interaction.guild_id or interaction.guild.id)
        sessionId = await self._orientationEngine.createSession(
            guildId=guildId,
            channelId=int(channel.id),
            hostId=int(host.id),
            sessionType="orientation",
            password=password,
            messageId=0,
            maxAttendeeLimit=maxAttendeeLimit,
        )
        session = await self._orientationEngine.getSession(int(sessionId))
        tempEmbed = (
            self._orientationAdapter.buildEmbed(session, [])
            if session is not None
            else discord.Embed(
                title="Orientation Session",
                description=f"Click the \u2705 button below to join the session!\n This Orientation has attendee limit of {session.get('maxAttendeeLimit', 30)}.",
            )
        )
        try:
            tempMessage = await channel.send(embed=tempEmbed, view=SessionView(int(sessionId)))
        except discord.Forbidden:
            await self._orientationEngine.updateSessionStatus(int(sessionId), "CANCELED")
            return await self._safeEphemeral(interaction, "I do not have permission to send messages in this channel.")
        except discord.HTTPException:
            await self._orientationEngine.updateSessionStatus(int(sessionId), "CANCELED")
            return await self._safeEphemeral(interaction, "I could not create the orientation session message.")
        await self._orientationEngine.setSessionMessageId(int(sessionId), int(tempMessage.id))

        # Update message with correct view custom_ids
        await updateSessionMessage(self.bot, int(sessionId))

        await self._safeEphemeral(interaction, f"Password: ||{password}||")

    @app_commands.command(name="bg-check", description="Create a background-check queue from pending users.")
    @app_commands.guild_only()
    async def bgCheck(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            return await self._safeEphemeral(interaction, "This command can only be used inside a server channel.")
        if not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used inside a server channel.")
        if not self.canStartBgCheckQueue(interaction.user):
            return await self._safeEphemeral(interaction, "You do not have permission to start background-check queues.")

        runtimeServices = getattr(self.bot, "runtimeServices", {}) or {}
        createBgCheckQueue = runtimeServices.get("createBgCheckQueue")
        if not callable(createBgCheckQueue):
            return await self._safeEphemeral(interaction, "Background-check queue creation is unavailable on this build.")

        await interactionRuntime.safeInteractionDefer(
            interaction,
            ephemeral=True,
            thinking=True,
        )
        ok, response = await createBgCheckQueue(
            guild=interaction.guild,
            channel=interaction.channel,
            actor=interaction.user,
            sourceMessage=None,
        )
        await self._safeEphemeral(interaction, response)

async def setup(bot: commands.Bot):
    await bot.add_cog(SessionsCog(bot))


