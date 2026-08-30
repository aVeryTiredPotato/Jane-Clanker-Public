from __future__ import annotations

from .core import *
from .env import _envText

# == Session / BG Check ==
bgCheckChannelId = 0
bgCheckAdultReviewGuildId = 0
bgCheckAdultReviewChannelId = 0
bgCheckMinorReviewGuildId = 0
bgCheckMinorReviewChannelId = 0
bgCheckMinorReviewRoleId = 0
bgCheckMinorReviewRoleIds = []
orientationSpreadsheetForumTagIds = {
    1491904301184974940: 1491904838823579648,
    1491920876848025840: 1491922830751830180,
}
orientationRoverWarmupEnabled = True
orientationRoverWarmupDelaySec = 600
orientationRoverWarmupIntervalSec = 60
bgMinorAgeRoleIds = []
bgMajorAgeRoleIds = []
bgMinorAgeGroups = ["13-15", "16-17", "NO INFO"]
bgAdultAgeGroups = ["18-20", "21+"]
bgUnknownDefaultsToMinor = True
# Source guild for ?bgcheck member collection (defaults to serverId when unset).
bgCheckSourceGuildId = serverId
bgCheckSpreadsheetTemplateId = _envText(
    "BGC_SPREADSHEET_TEMPLATE_ID",
    "",
)
bgCheckSpreadsheetFolderId = _envText(
    "BGC_SPREADSHEET_FOLDER_ID",
    "",
)
bgCheckSpreadsheetSheetName = _envText("BGC_SPREADSHEET_SHEET_NAME", "Sheet1")
bgIntelligenceReportChannelId = 0
bgIntelligenceSheetLookupLimit = 12
bgItemReviewQueueEnabled = True
bgItemReviewQueueChannelId = 0
bgItemReviewReviewerRoleId = bgReviewModeratorRoleId
bgItemReviewWebhookName = "Jane BG Item Review"
bgItemReviewMaxPagesPerType = 4
bgItemReviewCandidateLimit = 60
bgFlagOpenGuildIds = []
bgItemReviewSpreadsheetSyncEnabled = False
bgItemReviewSpreadsheetSyncIntervalSec = 300
bgItemReviewSpreadsheetStartupLookbackDays = 5
bgItemReviewSpreadsheetRecurringLookbackDays = 1
bgItemReviewSpreadsheetSyncScanLimit = 10
bgItemReviewSpreadsheetSyncMaxRows = 250
bgRiskScoreBase = 20
bgRiskScoreFloor = 5
bgIntelligenceFetchGroupsEnabled = True
bgIntelligenceFetchConnectionsEnabled = True
bgIntelligenceFetchUsernameHistoryEnabled = True
bgIntelligenceFetchInventoryEnabled = True
bgIntelligenceFetchGamepassesEnabled = True
bgIntelligenceFetchBadgesEnabled = True
bgIntelligenceFetchFavoriteGamesEnabled = True
bgIntelligenceFetchOutfitsEnabled = True
bgIntelligenceFetchBadgeHistoryEnabled = True
bgIntelligenceKnownMemberAltDetectionEnabled = True
bgIntelligenceFetchFriendIdsEnabled = True
bgIntelligenceExternalSourcesEnabled = True
bgIntelligenceTaseEnabled = True
bgIntelligenceTaseApiBaseUrl = "https://api.tasebot.org"
bgIntelligenceTaseTimeoutSec = 10
bgIntelligenceMocoEnabled = True
bgIntelligenceMocoApiBaseUrl = "https://api.moco-co.org"
bgIntelligenceMocoTimeoutSec = 10
bgIntelligenceFavoriteGameMax = 25
bgIntelligenceOutfitMax = 25
bgIntelligenceUsernameHistoryMax = 50
bgIntelligenceKnownMemberAltMatchLimit = 10
bgIntelligenceKnownMemberAltCandidateLimit = 500
bgIntelligenceKnownMemberAltFuzzyEnabled = False
bgIntelligenceKnownMemberAltFuzzyMinSimilarity = 0.9
bgIntelligenceKnownMemberAltFuzzyMinLength = 5
bgIntelligenceKnownMemberAltGroupOverlapMin = 2
bgIntelligenceKnownMemberAltGroupOverlapMaxMemberCount = 50000
bgIntelligenceKnownMemberAltFriendLimit = 200
bgIntelligenceKnownMemberAltWords = [
    "alt",
    "alts",
    "backup",
    "backups",
    "back_up",
    "bak",
    "bckup",
    "spare",
    "second",
    "secondaccount",
    "account",
    "acct",
    "acc",
    "clone",
    "copy",
    "new",
    "old",
    "main",
    "again",
]
bgIntelligenceInventoryMaxPages = 0
bgIntelligenceInventoryHardMaxPages = 100
bgIntelligencePublicInventoryMaxPagesPerType = 10
bgIntelligenceInventoryFuzzyMatchingEnabled = True
bgIntelligenceInventoryFuzzyScoreCutoff = 92
bgIntelligenceInventoryFuzzyMinKeywordLength = 6
bgIntelligenceInventoryVisualMatchingEnabled = True
bgIntelligenceInventoryVisualCandidateLimit = 120
bgIntelligenceInventoryVisualReferenceLimit = 80
bgIntelligenceInventoryVisualHashDistanceMax = 3
bgIntelligenceInventoryVisualHashSize = 16
bgIntelligenceInventoryVisualColorMatchingEnabled = True
bgIntelligenceInventoryVisualColorDistanceMax = 0.58
bgIntelligenceGamepassMaxPages = 0
bgIntelligenceGamepassHardMaxPages = 100
bgIntelligenceBadgeHistoryPageSize = 100
bgIntelligenceBadgeHistoryMaxPages = 0
bgIntelligenceBadgeHistoryHardMaxPages = 100
bgIntelligencePrivateInventoryDmEnabled = True
bgIntelligenceReportRetentionHours = 24
bgIntelligenceReportIndexRetentionDays = 90
bgIntelligenceIdentityGraphRetentionDays = 365
bgIntelligenceReportPruneCheckIntervalSec = 3600
robloxApiCacheMaxEntries = 5000
robloxProfileCacheTtlSec = 86400
robloxGroupCacheTtlSec = 3600
robloxConnectionCacheTtlSec = 3600
robloxFriendListCacheTtlSec = 3600
robloxFavoriteGamesCacheTtlSec = 3600
robloxOutfitCacheTtlSec = 3600
robloxInventoryValueCacheTtlSec = 21600
robloxGamepassCacheTtlSec = 21600
robloxAssetPriceCacheTtlSec = 86400
robloxAssetThumbnailCacheTtlSec = 86400
robloxAssetThumbnailHashCacheTtlSec = 86400
robloxAssetPriceLookupConcurrency = 16
robloxAssetPriceFallbackMaxAssets = 10
robloxAssetThumbnailHashConcurrency = 10
robloxGamepassProductCacheTtlSec = 86400
robloxBadgeHistoryCacheTtlSec = 86400
robloxBadgeAwardCacheTtlSec = 86400
robloxBadgeAwardLookupConcurrency = 1
robloxBadgeAwardLookupDelaySec = 0.5
trainingResultsChannelId = 0
startupGreetingChannelId = 0
bgFailureForumChannelId = 0

