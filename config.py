from __future__ import annotations

import os

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(), override=True)

# Config editing guide:
# - Secrets and tokens should come from `.env` through `_envText` / `_envFlag`.
# - Shared guild IDs and role IDs live near the top because many feature sections reference them.
# - Feature-specific channels, role allowlists, quotas, and tuning live in that feature's section.
# - Test-only command scopes should use `testGuildIds` so both test servers stay in sync.


def _envText(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _envFlag(name: str, default: bool = False) -> bool:
    rawValue = os.getenv(name)
    if rawValue is None:
        return bool(default)
    return rawValue.strip().lower() in {"1", "true", "yes", "on"}


# == Core Bot ==
# Jane's Discord bot token. Keep this in `.env`, not in versioned config.
token = _envText("DISCORD_BOT_TOKEN")

# Primary servers.
serverId = 0
serverIdTesting = 0
testGuildIds = []


# == Credentials / External APIs ==
# Keep API keys and credential paths in `.env`, not in versioned config.
# Roblox, RoVer, and Sheets credentials.
robloxOpenCloudApiKey = _envText("ROBLOX_OPEN_CLOUD_API_KEY")
roverApiKey = _envText("ROVER_API_KEY")
orbatGoogleCredentialsPath = _envText("ORBAT_GOOGLE_CREDENTIALS_PATH")
googleOauthClientSecretsPath = _envText("GOOGLE_OAUTH_CLIENT_SECRETS_PATH")
googleOauthTokenPath = _envText("GOOGLE_OAUTH_TOKEN_PATH", "localOnly/credentials/google-oauth-token.json")

# Optional dedicated inventory key.
robloxInventoryApiKey = _envText("ROBLOX_INVENTORY_API_KEY", robloxOpenCloudApiKey)

# Feature-specific external service credentials.
bgIntelligenceTaseApiToken = _envText("TASE_API_TOKEN")
bgIntelligenceMocoApiKey = _envText("MOCO_API_KEY")
gamblingApiToken = _envText("JANE_GAMBLING_API_TOKEN")
freedcampApiKey = _envText("FREEDCAMP_API_KEY")
freedcampSecret = _envText("FREEDCAMP_SECRET")

# == Command Access / Runtime ==
# Allowed servers for command usage.
allowedCommandGuildIds = []

# Reserved runtime override users.
overridingUserIds = []
# Command sync toggles.
clearGlobalCommands = False
clearGuildCommands = False

# Temporary command lock.
temporaryCommandLockEnabled = False
temporaryCommandAllowedUserIds = []

# Runtime / diagnostics access.
errorMirrorUserId = 0
janeTerminalAllowedUserId = errorMirrorUserId
opsAllowedUserIds = []
runtimeControlAllowedUserIds = []
permissionSimulatorGuildIds = []

# Runtime task tuning.
sessionMessageUpdateDebounceSec = 0.75
runtimeBudgetRobloxConcurrency = 6
runtimeBudgetSheetsConcurrency = 2
runtimeBudgetInteractiveSheetsConcurrency = 2
runtimeBudgetBackgroundSheetsConcurrency = 1
runtimeBudgetDiscordConcurrency = 6
runtimeBudgetInteractionAckConcurrency = 24
runtimeBudgetBackgroundConcurrency = 2
runtimeTaskStatsPath = "runtime/data/task-stats.json"
runtimeTaskStatsFlushIntervalSec = 30
runtimeTaskStatsFlushDirtyCount = 25
discordEntityCacheTtlSec = 300
bgQueueUpdateConcurrency = 2
sessionMessageUpdateConcurrency = 2
bgQueueRepostConcurrency = 1
retryQueuePollIntervalSec = 6
webhookHealthCheckIntervalSec = 600
generalErrorLogDir = ""
generalErrorLogMaxBytes = 2 * 1024 * 1024
generalErrorLogBackupCount = 5
automationReportChannelId = 0
autoGitUpdateEnabled = False
enablePrivateExtensions = False
enableDestructiveCommands = False
destructiveCommandsDryRun = True
allowGitPullOnManualRestart = False
autoGitUpdateRemote = "origin"
autoGitUpdateBranch = ""
autoGitUpdateCheckIntervalSec = 60
autoGitUpdateInitialDelaySec = 120
autoGitUpdatePauseDrainSec = 5
autoGitUpdateInstallRequirements = _envFlag("JANE_INSTALL_REQUIREMENTS_ON_UPDATE", True)
autoGitUpdateDependencyInstallTimeoutSec = 600
autoGitUpdatePreservePaths = [
    "backups/serverSnapshots",
    "backups/serverSnapshotsOffsite",
]
copyServerRoleBatchCreateLimit = 12
copyServerRoleBatchMutationLimit = 18

# Optional extension layers.
extraExtensionNames: list[str] = []
destructiveCommandGuildIds = []
destructiveCommandCooldownSec = 30

# Optional config sanity suppressions (ID keys intentionally left unset).
configSanityOptionalIdKeys = [
    "bestOfFormerMrRoleId",
    "bestOfFormerHrRoleId",
    "bestOfFormerAnrocomRoleId",
    "bestOfAnrocomRoleId",
    "projectHodRoleIds",
    "projectAssistantDirectorRoleIds",
]


# == Shared Role IDs ==
# Core moderation / training roles.
moderatorRoleId = 0  # BG Check Certified
bgReviewModeratorRoleId = 0  # BG reviewers in the review server
instructorRoleId = 0  # Training and Qualifications
newApplicantRoleId = 0  # New Applicant
pendingBgRoleId = 0  # Pending Background Check

# Shared rank / clearance roles.
middleRankRoleId = 0
highRankRoleId = 0
cnoRoleId = 0
dooRoleId = 0
ddooRoleId = 0
sectionChiefRoleId = 0
commandStaffRoleId = 0
foiRoleId = 0
crsRoleId = 0
shiftSupervisorRoleId = 0
juniorSuRoleId = 0
msbRoleId = 0

# Recruitment / ANRORS roles.
recruiterRoleId = 0  # CE Recruitment submitter role
recruitmentReviewerRoleId = 0
recruitmentReviewerPingRoleId = 0
anrorsMemberRoleId = 0  # ANRO Recruitment Services
anrorsRmPlusRoleId = 0  # ANRORS RM+

# Honor Guard roles.
honorGuardReviewerRoleId = 0
honorGuardReviewerPingRoleId = 0

# ANRD role placeholders (for future role -> ORBAT rank sync).
anrdRoleProbationaryId = 0
anrdRoleContributorId = 0
anrdRoleSeniorContributorId = 0
anrdRoleDeveloperId = 0
anrdRoleSeniorDeveloperId = 0
anrdRoleDevelopmentProjectLeadId = 0
# Only ORBAT ranks (no Discord role mapping):
# - Development Oversight
# - Development Creator and Director
anrdRoleDevelopmentOversightId = 0
anrdRoleDevelopmentCreatorAndDirectorId = 0
anrdFundingBenefactorsRoleId = 0


# == Session / BG Check ==
bgCheckChannelId = 0
bgCheckAdultReviewGuildId = 0
bgCheckAdultReviewChannelId = 0
bgCheckMinorReviewGuildId = 0
bgCheckMinorReviewChannelId = 0
bgCheckMinorReviewRoleId = 0
bgCheckMinorReviewRoleIds = []
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
bgItemReviewQueueEnabled = True
bgItemReviewQueueChannelId = 0
bgItemReviewReviewerRoleId = bgReviewModeratorRoleId
bgItemReviewWebhookName = "Jane BG Item Review"
bgItemReviewMaxPagesPerType = 4
bgItemReviewCandidateLimit = 60
bgItemReviewSpreadsheetSyncEnabled = True
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
bgIntelligenceKnownMemberAltCandidateLimit = 5000
bgIntelligenceKnownMemberAltFuzzyEnabled = True
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
bgIntelligenceInventoryVisualHashSize = 8
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
trainingLogStartupSyncDelaySec = 20
orbatStartupMaintenanceDelaySec = 45
trainingSummaryWebhookName = "Jane Training Summary"
trainingMirrorWebhookName = "Jane Training Log"
johnEventLogChannelId = 0
johnClankerBotId = 0

honorGuardEnabled = False
honorGuardCommandGuildIds = []
honorGuardReviewChannelId = 0
honorGuardLogChannelId = 0
honorGuardArchiveChannelId = 0
honorGuardSpreadsheetId = _envText(
    "HONOR_GUARD_SPREADSHEET_ID",
    "1aLD68JNA2nRjTxG1c3DZOtb_DPRXE4uNKN7ZxpKo_0k",
)
honorGuardMemberSheetName = "Main"
honorGuardScheduleSheetName = "Event Scheduling"
honorGuardArchiveSheetName = "Event Archive"
honorGuardEventHostsSheetName = "Event Hosts"
honorGuardCredentialsPathEnvVar = "ORBAT_GOOGLE_CREDENTIALS_PATH"
honorGuardCredentialsPathConfigKey = "orbatGoogleCredentialsPath"

# Honor Guard member sheet columns.
honorGuardDiscordIdColumn = ""
honorGuardRobloxUsernameColumn = "A"
honorGuardRankColumn = "B"
honorGuardActivityStatusColumn = "H"
honorGuardQuotaPointsColumn = "E"
honorGuardPromotionEventPointsColumn = "K"
honorGuardPromotionAwardedPointsColumn = "L"
honorGuardPromotionTotalPointsColumn = ""
honorGuardHostedEventsColumn = ""
honorGuardJuniorExamPassedColumn = "N"
honorGuardNcoExamPassedColumn = "O"
honorGuardQuotaCompleteFormulaColumn = "G"
honorGuardPromotionEligibleFormulaColumn = "P"
honorGuardStrikesColumn = "Q"

# Honor Guard schedule/archive sheet columns.
honorGuardScheduleEventIdColumn = ""
honorGuardScheduleEventTypeColumn = "A"
honorGuardScheduleEventTimeColumn = "B"
honorGuardScheduleHostColumn = "C"
honorGuardScheduleCoHostsColumn = "D"
honorGuardScheduleSupervisorsColumn = "E"
honorGuardScheduleEventDetailColumn = "F"
honorGuardScheduleNotesColumn = "G"
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
    "nco_exam": honorGuardEventHostExamsColumn,
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
honorGuardSentryDutyMinutesRequired = 30
honorGuardSentryDutyQuotaPoints = 1
honorGuardSentryDutyPromotionPoints = 1
honorGuardAttendanceQuotaPointsByEventType = {
    "gamenight": 0.5,
}
honorGuardAttendancePromotionPointsByEventType = {
    "inspection": 8,
    "sentry": 1,
}
honorGuardOfficerHostPromotionPointsByEventType = {
    "gamenight": 1,
    "orientation": 2,
    "training": 3,
    "lecture": 3,
    "tryout": 6,
    "inspection": 8,
}
honorGuardOfficerSupervisorPromotionPointsByEventType = {
    "orientation": 2,
}
honorGuardOfficerCohostPromotionPointsByEventType = {
    "inspection": 8,
}
honorGuardJgePointsPerGradedAttendee = 0.75
honorGuardNcoExamPointsPerGradedAttendee = 1.5
honorGuardNcoExamScreenAssistPoints = 2


# == Internal Link Hub ==
masterLinkHubManagerRoleIds = []
masterLinkHubWebhookName = "Jane Master Directory"


# == Public Utility / Suggestions ==
welcomeChannelId = 0
welcomeMessageTemplate = "Welcome to **{guild}**, {mention}."
publicRoleMenus = {}
reactionRoleCommandRoleIds = []
reactionRolePolicyRoleIds = []

suggestionChannelId = 0
suggestionForumChannelId = 0
suggestionReviewerRoleIds = []

# Optional Freedcamp task creation when suggestions are approved.
freedcampProjectId = 0
freedcampTaskGroupId = 0


# == Server Safety / Recovery ==
serverSafetyAlertChannelId = 0
serverSafetyAlertRoleId = 0
serverSafetySnapshotDir = ""
serverSafetyOffsiteSnapshotDir = ""
serverSafetyOffsiteSnapshotsEnabled = True
serverSafetyWeeklySnapshotKeepCount = 2
serverSafetyManualSnapshotKeepCount = 1
serverSafetyWeeklySnapshotGuildIds = []
serverSafetyQuarantineEnabled = False
serverSafetyIgnoredCategoryIds = []
serverSafetyPreservedChannelIds = []
serverSafetyQuarantineThreshold = 5
serverSafetyQuarantineWindowSec = 30
serverSafetyAllowedUserIds = []


# == Project Workflow ==
# Empty means /project can be used in any guild already allowed above.
projectCommandGuildIds = []
projectAutoCreateThread = True
projectHodRoleIds = []
projectAssistantDirectorRoleIds = []


# == Division Applications ==
divisionApplicationsConfigPath = "configData/divisions.json"
divisionApplicationsCooldownMinutes = 30
divisionApplicationsMaxActivePerDivision = 100

# Optional mapping for app division keys -> Department ORBAT division keys.
divisionOrbatSeedKeyMap = {
    "LO": "LO",
    "LOGISTICS": "LO",
    "LORE": "ANLD",
    "NIRI": "NIRI",
    "A&A": "A&A",
    "AA": "A&A",
    "MSB": "MSB",
}

# Optional mapping for app division keys -> initial ORBAT rank when first seeded.
divisionOrbatSeedStartRankMap = {
    "NIRI": "Researcher",
}

divisionApplicationsAdminRoleIds = []
divisionApplicationsGlobalReviewerRoleIds = []

# Roles allowed to use `!applications <divisionKey> <open|close|status>`.
divisionApplicationsControlRoleIds = []


# == Division Clock-in ==
# If empty, administrators/manage-server can still start sessions.
divisionClockinAllowedRoleIds = []

# Subdepartment -> sheet mapping placeholder for future wiring.
# Example:
# "anrd": {"sheetKey": "dept_anrd", "sheetName": "ANRD"}


# == ORBAT / LOA ==
# Review / audit channels.
orbatReviewChannelId = 0
loaReviewChannelId = 0
orbatAuditChannelId = 0

# Master switch for non-recruitment ORBAT writes.
nonRecruitmentOrbatWritesEnabled = False

# General staff ORBAT workbook.
orbatSpreadsheetId = 0
orbatSheetName = "General Staff"

# Recruitment / department ORBAT workbooks.
recruitmentSpreadsheetId = 0
deptSpreadsheetId = 0

# Department ORBAT layouts live in a separate JSON file.
departmentOrbatLayoutsPath = "features/staff/departmentOrbat/layouts.json"

# ORBAT submit / review access.
orbatSubmitterRoleIds = []
orbatReviewerRoleIds = []
orbatWriteGuildIds = []

# LOA roles (apply based on the submitter's rank role).
orbatLoaRoleMap = {
    middleRankRoleId: 1460770067909185657,  # Middle Rank -> Leave Of Absence [MR]
    highRankRoleId: 1460770421325693116,  # High Rank -> Leave Of Absence [HR]
    # ANROCOM role id -> 1470838628065214534
}

# ORBAT columns (A1 notation).
# The current General Staff workbook has no Discord ID column. Keep this blank
# so Jane does not treat the strike/status column as a Discord ID field.
orbatColumnDiscordId = 0
orbatColumnRobloxUser = "B"
orbatColumnRank = "D"
orbatColumnClearance = "E"
orbatColumnStatus = "G"
orbatColumnLoaInfo = "H"
orbatColumnDepartment = "J"
orbatColumnNotes = "K"
orbatColumnMic = "R"
orbatColumnTimezone = "S"
orbatColumnAgeGroup = "T"
orbatColumnShifts = "M"
orbatColumnOtherEvents = "N"
orbatColumnTotal = "O"
orbatColumnAllTime = "P"

# Role mappings.
orbatRoleRankMap = {
    cnoRoleId: "J - Chief Nuclear Officer",
    dooRoleId: "I - Director of Operations",
    ddooRoleId: "H - Deputy DoO",
    sectionChiefRoleId: "G - Section Chief",
    commandStaffRoleId: "F - Command Staff",
    foiRoleId: "E - Field Operations Inspector",
    crsRoleId: "D - Control Room Supervisor",
    shiftSupervisorRoleId: "C - Shift Supervisor",
    juniorSuRoleId: "B - Junior SU",
}

orbatRoleClearanceMap = {
    cnoRoleId: "1IC",  # Chief Nuclear Officer
    dooRoleId: "2IC",  # Director of Operations
    ddooRoleId: "3IC",  # Deputy DoO
    sectionChiefRoleId: "ADMINISTRATIVE",  # Section Chief
    msbRoleId: "MODERATION",  # Moderation Services Bureau
}

# Priority order (highest rank wins).
orbatRolePriority = [
    cnoRoleId,
    dooRoleId,
    ddooRoleId,
    sectionChiefRoleId,
    commandStaffRoleId,
    foiRoleId,
    crsRoleId,
    shiftSupervisorRoleId,
    juniorSuRoleId,
]

# Allowed dropdown values (must match sheet validation lists).
orbatAllowedRanks = [
    "J - Chief Nuclear Officer",
    "I - Director of Operations",
    "H - Deputy DoO",
    "G - Section Chief",
    "F - Command Staff",
    "E - Field Operations Inspector",
    "D - Control Room Supervisor",
    "C - Shift Supervisor",
    "B - Junior SU",
    "A - Retired",
    "0 - Decommisioned",
]

orbatAllowedClearances = [
    "1IC",
    "2IC",
    "3IC",
    "4IC",
    "ANROCOM",
    "ADMINISTRATIVE",
    "MODERATION",
    "NILL",
]

orbatAllowedStatuses = [
    "Active",
    "Inactive",
    "LoA",
    "Retired",
    "Decommisioned",
    "?",
]

orbatAllowedDepartments = [
    "ANROCOM",
    "INTERNAL AFFAIRS & HR",
    "TRAINING & QUALIFICATION",
    "LOGISTIC & OPERATIONS (LO)",
    "COMMUNITY ENGAGEMENT",
    "GENERAL ADMINISTRATION",
    "MIDDLE RANK MANAGEMENT",
    "MODERATION SERVICES BUREAU",
    "ANROCOM SECRETARY",
    "AUDIT & ASSURANCE (A&A)",
    "ANRO DEVELOPMENT",
    "NILL",
]

orbatAllowedMic = ["Yes", "No", "?"]
orbatAllowedAgeGroups = ["21+", "18-20", "16-17", "13-15", "NO INFO"]
orbatDefaultMic = "?"
orbatDefaultAgeGroup = "NO INFO"

# ORBAT row styling.
orbatBandingPrimaryHex = "#f3f3f3"
orbatBandingSecondaryHex = "#d9d9d9"
orbatRowFontSize = 13
orbatRowBold = True

# Weekly ORBAT organization schedule (UTC). Weekday: Monday=0 ... Sunday=6.
orbatOrganizationUtcHour = 3
orbatOrganizationUtcMinute = 0
orbatOrganizationUtcWeekday = 6

# Role-based ORBAT sync runtime controls.
roleOrbatSyncEnabled = True
roleOrbatSyncMinIntervalSec = 600
roleOrbatSyncMappings = [
    {
        "syncType": "recruitment.anrorsPlacement",
        "enabled": True,
        "memberRoleId": anrorsMemberRoleId,
        "rmPlusRoleId": anrorsRmPlusRoleId,
        "requireAnyRole": True,
        "organizeAfter": True,
    },
    {
        "syncType": "department.anrdRankByRole",
        "enabled": True,
        "divisionKey": "ANRD",
        "roleRankMap": {
            anrdRoleDevelopmentProjectLeadId: "Development Project Lead",
            anrdRoleSeniorDeveloperId: "Senior Developer",
            anrdRoleDeveloperId: "Developer",
            anrdRoleContributorId: "Contributor",
            anrdRoleProbationaryId: "Probationary",
        },
        "rolePriority": [
            anrdRoleDevelopmentProjectLeadId,
            anrdRoleSeniorDeveloperId,
            anrdRoleDeveloperId,
            anrdRoleContributorId,
            anrdRoleProbationaryId,
        ],
        "requireMappedRole": True,
        "organizeAfter": True,
        "fundingRoleId": anrdFundingBenefactorsRoleId,
    },
]

# Shared multi-ORBAT registry.
multiOrbatSheets = [
    {
        "key": "generalStaff",
        "displayName": "General Staff ORBAT",
        "spreadsheetId": orbatSpreadsheetId,
        "sheetName": orbatSheetName,
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "rowModel": {
            "identity": {
                "discordIdColumn": orbatColumnDiscordId,
                "robloxUserColumn": orbatColumnRobloxUser,
            },
            "eventColumns": {
                "shifts": orbatColumnShifts,
                "otherEvents": orbatColumnOtherEvents,
                "total": orbatColumnTotal,
                "allTime": orbatColumnAllTime,
            },
            "profileColumns": {
                "rank": orbatColumnRank,
                "clearance": orbatColumnClearance,
                "status": orbatColumnStatus,
                "loaInfo": orbatColumnLoaInfo,
                "department": orbatColumnDepartment,
                "notes": orbatColumnNotes,
                "mic": orbatColumnMic,
                "timezone": orbatColumnTimezone,
                "ageGroup": orbatColumnAgeGroup,
            },
        },
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "recruitment",
        "displayName": "Recruitment ORBAT",
        "spreadsheetId": recruitmentSpreadsheetId,
        "sheetName": "ANRORS",
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "rowModel": {
            "identity": {
                "robloxUserColumn": "B",
            },
            "pointColumns": {
                "monthly": "D",
                "allTime": "E",
                "patrols": "F",
            },
            "profileColumns": {
                "rank": "C",
                "quota": "G",
                "status": "H",
                "loaExpiration": "I",
                "notes": "J",
            },
        },
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "honorGuard_members",
        "displayName": "Honor Guard ORBAT",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardMemberSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "rowModel": {
            "identity": {
                "discordIdColumn": honorGuardDiscordIdColumn,
                "robloxUserColumn": honorGuardRobloxUsernameColumn,
            },
            "pointColumns": {
                "quota": honorGuardQuotaPointsColumn,
                "promotionEvent": honorGuardPromotionEventPointsColumn,
                "promotionAwarded": honorGuardPromotionAwardedPointsColumn,
                "promotionTotal": honorGuardPromotionTotalPointsColumn,
                "hostedEvents": honorGuardHostedEventsColumn,
            },
            "profileColumns": {
                "rank": honorGuardRankColumn,
                "status": honorGuardActivityStatusColumn,
                "quotaStatus": honorGuardQuotaCompleteFormulaColumn,
            },
        },
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "honorGuard_schedule",
        "displayName": "Honor Guard Event Schedule",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardScheduleSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": False,
        },
    },
    {
        "key": "honorGuard_archive",
        "displayName": "Honor Guard Event Archive",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardArchiveSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": False,
        },
    },
    {
        "key": "honorGuard_eventHosts",
        "displayName": "Honor Guard Event Hosts",
        "spreadsheetId": honorGuardSpreadsheetId,
        "sheetName": honorGuardEventHostsSheetName,
        "credentialsPathEnvVar": honorGuardCredentialsPathEnvVar,
        "credentialsPathConfigKey": honorGuardCredentialsPathConfigKey,
        "organization": {
            "enabled": honorGuardEnabled,
            "supportsSectionHeaders": True,
        },
    },
    {
        "key": "dept_anrd",
        "displayName": "Department ORBAT - ANRD",
        "spreadsheetId": deptSpreadsheetId,
        "sheetName": "ANRD",
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": False,
        },
    },
    {
        "key": "dept_ce",
        "displayName": "Department ORBAT - CE",
        "spreadsheetId": deptSpreadsheetId,
        "sheetName": "CE",
        "credentialsPathEnvVar": "ORBAT_GOOGLE_CREDENTIALS_PATH",
        "credentialsPathConfigKey": "orbatGoogleCredentialsPath",
        "organization": {
            "enabled": True,
            "supportsSectionHeaders": True,
        },
    },
]

