from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(), override=True)

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


# == Credentials / External APIs ==
# Keep API keys and credential paths in `.env`, not in versioned config.
robloxOpenCloudApiKey = _envText("ROBLOX_OPEN_CLOUD_API_KEY")
roverApiKey = _envText("ROVER_API_KEY")
orbatGoogleCredentialsPath = _envText("ORBAT_GOOGLE_CREDENTIALS_PATH")

# Optional dedicated inventory key.
robloxInventoryApiKey = _envText("ROBLOX_INVENTORY_API_KEY", robloxOpenCloudApiKey)

skinCooldownBypassRoleIds = []

freedcampApiKey = _envText("FREEDCAMP_API_KEY")
freedcampProjectId = 0
freedcampTaskGroupId = 0

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
permissionSimulatorGuildIds = []

# Runtime task tuning.
sessionMessageUpdateDebounceSec = 0.75
runtimeBudgetRobloxConcurrency = 6
runtimeBudgetSheetsConcurrency = 2
runtimeBudgetDiscordConcurrency = 6
runtimeBudgetBackgroundConcurrency = 2
retryQueuePollIntervalSec = 6
webhookHealthCheckIntervalSec = 600
generalErrorLogDir = ""
generalErrorLogMaxBytes = 2 * 1024 * 1024
generalErrorLogBackupCount = 5
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
recruiterRoleId = 0  # ANRO Recruitment Services
recruitmentReviewerRoleId = 0  # Dept. ORBAT Access
anrorsMemberRoleId = 0  # ANRO Recruitment Services
anrorsRmPlusRoleId = 0  # ANRORS RM+

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
bgMinorAgeRoleIds = []
bgMajorAgeRoleIds = []
bgMinorAgeGroups = ["13-15", "16-17", "NO INFO"]
bgAdultAgeGroups = ["18-20", "21+"]
bgUnknownDefaultsToMinor = True
# Source guild for ?bgcheck member collection (defaults to serverId when unset).
bgCheckSourceGuildId = serverId
trainingResultsChannelId = 0
startupGreetingChannelId = 0
bgFailureForumChannelId = 0


# == Public Utility / Suggestions ==
welcomeChannelId = 0
welcomeMessageTemplate = "Welcome to **{guild}**, {mention}."
publicRoleMenus = {}

suggestionChannelId = 0
suggestionForumChannelId = 0
suggestionReviewerRoleIds = []


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
runtimeControlAllowedUserIds = []


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
departmentOrbatLayoutsPath = "departmentOrbat/layouts.json"

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
        },
        "organization": {
            "enabled": True,
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


# == Recruitment / ANRORS ==
recruitmentChannelId = 0

# Optional: roles allowed to host /recruitment-patrol group.
# If empty, this falls back to recruiterRoleId permission.
recruitmentPatrolGroupHostRoleIds = []

recruitmentPointsBase = 2
recruitmentPointsOrientationBonus = 3
recruitmentPointsPer15Minutes = 1
recruitmentAutoDetectOrientation = True
recruitmentDivisionKeyAliases = ["recruitment", "anrors"]

# Automatic rank promotions (total points).
recruitmentPromoteRecruiterToSeniorAt = 10
recruitmentPromoteSeniorToLeadAt = 20

# New member defaults.
recruitmentMembersRankOrder = [
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
    "Head Recruiter 1 IC",
    "Head Recruiter 2 IC",
    "Head Recruiter 3 IC",
    "Head Recruiter 4 IC",
    "Recruitment Manager",
    "Lead Recruiter",
    "Senior Recruiter",
    "Recruiter",
]

recruitmentSectionHeaders = [
    "High Command",
    "Managers",
    "Employees",
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
robloxBadgeScanBatchSize = 50
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
gamblingApiToken = _envText("JANE_GAMBLING_API_TOKEN")
gamblingApiMaxConcurrency = 8
gamblingPointsToDollarRate = 5  # 1 point => 5 anrobucks


# == Automation / Logs ==
automationReportChannelId = 0
johnTrainingLogChannelId = 0
trainingArchiveChannelId = johnTrainingLogChannelId
trainingLogBackfillDays = 365
trainingSummaryWebhookName = "Jane Training Summary"
johnEventLogChannelId = 0
johnClankerBotId = 0


# == Hidden / Misc ==
skinAllowedUserIds = []