# Training log mirror and John event ingest.
johnTrainingLogChannelId = 0
trainingArchiveChannelId = johnTrainingLogChannelId
trainingLogBackfillDays = 2
trainingLogStartupSyncDelaySec = 90
trainingLogStartupBackfillMessageLimit = 250
trainingLogArchiveIndexLimit = 500
trainingLogStartupMirrorNewRows = True
orbatStartupMaintenanceDelaySec = 45
trainingSummaryWebhookName = "Jane Training Summary"
trainingMirrorWebhookName = "Jane Training Log"
johnEventLogChannelId = 0
johnClankerBotId = 0

honorGuardEnabled = True
honorGuardCommandGuildIds = []
honorGuardReviewChannelId = 0
honorGuardLogChannelId = 0
honorGuardArchiveChannelId = 0
honorGuardOrbatAuditChannelId = 0
honorGuardSpreadsheetId = _envText(
    "HONOR_GUARD_SPREADSHEET_ID",
    "1KwNuScn-19IO56AUf0fEmgRrthgS4Vrf0zVFDgxV6Do",
)
honorGuardMemberSheetName = "Main"
honorGuardCMPSheetName = "Cavalry"
honorGuardScheduleSheetName = "Event Scheduling"
honorGuardArchiveSheetName = "Event Archive"
honorGuardEventHostsSheetName = "Event Hosts"
honorGuardCredentialsPathEnvVar = "ORBAT_GOOGLE_CREDENTIALS_PATH"
honorGuardCredentialsPathConfigKey = "orbatGoogleCredentialsPath"