# Global Sheets throttling.
googleSheetsMinRequestIntervalSec = 0.05
googleSheetsMaxAttempts = 3
googleSheetsRetryBaseSec = 1.5

# Discord can occasionally 5xx during the first login/application_info call.
# Retry only startup transport/server failures; invalid token/config errors still fail fast.
discordStartupMaxAttempts = 6
discordStartupRetryBaseSec = 15
discordStartupRetryMaxDelaySec = 120

# Temporary identity backfill command: !pairDbNames
pairDbNamesSourceChannelId = 0
pairDbNamesLookbackDays = 5
pairDbNamesLookupConcurrency = 4
pairDbNamesMaxLookups = 500
pairDbNamesHistoryPageSize = 100
pairDbNamesHistoryMaxAttempts = 5
pairDbNamesHistoryRetryBaseSec = 2
pairDbNamesHistoryRetryMaxDelaySec = 20

# Local ORBAT mirror. This is a read-through cache of member rows, not the
# source of truth for sheet edits.
orbatMirrorEnabled = True
orbatMirrorMaxRows = 800
orbatMirrorMaxColumn = "AZ"
orbatMirrorHeaderScanRows = 12


# == Recruitment / ANRORS ==
recruitmentChannelId = 0
recruitmentTimeLogReviewChannelId = 0
recruitmentPatrolReviewChannelId = 0
recruitmentPatrolEvidenceChannelId = 0
recruitmentCommandGuildIds = []
recruitmentSourceGuildId = serverId

