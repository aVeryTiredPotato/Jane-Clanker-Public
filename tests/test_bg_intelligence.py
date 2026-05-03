from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import characters
import config
from cogs.staff.bgIntelligenceCog import BgIntelDetailsView
from features.staff.bgIntelligence import rendering, scoring, service
from features.staff.sessions import bgBuckets
from features.staff.sessions.Roblox import robloxBadges, robloxGamepasses, robloxInventoryApi, robloxInventoryVisual


def _report(**overrides):
    base = {
        "discordUserId": 123,
        "discordDisplayName": "Reviewer Target",
        "discordUsername": "target",
        "reviewBucket": bgBuckets.adultBgReviewBucket,
        "reviewBucketSource": "manual",
        "identitySource": "rover",
        "robloxUserId": 456,
        "robloxUsername": "TargetUser",
        "roverError": None,
        "robloxCreated": "2020-01-01T00:00:00Z",
        "robloxAgeDays": 1500,
        "usernameHistoryScanStatus": "OK",
        "usernameHistoryScanError": None,
        "previousRobloxUsernames": [],
        "altScanStatus": "OK",
        "altScanError": None,
        "altMatches": [],
        "directMatches": [],
        "externalSourceStatus": "SKIPPED",
        "externalSourceError": None,
        "externalSourceMatches": [],
        "externalSourceDetails": [],
        "connectionScanStatus": "OK",
        "connectionScanError": None,
        "connectionSummary": {"friends": 10, "followers": 2, "following": 3},
        "friendIdsScanStatus": "OK",
        "friendIdsScanError": None,
        "friendUserIds": [],
        "groupScanStatus": "OK",
        "groupScanError": None,
        "groupSummary": {"totalGroups": 0},
        "groups": [],
        "flaggedGroups": [],
        "flagMatches": [],
        "inventoryScanStatus": "OK",
        "inventoryScanError": None,
        "inventorySummary": {
            "uniqueAssetCount": 0,
            "knownValueRobux": 0,
            "complete": True,
            "valueSource": "test",
        },
        "flaggedItems": [],
        "gamepassScanStatus": "OK",
        "gamepassScanError": None,
        "gamepassSummary": {
            "totalGamepasses": 1,
            "totalRobux": 25,
            "pricedGamepasses": 1,
            "unpricedGamepasses": 0,
            "complete": True,
        },
        "ownedGamepasses": [{"id": 99, "name": "Pass", "price": 25}],
        "favoriteGameScanStatus": "OK",
        "favoriteGameScanError": None,
        "favoriteGames": [{"name": "Game", "universeId": 1, "placeId": 2}],
        "flaggedFavoriteGames": [],
        "outfitScanStatus": "OK",
        "outfitScanError": None,
        "outfits": [],
        "badgeScanStatus": "OK",
        "badgeScanError": None,
        "flaggedBadges": [],
        "badgeHistoryScanStatus": "OK",
        "badgeHistoryScanError": None,
        "badgeHistorySample": [],
        "badgeTimelineSummary": {
            "sampleSize": 0,
            "datedBadges": 0,
            "awardDateStatus": "OK",
            "historyComplete": True,
            "quality": "none",
        },
        "priorReportSummary": {
            "totalRecent": 1,
            "highRiskRecent": 0,
            "noScoreRecent": 0,
            "queueApprovals": 1,
            "queueRejections": 0,
            "rows": [],
        },
        "privateInventoryDmSent": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _dotnet_ticks(value: datetime) -> int:
    epoch = datetime(1, 1, 1, tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc)
    delta = normalized - epoch
    return ((delta.days * 86400 + delta.seconds) * 10_000_000) + (delta.microseconds * 10)


def _badge_cursor(badge_id: int, awarded_at: datetime) -> str:
    payload = {"key": f"{int(badge_id)}:{_dotnet_ticks(awarded_at)}"}
    raw = json.dumps(payload, separators=(",", ":")) + "\nchecksum"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


class BgIntelligenceScoringTests(unittest.TestCase):
    def test_banned_user_match_sets_hard_minimum(self):
        report = _report(
            directMatches=[
                {
                    "type": "banned_user",
                    "value": 456,
                    "minimumScore": 95,
                    "note": "test ban",
                }
            ]
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 95)
        self.assertEqual(score.hardMinimum, 95)
        self.assertTrue(any("known banned Roblox ID" in signal.label for signal in score.signals))

    def test_previous_username_match_is_scored_but_capped_below_ban_override(self):
        report = _report(
            previousRobloxUsernames=["OldName"],
            directMatches=[
                {
                    "type": "previous_username",
                    "value": "OldName",
                    "minimumScore": 60,
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 60)
        self.assertLess(score.hardMinimum, 80)
        self.assertTrue(any("Prior Roblox username" in signal.label for signal in score.signals))

    def test_known_member_alt_match_adds_contextual_risk(self):
        report = _report(
            robloxUsername="TargetUserAlt",
            altMatches=[
                {
                    "candidateUsername": "TargetUserAlt",
                    "candidateKind": "current_username",
                    "knownRobloxUsername": "TargetUser",
                    "knownDiscordUserId": 999,
                    "source": "orbat_member_mirror",
                    "reason": "known member username with an alt/back-up marker",
                    "strength": "moderate",
                    "evidenceType": "name_variant",
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertTrue(any("Alt/identity evidence" in signal.label for signal in score.signals))

    def test_cleared_alt_match_is_not_scored_as_risk(self):
        report = _report(
            altMatches=[
                {
                    "strength": "cleared",
                    "evidenceType": "staff_alt_link",
                    "knownRobloxUsername": "KnownUser",
                    "reason": "Staff cleared this relation.",
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertTrue(any("cleared/not-alt" in signal.label for signal in score.signals))
        self.assertFalse(any("Alt/identity evidence" in signal.label for signal in score.signals))

    def test_missing_identity_returns_identity_review(self):
        report = _report(
            robloxUserId=None,
            robloxUsername=None,
            externalSourceStatus="OK",
            externalSourceMatches=[],
            externalSourceDetails=[],
        )

        score = scoring.scoreReport(report)

        self.assertFalse(score.scored)
        self.assertEqual(score.outcome, "needs_identity")
        self.assertEqual(score.band, "Needs Identity Review")

    def test_configured_group_flag_stays_reviewable_after_clean_context(self):
        report = _report(
            flaggedGroups=[
                {"id": 1001, "name": "Flagged Group", "role": "Member", "rank": 1},
            ],
            flagMatches=[
                {
                    "type": "keyword",
                    "value": "flagged",
                    "context": "group",
                    "groupId": 1001,
                    "groupName": "Flagged Group",
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 40)
        self.assertGreaterEqual(score.signals[-1].points, 40)
        self.assertTrue(any("Review floor" in signal.label for signal in score.signals))

    def test_low_external_record_stays_reviewable_after_clean_context(self):
        report = _report(
            externalSourceStatus="OK",
            externalSourceDetails=[{"source": "TASE", "status": "OK", "summary": {}}],
            externalSourceMatches=[
                {
                    "source": "TASE",
                    "scoreSum": 20,
                    "guildCount": 1,
                    "pastOffender": False,
                }
            ],
        )

        score = scoring.scoreReport(report)

        self.assertTrue(score.scored)
        self.assertGreaterEqual(score.score, 35)
        self.assertEqual(score.band, "Mild Review")

    def test_connection_footprint_affects_established_accounts_lightly(self):
        noPrior = {
            "totalRecent": 0,
            "highRiskRecent": 0,
            "noScoreRecent": 0,
            "queueApprovals": 0,
            "queueRejections": 0,
            "rows": [],
        }
        thinReport = _report(
            connectionSummary={"friends": 0, "followers": 0, "following": 0},
            priorReportSummary=noPrior,
        )
        establishedReport = _report(
            connectionSummary={"friends": 100, "followers": 25, "following": 30},
            priorReportSummary=noPrior,
        )

        thinScore = scoring.scoreReport(thinReport)
        establishedScore = scoring.scoreReport(establishedReport)

        self.assertGreater(thinScore.score, establishedScore.score)
        self.assertTrue(any("social footprint" in signal.label for signal in thinScore.signals))
        self.assertTrue(any("social footprint looks established" in signal.label for signal in establishedScore.signals))


class BgIntelligenceRenderingTests(unittest.TestCase):
    def test_text_report_includes_full_release_sections(self):
        report = _report()
        score = scoring.scoreReport(report)

        text = rendering.buildReportText(report, score=score, reportId=42)

        self.assertIn("Decision Readiness", text)
        self.assertIn("Source Checks", text)
        self.assertIn("Alt / Identity Evidence", text)
        self.assertIn("Gamepasses", text)
        self.assertIn("Favorite Game Sample", text)
        self.assertIn("Jane History", text)

    def test_embed_footer_respects_optional_text_report(self):
        report = _report()
        score = scoring.scoreReport(report)

        without_text = rendering.buildReportEmbed(report, score=score, includeTextReport=False)
        with_text = rendering.buildReportEmbed(report, score=score, includeTextReport=True)

        self.assertNotIn("Full text report", without_text.footer.text or "")
        self.assertIn("Full text report", with_text.footer.text or "")

    def test_overview_embed_matches_summary_layout(self):
        report = _report(
            directMatches=[
                {
                    "type": "banned_user",
                    "value": 456,
                    "minimumScore": 95,
                    "note": "test ban",
                }
            ],
            altMatches=[
                {
                    "candidateUsername": "TargetUserAlt",
                    "knownRobloxUsername": "TargetUser",
                    "reason": "known member username with an alt/back-up marker",
                    "strength": "moderate",
                }
            ],
        )
        score = scoring.scoreReport(report)

        embed = rendering.buildReportEmbed(report, score=score)
        fieldNames = [field.name for field in embed.fields]
        fieldText = "\n".join(field.value for field in embed.fields)

        self.assertEqual(
            fieldNames,
            [
                "[Scan] Detection Summary",
                "[Profile] Profile Information",
                "[Connections] Connections",
                "[Groups] Groups",
                "[Inventory] Inventory",
                "[Gamepasses] Gamepasses",
                "[Favorites] Favorites",
                "[Records] TASE Records",
                "[Badges] Badges",
            ],
        )
        self.assertIn("Review Band", embed.fields[0].value)
        self.assertNotIn("route", fieldText.lower())
        self.assertNotIn("[Direct] Direct Rule Matches", fieldNames)
        self.assertNotIn("[Alt] Alt / Identity Evidence", fieldNames)

    def test_expanded_sections_keep_detailed_content(self):
        report = _report(
            directMatches=[
                {
                    "type": "banned_user",
                    "value": 456,
                    "minimumScore": 95,
                    "note": "test ban",
                }
            ],
            inventorySummary={
                "itemsScanned": 25,
                "pagesScanned": 2,
                "uniqueAssetCount": 10,
                "uniqueGamepassCount": 1,
                "knownValueRobux": 100,
                "pricedAssetCount": 4,
                "unpricedAssetCount": 6,
                "complete": True,
                "valueSource": "test",
            },
        )
        score = scoring.scoreReport(report)

        scanEmbed = rendering.buildPublicSectionEmbed(report, score=score, section="scan")
        inventoryEmbed = rendering.buildPublicSectionEmbed(report, score=score, section="inventory")

        self.assertIn("Direct rule matches", scanEmbed.fields[0].value)
        self.assertIn("Items scanned", inventoryEmbed.fields[0].value)
        self.assertIn("Known current asset value", inventoryEmbed.fields[0].value)


class RobloxBadgeTimelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_badge_history_applies_cursor_boundary_award_dates(self):
        oldRequestJson = robloxBadges._requestJson
        oldCacheGet = robloxBadges._cacheGet
        oldCacheSet = robloxBadges._cacheSet
        awardedAt = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        async def fakeRequestJson(*args, **kwargs):
            return 200, {
                "data": [
                    {
                        "id": 111,
                        "name": "First",
                        "created": "2020-01-01T00:00:00Z",
                        "statistics": {"awardedCount": 10},
                    },
                    {
                        "id": 222,
                        "name": "Boundary",
                        "created": "2020-01-02T00:00:00Z",
                        "statistics": {"awardedCount": 20},
                    },
                ],
                "nextPageCursor": _badge_cursor(222, awardedAt),
            }

        try:
            robloxBadges._requestJson = fakeRequestJson
            robloxBadges._cacheGet = lambda *args, **kwargs: None
            robloxBadges._cacheSet = lambda *args, **kwargs: None

            result = await robloxBadges.fetchRobloxUserBadges(456, limit=10, maxPages=1)
        finally:
            robloxBadges._requestJson = oldRequestJson
            robloxBadges._cacheGet = oldCacheGet
            robloxBadges._cacheSet = oldCacheSet

        self.assertIsNone(result.error)
        self.assertEqual(result.badges[1]["awardedDate"], "2024-01-02T03:04:05Z")
        self.assertEqual(result.badges[1]["awardedDateSource"], "badge_history_next_cursor")

    async def test_badge_award_403_reports_roblox_unavailable(self):
        oldRequestJson = robloxBadges._requestJson
        oldCacheGet = robloxBadges._cacheGet
        oldCacheSet = robloxBadges._cacheSet
        oldDelay = robloxBadges._badgeAwardLookupDelaySec

        async def fakeRequestJson(*args, **kwargs):
            return 403, {"errors": [{"message": "Request Context Failure: response code is not 200"}]}

        try:
            robloxBadges._requestJson = fakeRequestJson
            robloxBadges._cacheGet = lambda *args, **kwargs: None
            robloxBadges._cacheSet = lambda *args, **kwargs: None
            robloxBadges._badgeAwardLookupDelaySec = lambda: 0.0

            result = await robloxBadges.fetchRobloxBadgeAwards(456, {999}, batchSize=1)
        finally:
            robloxBadges._requestJson = oldRequestJson
            robloxBadges._cacheGet = oldCacheGet
            robloxBadges._cacheSet = oldCacheSet
            robloxBadges._badgeAwardLookupDelaySec = oldDelay

        self.assertEqual(result.status, 403)
        self.assertIn("unavailable from Roblox", result.error or "")
        self.assertIn("Request Context Failure", result.error or "")

    def test_badge_timeline_summary_tracks_partial_date_sources(self):
        summary = service._buildBadgeTimelineSummary(
            [
                {
                    "id": 123,
                    "awardedDate": "2024-01-02T03:04:05Z",
                    "awardedDateSource": "badge_history_next_cursor",
                }
            ],
            awardDateStatus="PARTIAL",
            awardDateError="Badge award-date lookup is unavailable from Roblox (403).",
        )

        self.assertEqual(summary["datedBadges"], 1)
        self.assertEqual(summary["awardDateStatus"], "PARTIAL")
        self.assertEqual(summary["awardDateSources"], {"badge_history_next_cursor": 1})

    def test_badge_overview_does_not_report_zero_dated_awards_on_api_error(self):
        report = _report(
            badgeTimelineSummary={
                "sampleSize": 125,
                "datedBadges": 0,
                "awardDateStatus": "ERROR",
                "historyComplete": True,
                "quality": "undated",
                "awardDateError": "Badge award-date lookup is unavailable from Roblox (403).",
            }
        )

        line = rendering._overviewBadgeLine(report)

        self.assertIn("Roblox award dates are currently unavailable", line)
        self.assertNotIn("0** dated", line)


class RobloxInventoryValueTests(unittest.TestCase):
    def test_inventory_value_excludes_self_created_assets(self):
        summary = robloxInventoryApi._inventoryValueSummary(
            {
                1: {"price": 100, "creatorId": 456, "creatorType": "User"},
                2: {"price": 50, "creatorId": 999},
                3: {"price": 25},
                4: {"price": None, "creatorId": 999},
                5: {"price": 200, "creatorId": 456, "creatorType": "Group"},
            },
            ownerRobloxUserId=456,
            uniqueAssetCount=5,
        )

        self.assertEqual(summary["knownValueRobux"], 275)
        self.assertEqual(summary["pricedAssetCount"], 3)
        self.assertEqual(summary["unpricedAssetCount"], 1)
        self.assertEqual(summary["selfCreatedAssetCount"], 1)
        self.assertEqual(summary["selfCreatedPricedAssetCount"], 1)
        self.assertEqual(summary["selfCreatedRobuxExcluded"], 100)

    def test_gamepass_value_excludes_self_created_gamepasses(self):
        summary = robloxGamepasses._gamepassValueSummary(
            [
                {"price": 100, "creatorId": 456, "creatorType": "User"},
                {"price": 50, "creatorId": 999},
                {"price": None, "creatorId": 999},
                {"price": 200, "creatorId": 456, "creatorType": "Group"},
            ],
            ownerRobloxUserId=456,
        )

        self.assertEqual(summary["totalRobux"], 250)
        self.assertEqual(summary["pricedGamepasses"], 2)
        self.assertEqual(summary["unpricedGamepasses"], 1)
        self.assertEqual(summary["selfCreatedGamepassCount"], 1)
        self.assertEqual(summary["selfCreatedPricedGamepassCount"], 1)
        self.assertEqual(summary["selfCreatedRobuxExcluded"], 100)


class RobloxInventoryVisualTests(unittest.IsolatedAsyncioTestCase):
    async def test_visual_hash_matching_requires_compatible_asset_types(self):
        oldHashLookup = robloxInventoryVisual.fetchRobloxAssetThumbnailHashes
        oldPriceLookup = robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices
        oldEnabled = config.bgIntelligenceInventoryVisualMatchingEnabled
        oldDistance = config.bgIntelligenceInventoryVisualHashDistanceMax

        async def fakeHashLookup(assetIds):
            return {int(assetId): "0" * 16 for assetId in assetIds}, None

        async def fakePriceLookup(assetIds):
            details = {
                100: {"assetTypeId": 11, "assetTypeName": "Shirt"},
            }
            return {int(assetId): details[int(assetId)] for assetId in assetIds if int(assetId) in details}, None

        try:
            config.bgIntelligenceInventoryVisualMatchingEnabled = True
            config.bgIntelligenceInventoryVisualHashDistanceMax = 3
            robloxInventoryVisual.fetchRobloxAssetThumbnailHashes = fakeHashLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = fakePriceLookup

            flaggedItemsById = {}
            summary = await robloxInventoryVisual.applyInventoryVisualMatches(
                flaggedItemsById=flaggedItemsById,
                candidateItems=[
                    {
                        "id": 200,
                        "name": "Brown Hat",
                        "itemType": "Hat",
                        "assetTypeId": 8,
                        "visualCategory": "hat",
                    },
                    {
                        "id": 201,
                        "name": "Black Green Shirt",
                        "itemType": "Shirt",
                        "assetTypeId": 11,
                        "visualCategory": "classic_shirt",
                    },
                ],
                referenceItemIds={100},
                referenceHashes={100: "0" * 16},
            )
        finally:
            robloxInventoryVisual.fetchRobloxAssetThumbnailHashes = oldHashLookup
            robloxInventoryVisual.robloxAssets.fetchCatalogAssetPrices = oldPriceLookup
            config.bgIntelligenceInventoryVisualMatchingEnabled = oldEnabled
            config.bgIntelligenceInventoryVisualHashDistanceMax = oldDistance

        self.assertEqual(summary["matchedCount"], 1)
        self.assertGreaterEqual(summary["skippedTypeMismatchCount"], 1)
        self.assertNotIn(200, flaggedItemsById)
        self.assertEqual(flaggedItemsById[201]["matchType"], "visual")


class BgIntelligenceViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_rerun_requests_username_for_discord_scan_without_roblox_identity(self):
        report = _report(robloxUserId=None, robloxUsername=None, roverError="No Roblox account linked via RoVer.")
        score = scoring.scoreReport(report)

        view = BgIntelDetailsView(ownerId=1, report=report, riskScore=score, reportId=0)

        self.assertTrue(view._needsRobloxUsernameForRerun())

    async def test_rerun_does_not_request_username_when_roblox_identity_exists(self):
        report = _report(robloxUserId=456, robloxUsername="TargetUser")
        score = scoring.scoreReport(report)

        view = BgIntelDetailsView(ownerId=1, report=report, riskScore=score, reportId=0)

        self.assertFalse(view._needsRobloxUsernameForRerun())


class CharacterAltMatcherTests(unittest.TestCase):
    def test_alt_marker_suffix_matches_known_username(self):
        reason = characters.username_alt_match_reason("KnownUserBackup", "KnownUser")

        self.assertIsNotNone(reason)
        self.assertIn("alt", reason or "")

    def test_alternate_characters_match_known_username(self):
        self.assertTrue(characters.looks_like_username_alt("Kn0wn_User", "KnownUser"))

    def test_arbitrary_contains_does_not_match(self):
        self.assertFalse(characters.looks_like_username_alt("RandomKnownUserThing", "KnownUser"))

    def test_exact_same_username_does_not_match(self):
        self.assertFalse(characters.looks_like_username_alt("KnownUser", "KnownUser"))


if __name__ == "__main__":
    unittest.main()