# Honor Guard member sheet columns.
honorGuardDiscordIdColumn = ""
honorGuardRobloxUsernameColumn = "A"
honorGuardRankColumn = "B"
honorGuardActivityStatusColumn = "G"
honorGuardQuotaPointsColumn = "E"
honorGuardEventPointsColumn = "J"
honorGuardAwardedPointsColumn = "K"
honorGuardTotalPlatoonPointsColumn = "L"
honorGuardTotalPointsColumn = "M"
# Legacy aliases retained for settings/operations.py rowModel.
honorGuardPromotionEventPointsColumn = honorGuardEventPointsColumn
honorGuardPromotionAwardedPointsColumn = honorGuardAwardedPointsColumn
honorGuardPromotionTotalPointsColumn = honorGuardTotalPointsColumn
honorGuardHostedEventsColumn = ""
honorGuardJuniorExamPassedColumn = "N"
honorGuardNcoExamPassedColumn = "O"
honorGuardQuotaCompleteFormulaColumn = "F"
honorGuardPromotionEligibleFormulaColumn = "P"
honorGuardStrikesColumn = "Q"

# Honor Guard schedule/archive sheet columns.
honorGuardScheduleEventIdColumn = ""
honorGuardScheduleEventTypeColumn = "A"
honorGuardScheduleEventTimeColumn = "B"
honorGuardScheduleHostColumn = "C"
honorGuardScheduleCoHostsColumn = "D"
honorGuardScheduleSupervisorsColumn = "E"
honorGuardScheduleEventDetailColumn = "G"
honorGuardScheduleNotesColumn = "H"
honorGuardScheduleStatusColumn = ""
honorGuardArchiveColumns = [
    "eventType",
    "eventTimeUtc",
    "host",
    "coHosts",
    "supervisors",
    "eventDuration",
    "eventDetail",
    "notes",
]
honorGuardEventHostUsernameColumn = "A"
honorGuardEventHostTotalEventsColumn = "F"
honorGuardEventHostExamsColumn = "G"
honorGuardEventHostTrainingsColumn = "H"
honorGuardEventHostTryoutsColumn = "I"
honorGuardEventHostInspectionsColumn = "J"
honorGuardEventHostEventTypeColumns = {
    "jge": honorGuardEventHostExamsColumn,
    "junior guardsman exam": honorGuardEventHostExamsColumn,
    "ncoe": honorGuardEventHostExamsColumn,
    "nco exam": honorGuardEventHostExamsColumn,
    "orientation": honorGuardEventHostTrainingsColumn,
    "training": honorGuardEventHostTrainingsColumn,
    "lecture": honorGuardEventHostTrainingsColumn,
    "drill": honorGuardEventHostTrainingsColumn,
    "tryout": honorGuardEventHostTryoutsColumn,
    "honor guard tryout": honorGuardEventHostTryoutsColumn,
    "inspection": honorGuardEventHostInspectionsColumn,
    "mock inspection": honorGuardEventHostInspectionsColumn,
}