# Optional: roles allowed to host /recruitment-patrol.
# If empty, this falls back to recruiterRoleId permission.
recruitmentPatrolGroupHostRoleIds = []

recruitmentPointsBase = 2
recruitmentPointsOrientationBonus = 3
recruitmentPointsPer15Minutes = 1
recruitmentGroupPatrolPoints = 6
recruitmentPatrolMaxDurationMinutes = 240
recruitmentAutoDetectOrientation = True
recruitmentDivisionKeyAliases = ["recruitment", "anrors"]

# Automatic rank promotions (total points).
recruitmentPromoteRecruiterToSeniorAt = 10
recruitmentPromoteSeniorToLeadAt = 20

# New member defaults.
recruitmentMembersRankOrder = [
    "Recruitment Supervisor",
    "Lead Recruiter",
    "Senior Recruiter",
    "Recruiter",
]
recruitmentNewMemberRank = "Recruiter"
recruitmentNewMemberQuota = 0
recruitmentNewMemberStatus = "Active"
recruitmentManagerQuotaPatrols = 4
recruitmentEmployeeQuotaPoints = 4

# Optional hardcoded cosmetic/footer row. Keep 0 to auto-detect the last non-empty row.
recruitmentFooterRow = 0

# Recruitment ORBAT ranks / sections.
recruitmentAllowedRanks = [
    "Commissioner 1 IC",
    "Comissioner 1 IC",
    "Head Recruiter 1 IC",
    "Head Recruiter 2 IC",
    "Head Recruiter 3 IC",
    "Head Recruiter 4 IC",
    "Recruitment Manager",
    "Recruitment Supervisor",
    "Lead Recruiter",
    "Senior Recruiter",
    "Recruiter",
]

