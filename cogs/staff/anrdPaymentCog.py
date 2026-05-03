import asyncio
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.staff.anrdPayments import sheets as paymentSheets
from features.staff.anrdPayments import service as paymentService
from features.staff.anrdPayments import workflowBridge as paymentWorkflowBridge
from features.staff.workflows import rendering as workflowRendering
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions
from runtime import taskBudgeter
from features.staff.sessions.Roblox import robloxUsers

log = logging.getLogger(__name__)


def _normalizeRoleIdList(rawValues) -> set[int]:
    return set(runtimePermissions.normalizeRoleIds(rawValues))


def _hasAnyRole(member: discord.Member, roleIds: set[int]) -> bool:
    return runtimePermissions.hasAnyRole(member, roleIds)


def _setAllButtonsDisabled(view: discord.ui.View, disabled: bool) -> None:
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            child.disabled = disabled


def _parsePrice(raw: str) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    if not text.isdigit():
        return None
    value = int(text)
    if value <= 0:
        return None
    return str(value)


async def _safeInteractionReply(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    await interactionRuntime.safeInteractionReply(
        interaction,
        content=message,
        ephemeral=ephemeral,
    )


class PaymentRequestModal(discord.ui.Modal, title="ANRD Payment Request"):
    workDoneInput = discord.ui.TextInput(
        label="What did you do?",
        style=discord.TextStyle.long,
        required=True,
        max_length=1500,
        placeholder="Briefly describe the work completed.",
    )
    proofInput = discord.ui.TextInput(
        label="Proof",
        style=discord.TextStyle.long,
        required=True,
        max_length=1500,
        placeholder="Paste proof links or a message link.",
    )
    askingPriceInput = discord.ui.TextInput(
        label="Asking price",
        style=discord.TextStyle.short,
        required=True,
        max_length=32,
        placeholder="Example: 250",
    )

    def __init__(self, cog: "AnrdPaymentCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.submitPaymentRequest(
            interaction,
            workSummary=str(self.workDoneInput.value or "").strip(),
            proof=str(self.proofInput.value or "").strip(),
            askingPriceRaw=str(self.askingPriceInput.value or "").strip(),
        )


class PaymentNegotiateModal(discord.ui.Modal, title="Negotiate Price"):
    proposedPriceInput = discord.ui.TextInput(
        label="Proposed price",
        style=discord.TextStyle.short,
        required=True,
        max_length=32,
        placeholder="Example: 200",
    )
    noteInput = discord.ui.TextInput(
        label="Negotiation note",
        style=discord.TextStyle.long,
        required=False,
        max_length=1000,
        placeholder="Optional context for the requester.",
    )

    def __init__(self, parent: "AnrdPaymentReviewView"):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        proposedPrice = _parsePrice(str(self.proposedPriceInput.value or "").strip())
        if not proposedPrice:
            await self.parent.safeInteractionReply(
                interaction,
                "Proposed price must be a positive number.",
                ephemeral=True,
            )
            return
        note = str(self.noteInput.value or "").strip()
        await self.parent.handleNegotiate(interaction, proposedPrice, note)


class PaymentNeedsInfoModal(discord.ui.Modal, title="Ask For More Details"):
    noteInput = discord.ui.TextInput(
        label="What details are needed?",
        style=discord.TextStyle.long,
        required=True,
        max_length=1200,
        placeholder="Explain what the requester should provide.",
    )

    def __init__(self, parent: "AnrdPaymentReviewView"):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        note = str(self.noteInput.value or "").strip()
        await self.parent.handleNeedsInfo(interaction, note)


class PaymentFinalPriceModal(discord.ui.Modal, title="Set Final Price"):
    finalPriceInput = discord.ui.TextInput(
        label="Final price",
        style=discord.TextStyle.short,
        required=True,
        max_length=32,
        placeholder="Example: 175",
    )

    def __init__(self, parent: "PaymentNegotiationThreadView"):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        finalPrice = _parsePrice(str(self.finalPriceInput.value or "").strip())
        if not finalPrice:
            await self.parent.safeInteractionReply(
                interaction,
                "Final price must be a positive number.",
                ephemeral=True,
            )
            return
        await self.parent.handleFinalPrice(interaction, finalPrice)


class PaymentNegotiationThreadView(discord.ui.View):
    def __init__(
        self,
        *,
        cog: "AnrdPaymentCog",
        requestId: int,
        reviewerId: int,
        submitterId: int,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.requestId = int(requestId)
        self.reviewerId = int(reviewerId)
        self.submitterId = int(submitterId)

    async def safeInteractionReply(
        self,
        interaction: discord.Interaction,
        message: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        await _safeInteractionReply(interaction, message, ephemeral=ephemeral)

    @discord.ui.button(
        label="Final Price",
        style=discord.ButtonStyle.primary,
        custom_id="anrd_payment:final_price",
    )
    async def finalPriceBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.reviewerId:
            await self.safeInteractionReply(
                interaction,
                "Only the reviewer who opened this negotiation can set the final price.",
                ephemeral=True,
            )
            return
        await interactionRuntime.safeInteractionSendModal(interaction, PaymentFinalPriceModal(self))

    async def handleFinalPrice(self, interaction: discord.Interaction, finalPrice: str) -> None:
        if interaction.user.id != self.reviewerId:
            await self.safeInteractionReply(
                interaction,
                "Only the reviewer who opened this negotiation can set the final price.",
                ephemeral=True,
            )
            return
        await paymentService.updatePaymentDecision(
            self.requestId,
            status="NEGOTIATING",
            reviewerId=interaction.user.id,
            reviewNote="Final price proposed in negotiation thread.",
            negotiatedPrice=finalPrice,
        )
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            responseView = PaymentFinalPriceResponseView(
                cog=self.cog,
                requestId=self.requestId,
                reviewerId=self.reviewerId,
                submitterId=self.submitterId,
                finalPrice=finalPrice,
            )
            try:
                responseMessage = await channel.send(
                    f"<@{self.submitterId}> Final proposed price: **{finalPrice}**.\n"
                    f"Please confirm if this price is acceptable.",
                    view=responseView,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                )
                self.cog.bot.add_view(responseView, message_id=responseMessage.id)
            except (discord.Forbidden, discord.HTTPException):
                pass
        await self.safeInteractionReply(
            interaction,
            "Final price posted and submitter has been asked for confirmation.",
            ephemeral=True,
        )


class PaymentFinalPriceResponseView(discord.ui.View):
    def __init__(
        self,
        *,
        cog: "AnrdPaymentCog",
        requestId: int,
        reviewerId: int,
        submitterId: int,
        finalPrice: str,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.requestId = int(requestId)
        self.reviewerId = int(reviewerId)
        self.submitterId = int(submitterId)
        self.finalPrice = str(finalPrice).strip()
        self._lock = asyncio.Lock()

    async def safeInteractionReply(
        self,
        interaction: discord.Interaction,
        message: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        await _safeInteractionReply(interaction, message, ephemeral=ephemeral)

    @discord.ui.button(
        label="Accept Price",
        style=discord.ButtonStyle.success,
        custom_id="anrd_payment:accept_final_price",
    )
    async def acceptPriceBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handleSubmitterResponse(interaction, accepted=True)

    @discord.ui.button(
        label="Deny Price",
        style=discord.ButtonStyle.danger,
        custom_id="anrd_payment:deny_final_price",
    )
    async def denyPriceBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handleSubmitterResponse(interaction, accepted=False)

    async def _handleSubmitterResponse(self, interaction: discord.Interaction, *, accepted: bool) -> None:
        if interaction.user.id != self.submitterId:
            await self.safeInteractionReply(
                interaction,
                "Only the submitter can respond to this final price prompt.",
                ephemeral=True,
            )
            return

        async with self._lock:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)

            requestRow = await paymentService.getPaymentRequest(self.requestId)
            if not requestRow:
                await interaction.followup.send("Payment request not found.", ephemeral=True)
                return
            if str(requestRow.get("status") or "").upper() in {"APPROVED", "DENIED"}:
                await interaction.followup.send("This payment request is already finalized.", ephemeral=True)
                return

            await paymentService.applySubmitterFinalPriceDecision(
                self.requestId,
                accepted=accepted,
                actorId=interaction.user.id,
                finalPrice=self.finalPrice,
            )
            refreshedRequest = await paymentService.getPaymentRequest(self.requestId)
            if refreshedRequest is not None:
                await paymentWorkflowBridge.syncPaymentWorkflow(
                    refreshedRequest,
                    stateKey="approved" if accepted else "negotiating",
                    actorId=int(interaction.user.id),
                    note=(
                        f"Submitter accepted final price {self.finalPrice}."
                        if accepted
                        else f"Submitter rejected final price {self.finalPrice}."
                    ),
                    eventType="SUBMITTER_RESPONSE",
                )
            await self.cog.refreshReviewMessageByRequestId(self.requestId)

            syncMessage = ""
            if accepted:
                syncOk, syncDetails = await self.cog.syncApprovedPaymentToOrbat(self.requestId)
                if syncDetails:
                    prefix = "ORBAT sync completed:" if syncOk else "ORBAT sync failed:"
                    syncMessage = f"{prefix} {syncDetails}"

            _setAllButtonsDisabled(self, True)
            if isinstance(interaction.message, discord.Message):
                try:
                    await interaction.message.edit(view=self)
                except (discord.Forbidden, discord.HTTPException):
                    pass

            channel = interaction.channel
            if isinstance(channel, discord.Thread):
                try:
                    if accepted:
                        await channel.send(
                            f"<@{self.reviewerId}> <@{self.submitterId}> accepted the final price **{self.finalPrice}**.",
                            allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                        )
                    else:
                        await channel.send(
                            f"<@{self.reviewerId}> <@{self.submitterId}> denied the final price **{self.finalPrice}**.",
                            allowed_mentions=discord.AllowedMentions(users=True, roles=False),
                        )
                except (discord.Forbidden, discord.HTTPException):
                    pass

            await interaction.followup.send(
                f"Response recorded.{(' ' + syncMessage) if syncMessage else ''}",
                ephemeral=True,
            )


class PaymentRequestLauncherView(discord.ui.View):
    def __init__(self, cog: "AnrdPaymentCog", requesterId: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.requesterId = int(requesterId)

    @discord.ui.button(
        label="Open Payment Form",
        style=discord.ButtonStyle.primary,
    )
    async def openFormBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.requesterId:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Only the user who started this request can use this button.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Only the user who started this request can use this button.",
                    ephemeral=True,
                )
            return
        await interactionRuntime.safeInteractionSendModal(interaction, PaymentRequestModal(self.cog))


class AnrdPaymentReviewView(discord.ui.View):
    def __init__(self, cog: "AnrdPaymentCog", requestId: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.requestId = int(requestId)
        self._lock = asyncio.Lock()

    async def safeInteractionReply(
        self,
        interaction: discord.Interaction,
        message: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        await _safeInteractionReply(interaction, message, ephemeral=ephemeral)

    def _canReview(self, member: discord.Member) -> bool:
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        reviewerRoleIds = _normalizeRoleIdList(getattr(config, "anrdPaymentReviewerRoleIds", []))
        return _hasAnyRole(member, reviewerRoleIds)

    def _isFinalStatus(self, status: str) -> bool:
        return str(status or "").upper() in {"APPROVED", "DENIED"}

    async def _loadRequest(self) -> Optional[dict]:
        return await paymentService.getPaymentRequest(self.requestId)

    async def _refreshReviewMessage(self, interaction: discord.Interaction) -> None:
        requestRow = await self._loadRequest()
        if not requestRow:
            return
        if self._isFinalStatus(str(requestRow.get("status") or "")):
            _setAllButtonsDisabled(self, True)
        embed = await self.cog.buildPaymentRequestReviewEmbed(requestRow)
        message = interaction.message
        if isinstance(message, discord.Message):
            try:
                await message.edit(embed=embed, view=self)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _finishDecision(
        self,
        interaction: discord.Interaction,
        *,
        status: str,
        note: Optional[str],
        negotiatedPrice: Optional[str] = None,
    ) -> Optional[dict]:
        if not isinstance(interaction.user, discord.Member):
            await self.safeInteractionReply(
                interaction,
                "This action can only be used in a server.",
                ephemeral=True,
            )
            return None
        if not self._canReview(interaction.user):
            await self.safeInteractionReply(
                interaction,
                "You are not authorized to review payment requests.",
                ephemeral=True,
            )
            return None

        async with self._lock:
            requestRow = await self._loadRequest()
            if not requestRow:
                await self.safeInteractionReply(interaction, "Payment request not found.", ephemeral=True)
                return None
            if self._isFinalStatus(str(requestRow.get("status") or "")):
                await self.safeInteractionReply(
                    interaction,
                    "This payment request has already been finalized.",
                    ephemeral=True,
                )
                return None

            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)

            previousStates = [child.disabled for child in self.children]
            _setAllButtonsDisabled(self, True)
            if isinstance(interaction.message, discord.Message):
                try:
                    await interaction.message.edit(view=self)
                except (discord.Forbidden, discord.HTTPException):
                    pass

            try:
                await paymentService.updatePaymentDecision(
                    self.requestId,
                    status=status,
                    reviewerId=interaction.user.id,
                    reviewNote=note,
                    negotiatedPrice=negotiatedPrice,
                )
                refreshedForWorkflow = await self._loadRequest()
                if refreshedForWorkflow is not None:
                    workflowState = {
                        "APPROVED": "approved",
                        "DENIED": "denied",
                        "NEGOTIATING": "negotiating",
                        "NEEDS_INFO": "needs-info",
                    }.get(str(status or "").upper(), "pending-review")
                    workflowNote = note or {
                        "APPROVED": "Payment request approved.",
                        "DENIED": "Payment request denied.",
                        "NEGOTIATING": "Reviewer opened price negotiation.",
                        "NEEDS_INFO": "Reviewer requested more details.",
                    }.get(str(status or "").upper(), f"Payment request moved to {status}.")
                    await paymentWorkflowBridge.syncPaymentWorkflow(
                        refreshedForWorkflow,
                        stateKey=workflowState,
                        actorId=int(interaction.user.id),
                        note=workflowNote,
                        eventType=f"STATUS_{str(status or '').upper()}",
                    )
                await self._refreshReviewMessage(interaction)

                syncMessage = ""
                if status == "APPROVED":
                    syncOk, syncDetails = await self.cog.syncApprovedPaymentToOrbat(self.requestId)
                    if syncDetails:
                        prefix = "ORBAT sync completed:" if syncOk else "ORBAT sync failed:"
                        syncMessage = f"{prefix} {syncDetails}"

                finalRow = await self._loadRequest()
                if finalRow and not self._isFinalStatus(str(finalRow.get("status") or "")):
                    _setAllButtonsDisabled(self, False)
                    if isinstance(interaction.message, discord.Message):
                        try:
                            await interaction.message.edit(view=self)
                        except (discord.Forbidden, discord.HTTPException):
                            pass

                if status == "APPROVED":
                    await interaction.followup.send(
                        f"Payment request approved.{(' ' + syncMessage) if syncMessage else ''}",
                        ephemeral=True,
                    )
                elif status == "DENIED":
                    await interaction.followup.send("Payment request denied.", ephemeral=True)
                elif status == "NEGOTIATING":
                    await interaction.followup.send(
                        f"Negotiation started with proposed price {negotiatedPrice}.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send("Marked as needs more details.", ephemeral=True)
                return finalRow
            except Exception as exc:
                for idx, child in enumerate(self.children):
                    child.disabled = previousStates[idx] if idx < len(previousStates) else False
                if isinstance(interaction.message, discord.Message):
                    try:
                        await interaction.message.edit(view=self)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                await interaction.followup.send(f"Could not process this action: {exc}", ephemeral=True)
                return None

    @discord.ui.button(
        label="Accept",
        style=discord.ButtonStyle.success,
        custom_id="anrd_payment:accept",
    )
    async def acceptBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finishDecision(interaction, status="APPROVED", note=None)

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        custom_id="anrd_payment:deny",
    )
    async def denyBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finishDecision(interaction, status="DENIED", note=None)

    @discord.ui.button(
        label="Negotiate Price",
        style=discord.ButtonStyle.primary,
        custom_id="anrd_payment:negotiate",
    )
    async def negotiateBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not self._canReview(interaction.user):
            await self.safeInteractionReply(interaction, "You are not authorized to review payment requests.")
            return
        await interactionRuntime.safeInteractionSendModal(interaction, PaymentNegotiateModal(self))

    @discord.ui.button(
        label="Ask For More Details",
        style=discord.ButtonStyle.secondary,
        custom_id="anrd_payment:needs_info",
    )
    async def needsInfoBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member) or not self._canReview(interaction.user):
            await self.safeInteractionReply(interaction, "You are not authorized to review payment requests.")
            return
        await interactionRuntime.safeInteractionSendModal(interaction, PaymentNeedsInfoModal(self))

    async def handleNegotiate(self, interaction: discord.Interaction, proposedPrice: str, note: str) -> None:
        finalNote = note if note else "Reviewer requested price negotiation."
        updatedRow = await self._finishDecision(
            interaction,
            status="NEGOTIATING",
            note=finalNote,
            negotiatedPrice=proposedPrice,
        )
        if not updatedRow:
            return

        submitterId = int(updatedRow.get("submitterId") or 0)
        if submitterId <= 0:
            return

        message = interaction.message
        if not isinstance(message, discord.Message):
            return

        thread: Optional[discord.Thread] = None
        if isinstance(message.channel, discord.Thread):
            thread = message.channel
        else:
            existingThread = getattr(message, "thread", None)
            if isinstance(existingThread, discord.Thread):
                thread = existingThread
            elif isinstance(existingThread, discord.Object):
                # Best effort for partial thread objects.
                fetched = interaction.guild.get_thread(existingThread.id) if interaction.guild else None
                thread = fetched if isinstance(fetched, discord.Thread) else None
        if thread is None and not isinstance(message.channel, discord.Thread):
            try:
                thread = await message.create_thread(
                    name=f"payment-{self.requestId}-negotiation",
                    auto_archive_duration=1440,
                )
            except (discord.Forbidden, discord.HTTPException):
                thread = None

        if thread is None:
            await interaction.followup.send(
                "Negotiation status updated, but I could not create/open the negotiation thread.",
                ephemeral=True,
            )
            return

        negotiationView = PaymentNegotiationThreadView(
            cog=self.cog,
            requestId=self.requestId,
            reviewerId=interaction.user.id,
            submitterId=submitterId,
        )
        try:
            threadMessage = await thread.send(
                f"{interaction.user.mention} <@{submitterId}>\n"
                f"Negotiation opened for this payment request.\n"
                f"Proposed price: **{proposedPrice}**\n"
                f"{finalNote}\n\n"
                "Reviewer: use **Final Price** when ready.",
                view=negotiationView,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False),
            )
            self.cog.bot.add_view(negotiationView, message_id=threadMessage.id)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Negotiation status updated, but I could not post the thread prompt.",
                ephemeral=True,
            )

    async def handleNeedsInfo(self, interaction: discord.Interaction, note: str) -> None:
        await self._finishDecision(
            interaction,
            status="NEEDS_INFO",
            note=note,
        )


class AnrdPaymentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            workflowRows = await paymentService.listPaymentRequestsForWorkflowReconciliation()
            reconciled, changed = await paymentWorkflowBridge.reconcilePaymentWorkflowRows(workflowRows)
            log.info(
                "ANRD payment workflow reconciliation: checked=%d changed=%d",
                reconciled,
                changed,
            )
        except Exception:
            log.exception("ANRD payment workflow reconciliation failed during startup.")

        rows = await paymentService.listOpenPaymentRequests()
        restored = 0
        for row in rows:
            messageId = int(row.get("reviewMessageId") or 0)
            requestId = int(row.get("requestId") or 0)
            if messageId <= 0 or requestId <= 0:
                continue
            self.bot.add_view(AnrdPaymentReviewView(self, requestId), message_id=messageId)
            restored += 1
        if restored > 0:
            log.info("ANRD payment review views restored: %s", restored)

    async def refreshReviewMessageByRequestId(self, requestId: int) -> None:
        requestRow = await paymentService.getPaymentRequest(int(requestId))
        if not requestRow:
            return
        reviewChannelId = int(requestRow.get("reviewChannelId") or 0)
        reviewMessageId = int(requestRow.get("reviewMessageId") or 0)
        if reviewChannelId <= 0 or reviewMessageId <= 0:
            return

        guildId = int(requestRow.get("guildId") or 0)
        guild = self.bot.get_guild(guildId) if guildId > 0 else None
        channel = guild.get_channel(reviewChannelId) if guild else None
        if channel is None and guild is not None:
            try:
                channel = await guild.fetch_channel(reviewChannelId)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(reviewMessageId)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

        view = AnrdPaymentReviewView(self, int(requestId))
        status = str(requestRow.get("status") or "").upper()
        if status in {"APPROVED", "DENIED"}:
            _setAllButtonsDisabled(view, True)
        try:
            await message.edit(embed=await self.buildPaymentRequestReviewEmbed(requestRow), view=view)
            self.bot.add_view(view, message_id=message.id)
        except (discord.Forbidden, discord.HTTPException):
            return

    def canSubmit(self, member: discord.Member) -> bool:
        submitterRoleIds = _normalizeRoleIdList(getattr(config, "anrdPaymentSubmitterRoleIds", []))
        if not submitterRoleIds:
            return True
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        return _hasAnyRole(member, submitterRoleIds)

    async def safeReply(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=content,
            ephemeral=ephemeral,
        )

    async def resolveReviewChannel(
        self,
        guild: discord.Guild,
        fallback: Optional[discord.abc.Messageable],
    ) -> Optional[discord.abc.Messageable]:
        channelId = int(getattr(config, "anrdPaymentReviewChannelId", 0) or 0)
        if channelId > 0:
            channel = guild.get_channel(channelId)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channelId)
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    channel = None
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                return channel
        if isinstance(fallback, (discord.TextChannel, discord.Thread)):
            return fallback
        return None

    def reviewerMention(self) -> str:
        roleIds = sorted(_normalizeRoleIdList(getattr(config, "anrdPaymentReviewerRoleIds", [])))
        return " ".join(f"<@&{roleId}>" for roleId in roleIds)

    def buildPaymentRequestEmbed(self, row: dict) -> discord.Embed:
        status = str(row.get("status") or "PENDING").upper()
        statusMap = {
            "PENDING": "Pending Review",
            "NEGOTIATING": "Negotiation Requested",
            "NEEDS_INFO": "Needs More Details",
            "APPROVED": "Approved",
            "DENIED": "Denied",
        }
        statusLabel = statusMap.get(status, status.title())

        embed = discord.Embed(
            title="ANRD Payment Request",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Requester", value=f"<@{int(row.get('submitterId') or 0)}>", inline=True)
        embed.add_field(name="Asking Price", value=str(row.get("askingPrice") or "N/A"), inline=True)
        negotiated = str(row.get("negotiatedPrice") or "").strip()
        if negotiated:
            embed.add_field(name="Proposed Price", value=negotiated, inline=True)

        workSummary = str(row.get("workSummary") or "").strip() or "N/A"
        proof = str(row.get("proof") or "").strip() or "N/A"
        embed.add_field(name="Work Completed", value=workSummary[:1024], inline=False)
        embed.add_field(name="Proof", value=proof[:1024], inline=False)
        createdAt = str(row.get("createdAt") or "").strip()
        if createdAt:
            embed.set_footer(text=f"Created: {createdAt}")
        return embed

    async def buildPaymentRequestReviewEmbed(self, row: dict) -> discord.Embed:
        if int(row.get("reviewMessageId") or 0) > 0 or str(row.get("status") or "").upper() != "PENDING":
            await paymentWorkflowBridge.ensurePaymentWorkflowCurrent(row)
        workflowSummary = await paymentWorkflowBridge.getPaymentWorkflowSummary(row)
        workflowHistorySummary = await paymentWorkflowBridge.getPaymentWorkflowHistorySummary(row)
        embed = self.buildPaymentRequestEmbed(row)
        workflowRendering.addReviewWorkflowFields(
            embed,
            statusText={
                "PENDING": "Pending Review",
                "NEGOTIATING": "Negotiation Requested",
                "NEEDS_INFO": "Needs More Details",
                "APPROVED": "Approved",
                "DENIED": "Denied",
            }.get(str(row.get("status") or "PENDING").upper(), str(row.get("status") or "Pending").title()),
            workflowSummary=workflowSummary,
            workflowHistorySummary=workflowHistorySummary,
            reviewerNote=str(row.get("reviewNote") or "").strip(),
        )
        return embed

    def approvedAmountForRequest(self, row: dict) -> Optional[int]:
        negotiatedRaw = str(row.get("negotiatedPrice") or "").strip()
        askingRaw = str(row.get("askingPrice") or "").strip()
        chosen = negotiatedRaw if negotiatedRaw.isdigit() else askingRaw
        if not chosen or not chosen.isdigit():
            return None
        amount = int(chosen)
        if amount <= 0:
            return None
        return amount

    def guessRobloxUsernameFromDisplayName(self, displayName: str) -> Optional[str]:
        text = str(displayName or "").strip()
        if not text:
            return None
        while text.startswith("["):
            end = text.find("]")
            if end < 0:
                break
            text = text[end + 1 :].lstrip()
        if not text:
            return None
        candidate = text.split()[0].strip()
        if candidate.startswith("@"):
            candidate = candidate[1:]
        candidate = re.sub(r"[^A-Za-z0-9_]", "", candidate)
        return candidate or None

    async def resolveSubmitterMember(self, row: dict) -> Optional[discord.Member]:
        guildId = int(row.get("guildId") or 0)
        submitterId = int(row.get("submitterId") or 0)
        if guildId <= 0 or submitterId <= 0:
            return None
        guild = self.bot.get_guild(guildId)
        if guild is None:
            return None
        member = guild.get_member(submitterId)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(submitterId)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def resolveSubmitterRobloxUsername(self, row: dict) -> tuple[Optional[str], Optional[str]]:
        submitterId = int(row.get("submitterId") or 0)
        guildId = int(row.get("guildId") or 0)
        if submitterId <= 0:
            return None, "Missing submitter ID."
        roverResult = await robloxUsers.fetchRobloxUser(submitterId, guildId if guildId > 0 else None)
        username = str(roverResult.robloxUsername or "").strip()
        if username:
            return username, None

        member = await self.resolveSubmitterMember(row)
        if member is not None:
            guessed = self.guessRobloxUsernameFromDisplayName(member.display_name)
            if guessed:
                await robloxUsers.rememberKnownRobloxIdentity(
                    submitterId,
                    guessed,
                    source="anrd-nickname",
                    guildId=guildId if guildId > 0 else None,
                    confidence=65,
                )
                return guessed, "RoVer lookup failed; using nickname fallback."

        reason = str(roverResult.error or "No Roblox username linked in RoVer.")
        return None, reason

    async def syncApprovedPaymentToOrbat(self, requestId: int) -> tuple[bool, str]:
        if await paymentService.isPaymentPayoutSynced(requestId):
            return True, "Already synced."

        requestRow = await paymentService.getPaymentRequest(requestId)
        if not requestRow:
            return False, "Request not found."

        status = str(requestRow.get("status") or "").upper()
        if status != "APPROVED":
            return False, f"Request status is {status}, not APPROVED."

        amount = self.approvedAmountForRequest(requestRow)
        if amount is None:
            return False, "Approved amount is invalid."

        username, usernameError = await self.resolveSubmitterRobloxUsername(requestRow)
        if not username:
            return False, f"Could not resolve Roblox username: {usernameError}"

        fallbackUsername: Optional[str] = None
        member = await self.resolveSubmitterMember(requestRow)
        if member is not None:
            guessed = self.guessRobloxUsernameFromDisplayName(member.display_name)
            if guessed and guessed.lower() != username.lower():
                fallbackUsername = guessed

        try:
            result = await taskBudgeter.runSheetsThread(
                paymentSheets.applyApprovedPaymentRequest,
                username,
                amount,
            )
        except Exception as exc:
            return False, str(exc)

        if (not isinstance(result, dict) or not result.get("ok")) and fallbackUsername:
            primaryReason = str(result.get("reason") if isinstance(result, dict) else "Unknown error")
            if "rank-not-found" in primaryReason:
                try:
                    fallbackResult = await taskBudgeter.runSheetsThread(
                        paymentSheets.applyApprovedPaymentRequest,
                        fallbackUsername,
                        amount,
                    )
                except Exception as exc:
                    return False, f"{primaryReason}; fallback failed: {exc}"
                if isinstance(fallbackResult, dict) and fallbackResult.get("ok"):
                    result = fallbackResult
                    username = fallbackUsername
                    await robloxUsers.rememberKnownRobloxIdentity(
                        int(requestRow.get("submitterId") or 0),
                        username,
                        source="anrd-orbat-fallback",
                        guildId=int(requestRow.get("guildId") or 0) or None,
                        confidence=70,
                    )

        if not isinstance(result, dict) or not result.get("ok"):
            reason = str(result.get("reason") if isinstance(result, dict) else "Unknown error")
            return False, reason

        await paymentService.markPaymentPayoutSynced(requestId)
        section = str(result.get("section") or "Unknown section")
        rowNumber = int(result.get("row") or 0)
        if rowNumber > 0:
            return True, f"{section} row {rowNumber} updated for {username}."
        return True, f"{section} updated for {username}."

    async def submitPaymentRequest(
        self,
        interaction: discord.Interaction,
        *,
        workSummary: str,
        proof: str,
        askingPriceRaw: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self.safeReply(interaction, "This command can only be used in a server.")
            return
        if not self.canSubmit(interaction.user):
            await self.safeReply(
                interaction,
                "You are not authorized to submit ANRD payment requests.",
                ephemeral=True,
            )
            return

        askingPrice = _parsePrice(askingPriceRaw)
        if not askingPrice:
            await self.safeReply(
                interaction,
                "Asking price must be a positive number.",
                ephemeral=True,
            )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        reviewChannel = await self.resolveReviewChannel(interaction.guild, interaction.channel)
        if not isinstance(reviewChannel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send(
                "Could not find a valid review channel for payment requests.",
                ephemeral=True,
            )
            return

        requestId = await paymentService.createPaymentRequest(
            guildId=interaction.guild.id,
            channelId=interaction.channel_id,
            submitterId=interaction.user.id,
            workSummary=workSummary,
            proof=proof,
            askingPrice=askingPrice,
        )
        requestRow = await paymentService.getPaymentRequest(requestId)
        if not requestRow:
            await interaction.followup.send("Could not create payment request.", ephemeral=True)
            return
        await paymentWorkflowBridge.syncPaymentWorkflow(
            requestRow,
            stateKey="submitted",
            actorId=int(interaction.user.id),
            note="Payment request submitted.",
            eventType="SUBMITTED",
        )
        requestRow = await paymentService.getPaymentRequest(requestId) or requestRow

        reviewView = AnrdPaymentReviewView(self, requestId)
        contentParts = []
        mention = self.reviewerMention()
        if mention:
            contentParts.append(mention)
        content = "\n".join(contentParts) if contentParts else None
        try:
            reviewMessage = await reviewChannel.send(
                content=content,
                embed=await self.buildPaymentRequestReviewEmbed(requestRow),
                view=reviewView,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "Request saved, but I could not post the review card.",
                ephemeral=True,
            )
            return

        await paymentService.setPaymentReviewMessage(
            requestId=requestId,
            reviewChannelId=reviewMessage.channel.id,
            reviewMessageId=reviewMessage.id,
        )
        routedRow = await paymentService.getPaymentRequest(requestId)
        if routedRow is not None:
            await paymentWorkflowBridge.syncPaymentWorkflow(
                routedRow,
                stateKey="pending-review",
                actorId=None,
                note="Routed for review.",
                eventType="ROUTED_FOR_REVIEW",
            )
        self.bot.add_view(reviewView, message_id=reviewMessage.id)
        await self.refreshReviewMessageByRequestId(requestId)
        await interaction.followup.send(
            "Payment request submitted for review.",
            ephemeral=True,
        )

    @app_commands.command(
        name="request-payment",
        description="Submit an ANRD payment request for reviewer approval.",
    )
    async def requestPayment(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self.safeReply(interaction, "This command can only be used in a server.")
            return
        if not self.canSubmit(interaction.user):
            await self.safeReply(
                interaction,
                "You are not authorized to submit ANRD payment requests.",
                ephemeral=True,
            )
            return

        view = PaymentRequestLauncherView(self, interaction.user.id)
        try:
            await interaction.response.send_message(
                "Fill out your payment request with the button below.\n"
                "Please include clear proof links and your asking price.",
                view=view,
                ephemeral=True,
            )
        except (discord.NotFound, discord.HTTPException):
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnrdPaymentCog(bot))