# Honor Guard ranks and point rules.
honorGuardEnlistedRanks = [
    "Jr Guardsman",
    "Junior Guardsman",
    "Guardsman",
]
honorGuardNcoRanks = [
    "Sr Guardsman",
    "Senior Guardsman",
    "Patrol Sergeant",
]
honorGuardOfficerRanks = [
    "Parade Officer",
    "Senior Parade Officer",
    "Honor Guard Officer",
    "Commanding Officer",
]
honorGuardExcuseStatusValues = [
    "Excused",
    "LoA",
    "LOA",
    "Retired",
    "N/A",
    "New",
]
honorGuardBiweeklyQuotaPointsRequired = 4
honorGuardEarlyActiveQuotaPoints = 8
honorGuardSoloSentryDutyMinutesRequired = 30
honorGuardSoloSentryDutyEventPoints = 1
honorGuardAttendanceQuotaPointsByEventType = {
    "gamenight": 0.5,
}
honorGuardAttendanceEventPointsByEventType = {
    "drill" : {
        "base": 2,
        "per_intervall": 0,
        "minimum": 0
    },
    "inspection": {
        "base": 8,
        "per_intervall": 0,
        "minimum": 0
    },
    "sentry": {
        "base": 0,
        "per_intervall": 1,
        "minimum": 0
    },
    "tryout": {
        "base": 0,
        "per_intervall": 1,
        "minimum": 0
    },
    "orientation": {
        "base": 0,
        "per_intervall": 1,
        "minimum": 2
    },
    "jge": {
        "base": 5,
        "per_intervall": 0,
        "minimum": 0
    },
    "ncoe": {
        "base": 5,
        "per_intervall": 0,
        "minimum": 0
    },
    "gamenight": {
        "base": 1,
        "per_intervall": 0,
        "minimum": 0
    },
}
honorGuardHostEventPointsByEventType = {
    "gamenight": 1,
    "orientation": 2,
    "drill": 3,
    "lecture": 3,
    "tryout": 6,
    "inspection": 8,
}
honorGuardSupervisorEventPointsByEventType = {
    "orientation": {
        "base": 0,
        "per_intervall": 1,
        "minimum": 2
    },
    "drill" : {
        "base": 0,
        "per_intervall": 0,
        "minimum": 0
    },
}
honorGuardJgePointsPerGradedAttendee = 0.75
honorGuardNcoExamPointsPerGradedAttendee = 1.5
honorGuardNcoExamScreenAssistPoints = 2

# Discord role IDs used to bucket a Honor Guard attendee at clock-in time.
# A user is classified by the first matching bucket in order: enlisted, nco, officer.
honorGuardEnlistedRoleIds: list[int] = [
    1477788769641037826, # Junior Guardsman
    1477788768256917786  # Guardsman
]
honorGuardNcoRoleIds: list[int] = [
    1477788766583259309, # Senior Guardsman
    1478133203788107827  # Platoon Sergeant
]
honorGuardOfficerRoleIds: list[int] = [
	1477788764804743400, # Parade Officer
	1533539376502669312, # Parade Captain
	1477788762774835333, # Parade Marshal
	1477788760866427192, # Inspector General
	1533539146856140870, # Platoon Corporal
	1477788754805788875  # HG Commandant
]

honorGuardChannelId: int = 1477718269921202226

honorGuardActivePlatoons = ["CMP"]
honorGuardAllowedRanks = [
    "Commandant",
    "Deputy Commandant",
    "Oversight",
    "Inspector General",
    "Board Advisor",
    "Parade Marshal",
    "Parade Captain",
    "Parade Officer",
    "Platoon Sergeant",
    "Platoon Corporal",
    "Senior Guardsman",
    "Guardsman",
    "Junior Guardsman",
]
honorGuardPlatoonAllowedRanks = {
    "cmp": [
        "Major",
        "Captain",
        "Lieutenant",
        "Sergeant",
        "Corporal",
        "Trooper First Class",
        "Trooper",
    ],
}
honorGuardMemberSectionHeaders = [
    "IGB",
    "CO",
    "NCO",
    "Guardsman",
    "RETIRED",
]
honorGuardPlatoonSectionHeaders = [
    "HR",
    "MR",
    "LR",
    "RETIRED",
]

# Honor Guard platoon sheet columns.
honorGuardPlatoonDiscordIdColumn = ""
honorGuardPlatoonRobloxUsernameColumn = "A"
honorGuardPlatoonRankColumn = "B"
honorGuardPlatoonPointsColumn = "D"

canCreateVoiceChatAll = [
    1376949984750206986,
    1399386519256563793,
    1416982954285989998,
    1376949919100698814
]
canCreateVoiceChatBasic = [
    1456604223407001601,
    1376949984750206986,
    1399386519256563793,
    1416982954285989998,
    1376949919100698814
]