recruitmentSectionHeaders = [
    "High Command",
    "Managers",
    "Employees",
    "Quota not enforced (After 10th)",
    "Dept. Lead",
    "Members",
]

# Section header used for regular recruiter rows.
recruitmentMembersSectionHeaderCandidates = ["Employees", "Members"]

# Quota status values for ANRORS quota column (F).
recruitmentQuotaStatusValues = [
    "Completed",
    "Incomplete",
    "Excused",
    "Failed",
    "Exempt",
]


# == ANRD Payments ==
anrdPaymentReviewChannelId = 0

anrdPaymentSubmitterRoleIds = []
anrdPaymentReviewerRoleIds = []

# ANRD payment manager mapping (ANRD tab, lower section).
anrdMembersStartRow = 3
anrdMembersEndRow = 49
anrdPaymentManagerStartRow = 52
anrdPaymentManagerScanEndRow = 260
anrdDeveloperMonthlyCap = 1200
anrdContributorMonthlyCap = 2000
anrdDeveloperEligibleRanks = [
    "Development Project Lead",
    "Senior Developer",
    "Developer",
]
anrdDeveloperUnlimitedRanks = [
    "Senior Developer",
]
anrdContributorRanks = [
    "Contributer",
    "Contributor",
    "Probationary",
]


# == Ribbon System ==
ribbonRulesPath = "configData/ribbons.json"
ribbonReviewChannelId = 0
ribbonApprovedOutputChannelId = 0

ribbonManagerRoleIds = []
ribbonRequestPingRoleIds = []


# == Best Of ==
# Best Of role priority (lowest -> highest):
# Former MR -> MR -> Former HR -> HR -> Former ANROCOM -> Command Staff -> ANROCOM
bestOfFormerMrRoleId = 0
bestOfMrRoleId = middleRankRoleId
bestOfFormerHrRoleId = 0
bestOfHrRoleId = highRankRoleId
bestOfFormerAnrocomRoleId = 0
bestOfFormerAnrocomRoleIds = []
bestOfCommandStaffRoleId = commandStaffRoleId
bestOfAnrocomRoleIds = []


# == Hall of Fame / Shame ==
hallOfFameChannelId = 0
hallOfShameChannelId = 0
hallReactionThreshold = 5
hallUseWebhook = True
hallIgnoreBotMessages = True
hallAllowedCategoryIds = []


# == Cohost ==
cohostAllowedRoleIds = []
cohostSupervisorRoleId = 0
cohostSupervisorRoleName = "supervisor eligible"
cohostSlotsSolo = 2
cohostSlotsEmergency = 2
cohostSlotsTurbine = 2
cohostSlotsGrid = 4
cohostSlotsShift = 2


# == Voice Chat ==
_canCreateVoiceChatAll = [
    1376949919100698814,
    1376949984750206986,
    1383522027251699772,
    1470914397424717825,
]

_canCreateVoiceChatBasic = [
    1376949919100698814,
    1376949984750206986,
    1383522027251699772,
    1456604223407001601,
]

voiceChannelCreationCategory = 1481169790990029002
permanentVoiceChatChannelIds = []


# == Roblox / RoVer ==
robloxGroupId = 0
robloxGroupUrl = "https://www.roblox.com/communities/36000077/ANRO-Advanced-Noobic-Reactor-Operations#!/about"

# RoVer lookup (Discord -> Roblox). Uses the official RoVer API.
roverApiBaseUrl = "https://registry.rover.link/api/guilds/{guildId}/discord-to-roblox/{discordId}"
roverApiKeyHeader = "Authorization"
roverApiKeyUseBearer = True
roverVerifyUrl = "https://rover.link/verify"
roverCacheTtlSec = 120
roverCacheMaxEntries = 2000
roverIdentityDbTimeoutSec = 1.5
robloxHttpTimeoutSec = 10
recruitmentRoverLookupConcurrency = 8


# == Roblox Flagging / Scanning ==
# If a passing attendee is in any of these groups, they are marked FLAGGED.
robloxFlagGroupIds = []

# Flag Roblox accounts younger than this many days (0 to disable).
robloxAccountAgeFlagDays = 100

# Group scan cache.
robloxGroupScanCacheDays = 7

# Badge scan.
robloxBadgeScanEnabled = True
robloxBadgeScanCacheDays = 7
robloxBadgeScanBatchSize = 100
robloxBadgeImportMax = 200

# Outfit viewer.
robloxOutfitScanEnabled = True
robloxOutfitScanCacheDays = 7
robloxOutfitMax = 0
robloxOutfitMaxPages = 20
robloxOutfitThumbSize = "420x420"

# Inventory scanning.
robloxInventoryScanEnabled = True
robloxInventoryScanCacheDays = 7
robloxInventoryScanMaxPages = 5


# == Gambling API ==
# Use 0.0.0.0 to allow external callers via token auth.
gamblingApiEnabled = True
gamblingApiHost = "0.0.0.0"
gamblingApiPort = 8787
gamblingApiMaxConcurrency = 8
gamblingPointsToDollarRate = 5  # 1 point => 5 anrobucks


# == Hidden / Misc ==
skinAllowedUserIds = []
skinCooldownBypassRoleIds = []


# == Organization Profiles ==
# Jane is still a single bot process, but org-specific settings now live behind
# profile keys so other groups can be added without turning config.py into a
# bigger singleton mess than it already is.
defaultOrganizationKey = "ANRO"
organizationCommandFeatureMap = {
    "orientation": "anro-sessions",
    "bg-check": "anro-bgc",
    "bgcheck": "anro-bgc",
    "bg-intel": "anro-bgc",
    "trainingstats": "anro-training-logs",
    "hoststats": "anro-training-logs",
    "mirrortraininghistory": "anro-training-logs",
    "bgleaderboard": "anro-bgc",
    "bg-leaderboard": "anro-bgc",
    "recruitment": "anro-recruitment",
    "recruitment-time-log": "anro-recruitment",
    "recruitment-patrol": "anro-recruitment",
    "orbat": "anro-orbat",
    "orbat-request": "anro-orbat",
    "orbat-pending": "anro-orbat",
    "loa-request": "anro-orbat",
}
_anroOrganizationGuildIds = sorted(
    {
        int(guildId)
        for guildId in (
            list(allowedCommandGuildIds)
            + [
                serverId,
                serverIdTesting,
                bgCheckAdultReviewGuildId,
                bgCheckMinorReviewGuildId,
            ]
        )
        if int(guildId) > 0
    }
)
organizationProfiles = {
    "ANRO": {
        "label": "ANRO",
        "primaryGuildId": serverId,
        "guildIds": list(_anroOrganizationGuildIds),
        "enabledFeatures": [
            "anro-sessions",
            "anro-bgc",
            "anro-training-logs",
            "anro-recruitment",
            "anro-orbat",
        ],
        "trainingResultsChannelId": trainingResultsChannelId,
        "trainingArchiveChannelId": trainingArchiveChannelId,
        "trainingLogBackfillDays": trainingLogBackfillDays,
        "trainingSummaryWebhookName": trainingSummaryWebhookName,
        "trainingMirrorWebhookName": trainingMirrorWebhookName,
        "startupGreetingChannelId": startupGreetingChannelId,
        "bgCheckChannelId": bgCheckChannelId,
        "bgCheckAdultReviewGuildId": bgCheckAdultReviewGuildId,
        "bgCheckAdultReviewChannelId": bgCheckAdultReviewChannelId,
        "bgCheckMinorReviewGuildId": bgCheckMinorReviewGuildId,
        "bgCheckMinorReviewChannelId": bgCheckMinorReviewChannelId,
        "bgCheckMinorReviewRoleId": bgCheckMinorReviewRoleId,
        "bgCheckMinorReviewRoleIds": list(bgCheckMinorReviewRoleIds),
        "bgCheckSourceGuildId": bgCheckSourceGuildId,
        "bgCheckSpreadsheetTemplateId": bgCheckSpreadsheetTemplateId,
        "bgCheckSpreadsheetFolderId": bgCheckSpreadsheetFolderId,
        "bgCheckSpreadsheetSheetName": bgCheckSpreadsheetSheetName,
        "bgMinorAgeRoleIds": list(bgMinorAgeRoleIds),
        "bgMajorAgeRoleIds": list(bgMajorAgeRoleIds),
        "bgMinorAgeGroups": list(bgMinorAgeGroups),
        "bgAdultAgeGroups": list(bgAdultAgeGroups),
        "bgUnknownDefaultsToMinor": bool(bgUnknownDefaultsToMinor),
        "moderatorRoleId": moderatorRoleId,
        "bgReviewModeratorRoleId": bgReviewModeratorRoleId,
        "newApplicantRoleId": newApplicantRoleId,
        "pendingBgRoleId": pendingBgRoleId,
        "robloxGroupId": robloxGroupId,
        "robloxGroupUrl": robloxGroupUrl,
    },
}
guildOrganizationKeys = {
    int(guildId): defaultOrganizationKey
    for guildId in list(_anroOrganizationGuildIds)
    if int(guildId) > 0
}
