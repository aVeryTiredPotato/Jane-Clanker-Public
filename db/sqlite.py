import aiosqlite
import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

dbPath = str(Path(__file__).resolve().parent.parent / "bot.db")
_dbConn: Optional[aiosqlite.Connection] = None
_dbConnInitLock = asyncio.Lock()
_dbWriteLock = asyncio.Lock()
log = logging.getLogger(__name__)
_schemaVersionTarget = 14
_T = TypeVar("_T")


async def _prepareConnection(db: aiosqlite.Connection) -> None:
    # Connection-scoped pragmas
    await db.execute("PRAGMA foreign_keys=ON;")
    await db.execute("PRAGMA busy_timeout=5000;")


async def _readSchemaVersion(db: aiosqlite.Connection) -> int:
    async with db.execute("PRAGMA user_version;") as cur:
        row = await cur.fetchone()
        if row is None:
            return 0
        try:
            return int(row[0] or 0)
        except (TypeError, ValueError):
            return 0


async def _writeSchemaVersion(db: aiosqlite.Connection, version: int) -> None:
    safeVersion = max(0, int(version or 0))
    await db.execute(f"PRAGMA user_version={safeVersion};")

async def _getConnection() -> aiosqlite.Connection:
    global _dbConn
    if _dbConn is not None:
        return _dbConn
    async with _dbConnInitLock:
        if _dbConn is not None:
            return _dbConn
        db = await aiosqlite.connect(dbPath, timeout=30)
        await _prepareConnection(db)
        db.row_factory = aiosqlite.Row
        _dbConn = db
        return _dbConn

async def initDb():
    db = await _getConnection()
    async with _dbWriteLock:
        # oh my fucking god
        currentSchemaVersion = await _readSchemaVersion(db)

        async def _executeOptional(statement: str) -> None:
            try:
                await db.execute(statement)
            except Exception:
                pass

        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            sessionId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL,
            sessionType TEXT NOT NULL,
            hostId INTEGER NOT NULL,
            passwordHash TEXT NOT NULL,
            maxAttendeeLimit INTEGER DEFAULT 30,
            status TEXT NOT NULL,              -- OPEN/GRADING/FINISHED/CANCELED/FULL
            gradingIndex INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            finishedAt TEXT,
            bgQueueMessageId INTEGER,
            bgQueueMinorMessageId INTEGER
        );
        """)
        await _executeOptional("ALTER TABLE sessions ADD COLUMN maxAttendeeLimit INTEGER DEFAULT 30")
        await _executeOptional("ALTER TABLE sessions ADD COLUMN bgQueueMessageId INTEGER")
        await _executeOptional("ALTER TABLE sessions ADD COLUMN bgQueueMinorMessageId INTEGER")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS attendees (
            sessionId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            joinTime TEXT NOT NULL DEFAULT (datetime('now')),
            examGrade TEXT NOT NULL DEFAULT 'NOT_GRADED',  -- NOT_GRADED/PASS/FAIL
            bgStatus TEXT NOT NULL DEFAULT 'PENDING',      -- PENDING/APPROVED/REJECTED
            credited INTEGER NOT NULL DEFAULT 0,           -- host point credited? 0/1
            bgReviewBucket TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (sessionId, userId)
        );
        """)
        await _executeOptional("ALTER TABLE attendees ADD COLUMN bgReviewBucket TEXT NOT NULL DEFAULT ''")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bg_review_actions (
            actionId INTEGER PRIMARY KEY AUTOINCREMENT,
            sessionId INTEGER NOT NULL,
            attendeeUserId INTEGER NOT NULL,
            reviewerId INTEGER NOT NULL,
            decision TEXT NOT NULL, -- APPROVED/REJECTED
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bg_intelligence_reports (
            reportId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL DEFAULT 0,
            reviewerId INTEGER NOT NULL,
            targetUserId INTEGER NOT NULL,
            robloxUserId INTEGER,
            robloxUsername TEXT,
            reviewBucket TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL,
            band TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            scored INTEGER NOT NULL DEFAULT 1,
            outcome TEXT NOT NULL DEFAULT 'scored',
            hardMinimum INTEGER NOT NULL DEFAULT 0,
            signalJson TEXT NOT NULL DEFAULT '[]',
            reportJson TEXT NOT NULL DEFAULT '{}',
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        for statement in (
            "ALTER TABLE bg_intelligence_reports ADD COLUMN scored INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE bg_intelligence_reports ADD COLUMN outcome TEXT NOT NULL DEFAULT 'scored'",
            "ALTER TABLE bg_intelligence_reports ADD COLUMN hardMinimum INTEGER NOT NULL DEFAULT 0",
        ):
            await _executeOptional(statement)
        for statement in (
            "ALTER TABLE attendees ADD COLUMN robloxUserId INTEGER",
            "ALTER TABLE attendees ADD COLUMN robloxUsername TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxJoinStatus TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxLastError TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxProcessedAt TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxGroupsJson TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxFlaggedGroupsJson TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxFlagMatchesJson TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxFlagged INTEGER",
            "ALTER TABLE attendees ADD COLUMN robloxGroupScanStatus TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxGroupScanError TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxGroupScanAt TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxInventoryItemsJson TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxFlaggedItemsJson TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxInventoryScanStatus TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxInventoryScanError TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxInventoryScanAt TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxFlaggedBadgesJson TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxBadgeScanStatus TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxBadgeScanError TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxBadgeScanAt TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxOutfitsJson TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxOutfitScanStatus TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxOutfitScanError TEXT",
            "ALTER TABLE attendees ADD COLUMN robloxOutfitScanAt TEXT",
        ):
            await _executeOptional(statement)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS points (
            userId INTEGER PRIMARY KEY,
            pointsTotal INTEGER NOT NULL DEFAULT 0
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS points_pending (
            pendingId INTEGER PRIMARY KEY AUTOINCREMENT,
            userId INTEGER NOT NULL,
            points INTEGER NOT NULL,
            sourceType TEXT NOT NULL,
            sourceId INTEGER,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            processedAt TEXT
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS cohost_requests (
            messageId INTEGER PRIMARY KEY,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            hostId INTEGER NOT NULL,
            eventType TEXT NOT NULL,
            collectMinutes INTEGER NOT NULL,
            status TEXT NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            finishedAt TEXT
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS cohost_volunteers (
            messageId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            rank TEXT NOT NULL DEFAULT '',
            joinTime TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (messageId, userId)
        );
        """)
        await _executeOptional("ALTER TABLE cohost_volunteers ADD COLUMN rank TEXT NOT NULL DEFAULT ''")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_events (
            eventId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL DEFAULT 0,
            creatorId INTEGER NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT,
            eventAtUtc TEXT NOT NULL,
            timezone TEXT NOT NULL,
            maxAttendees INTEGER NOT NULL DEFAULT 0,
            lockRsvpAtStart INTEGER NOT NULL DEFAULT 0,
            pingRoleIdsJson TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'ACTIVE', -- ACTIVE/DELETED
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            deletedAt TEXT,
            reminderSentAt TEXT,
            reminderThreadId INTEGER
        );
        """)
        await _executeOptional("ALTER TABLE scheduled_events ADD COLUMN maxAttendees INTEGER NOT NULL DEFAULT 0")
        await _executeOptional("ALTER TABLE scheduled_events ADD COLUMN lockRsvpAtStart INTEGER NOT NULL DEFAULT 0")
        await _executeOptional("ALTER TABLE scheduled_events ADD COLUMN pingRoleIdsJson TEXT NOT NULL DEFAULT '[]'")
        await _executeOptional("ALTER TABLE scheduled_events ADD COLUMN reminderSentAt TEXT")
        await _executeOptional("ALTER TABLE scheduled_events ADD COLUMN reminderThreadId INTEGER")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_event_rsvps (
            eventId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            response TEXT NOT NULL, -- ATTENDING/TENTATIVE
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (eventId, userId)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS department_projects (
            projectId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            reviewChannelId INTEGER,
            reviewMessageId INTEGER,
            threadId INTEGER,
            creatorId INTEGER NOT NULL,
            title TEXT NOT NULL,
            idea TEXT NOT NULL,
            requestedPoints INTEGER NOT NULL DEFAULT 0,
            awardedPoints INTEGER,
            status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL', -- PENDING_APPROVAL/APPROVED/DENIED/SUBMITTED/FINALIZED
            hodReviewerId INTEGER,
            hodReviewNote TEXT,
            hodReviewedAt TEXT,
            submitSummary TEXT,
            submitProof TEXT,
            submittedAt TEXT,
            finalReviewerId INTEGER,
            finalReviewNote TEXT,
            finalizedAt TEXT,
            closedAt TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS department_project_history (
            historyId INTEGER PRIMARY KEY AUTOINCREMENT,
            projectId INTEGER NOT NULL,
            guildId INTEGER NOT NULL,
            actorId INTEGER,
            action TEXT NOT NULL,
            fromStatus TEXT,
            toStatus TEXT,
            note TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS recruitment_submissions (
            submissionId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL,
            submitterId INTEGER NOT NULL,
            recruitUserId INTEGER NOT NULL,
            recruitDisplayName TEXT NOT NULL DEFAULT '',
            passedOrientation INTEGER NOT NULL DEFAULT 0,
            imageUrls TEXT NOT NULL,
            status TEXT NOT NULL,
            points INTEGER NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedBy INTEGER,
            reviewedAt TEXT,
            reviewNote TEXT,
            threadId INTEGER
        );
        """)
        await _executeOptional("ALTER TABLE recruitment_submissions ADD COLUMN recruitDisplayName TEXT NOT NULL DEFAULT ''")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS recruitment_time_submissions (
            submissionId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL,
            submitterId INTEGER NOT NULL,
            patrolType TEXT NOT NULL DEFAULT 'solo',
            participantUserIds TEXT,
            durationMinutes INTEGER NOT NULL,
            imageUrls TEXT NOT NULL,
            evidenceMessageUrl TEXT,
            status TEXT NOT NULL,
            points INTEGER NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedBy INTEGER,
            reviewedAt TEXT,
            reviewNote TEXT,
            threadId INTEGER
        );
        """)
        for statement in (
            "ALTER TABLE recruitment_time_submissions ADD COLUMN patrolType TEXT NOT NULL DEFAULT 'solo'",
            "ALTER TABLE recruitment_time_submissions ADD COLUMN participantUserIds TEXT",
            "ALTER TABLE recruitment_time_submissions ADD COLUMN evidenceMessageUrl TEXT",
        ):
            await _executeOptional(statement)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS recruitment_patrol_sessions (
            patrolId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL,
            hostId INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN/FINISHED/CANCELED
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            finishedAt TEXT
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS recruitment_patrol_attendees (
            patrolId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            joinTime TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (patrolId, userId)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hg_main(
            userId INTEGER NOT NULL,
            rank TEXT NOT NULL,
            quotaPoints INTEGER NOT NULL DEFAULT 0,
            activityStatus TEXT NOT NULL DEFAULT 'N/A', -- ACTIVE / INACTIVE / N/A
            excuseStatus TEXT NOT NULL DEFAULT 'NONE', -- NONE / LOA / ANROMR+ / RETIRED
            eventPoints INTEGER NOT NULL DEFAULT 0,
            awardedPoints INTEGER NOT NULL DEFAULT 0,
            allTimePoints INTEGER NOT NULL DEFAULT 0,
            passedJGE TEXT NOT NULL DEFAULT 'FALSE',
            passedNCO TEXT NOT NULL DEFAULT 'FALSE',
            promotionEligible TEXT NOT NULL DEFAULT 'FALSE',
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hg_events (
            eventId INTEGER AUTOINCREMENT,
            messageId INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL, -- TRAINING / EXAM_JGE / EXAM_NCO / GAMENIGHT / SENTRY_DUTY / INSPECTION / ORIENTATION
            time TEXT NOT NULL,
            hostId INTEGER NOT NULL,
            cohostsString TEXT,
            supervisorsString TEXT,
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hg_events_archive (
            eventId INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL, -- TRAINING / EXAM_JGE / EXAM_NCO / GAMENIGHT / SENTRY_DUTY / INSPECTION / ORIENTATION
            time TEXT NOT NULL,
            hostId INTEGER NOT NULL,
            cohostsString TEXT,
            supervisorsString TEXT,
            durationMinutes INTEGER NOT NULL,
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hg_point_awards (
            awardedId INTEGER NOT NULL, -- guardsman that the points are awarded to
            submitterId INTEGER NOT NULL, -- officer/guardsman/person that recommended the award
            approverId INTEGER, -- officer who approved the award, can be none if it wasnt rejected/approved
            points INTEGER NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL, -- PENDING / APPROVED / REJECTED
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hg_attendance_records (
            eventId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            joinTime TEXT NOT NULL DEFAULT (datetime('now')),
            leaveTime TEXT,
            eventPoints INTEGER NOT NULL DEFAULT 0, -- calculated in service calculator here for logging only
            PRIMARY KEY (eventId, userId),
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hg_solo_sentry_logs (
            userId INTEGER NOT NULL,
            startTime TEXT NOT NULL,
            endTime TEXT NOT NULL,
            evidenceAttachmentUrl1 TEXT,
            evidenceAttachmentUrl2 TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hg_quota_cycles (
            cycleId INTEGER AUTOINCREMENT,
            startDate TEXT NOT NULL,
            endDate TEXT NOT NULL,
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bg_flag_rules (
            ruleId INTEGER PRIMARY KEY AUTOINCREMENT,
            ruleType TEXT NOT NULL,
            ruleValue TEXT NOT NULL,
            note TEXT,
            severity INTEGER NOT NULL DEFAULT 0,
            createdBy INTEGER,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await _executeOptional("ALTER TABLE bg_flag_rules ADD COLUMN severity INTEGER NOT NULL DEFAULT 0")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS orbat_requests (
            requestId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER,
            submitterId INTEGER NOT NULL,
            robloxUser TEXT NOT NULL,
            mic TEXT NOT NULL,
            timezone TEXT NOT NULL,
            ageGroup TEXT NOT NULL,
            notes TEXT,
            inferredRank TEXT,
            inferredClearance TEXT,
            inferredDepartment TEXT,
            status TEXT NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedBy INTEGER,
            reviewedAt TEXT,
            reviewNote TEXT,
            sheetRow INTEGER,
            threadId INTEGER
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS loa_requests (
            requestId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER,
            submitterId INTEGER NOT NULL,
            startDate TEXT NOT NULL,
            endDate TEXT NOT NULL,
            reason TEXT,
            status TEXT NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedBy INTEGER,
            reviewedAt TEXT,
            reviewNote TEXT,
            threadId INTEGER
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS division_applications (
            applicationId INTEGER PRIMARY KEY AUTOINCREMENT,
            appCode TEXT UNIQUE,
            guildId INTEGER NOT NULL,
            divisionKey TEXT NOT NULL,
            applicantId INTEGER NOT NULL,
            status TEXT NOT NULL, -- PENDING/NEEDS_INFO/APPROVED/DENIED
            answersJson TEXT NOT NULL,
            proofMessageUrl TEXT,
            proofAttachmentsJson TEXT,
            reviewChannelId INTEGER,
            reviewMessageId INTEGER,
            reviewerId INTEGER,
            reviewNote TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedAt TEXT,
            closedAt TEXT,
            reopenedCount INTEGER NOT NULL DEFAULT 0
        );
        """)
        for statement in (
            "ALTER TABLE division_applications ADD COLUMN reviewedAt TEXT",
            "ALTER TABLE division_applications ADD COLUMN closedAt TEXT",
            "ALTER TABLE division_applications ADD COLUMN reopenedCount INTEGER NOT NULL DEFAULT 0",
        ):
            await _executeOptional(statement)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS division_application_events (
            eventId INTEGER PRIMARY KEY AUTOINCREMENT,
            applicationId INTEGER NOT NULL,
            actorId INTEGER,
            eventType TEXT NOT NULL,
            details TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS division_hub_messages (
            messageId INTEGER PRIMARY KEY,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            divisionKey TEXT NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ribbon_assets (
            assetId TEXT PRIMARY KEY,
            displayName TEXT NOT NULL,
            category TEXT NOT NULL,
            filePath TEXT NOT NULL,
            fileHash TEXT NOT NULL,
            isRetired INTEGER NOT NULL DEFAULT 0,
            aliasesJson TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ribbon_profiles (
            userId INTEGER PRIMARY KEY,
            nameplateText TEXT,
            medalSelectionJson TEXT,
            currentRibbonIdsJson TEXT NOT NULL DEFAULT '[]',
            lastGeneratedImagePath TEXT,
            updatedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ribbon_requests (
            requestId INTEGER PRIMARY KEY AUTOINCREMENT,
            requestCode TEXT UNIQUE,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            requesterId INTEGER NOT NULL,
            status TEXT NOT NULL, -- PENDING/NEEDS_INFO/APPROVED/REJECTED/CANCELED
            nameplateText TEXT,
            medalSelectionJson TEXT,
            addRibbonIdsJson TEXT NOT NULL DEFAULT '[]',
            removeRibbonIdsJson TEXT NOT NULL DEFAULT '[]',
            autoApprovedRibbonIdsJson TEXT NOT NULL DEFAULT '[]',
            needsProofRibbonIdsJson TEXT NOT NULL DEFAULT '[]',
            staffOnlyRibbonIdsJson TEXT NOT NULL DEFAULT '[]',
            currentSnapshotJson TEXT,
            reviewMessageId INTEGER,
            reviewChannelId INTEGER,
            reviewerId INTEGER,
            reviewNote TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedAt TEXT
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ribbon_request_proofs (
            proofId INTEGER PRIMARY KEY AUTOINCREMENT,
            requestId INTEGER NOT NULL,
            ribbonId TEXT,
            proofType TEXT,
            messageId INTEGER,
            messageUrl TEXT,
            attachmentUrl TEXT,
            attachmentHash TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS ribbon_request_events (
            eventId INTEGER PRIMARY KEY AUTOINCREMENT,
            requestId INTEGER NOT NULL,
            actorId INTEGER,
            eventType TEXT NOT NULL,
            details TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS john_event_log_messages (
            messageId INTEGER PRIMARY KEY,
            channelId INTEGER NOT NULL,
            hostId INTEGER,
            eventCategory TEXT,
            processedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS training_result_logs (
            messageId INTEGER PRIMARY KEY,
            sourceGuildId INTEGER NOT NULL DEFAULT 0,
            sourceChannelId INTEGER NOT NULL DEFAULT 0,
            sourceAuthorId INTEGER NOT NULL DEFAULT 0,
            sourceCreatedAt TEXT NOT NULL,
            eventKind TEXT NOT NULL DEFAULT '',
            certType TEXT NOT NULL DEFAULT '',
            certVariant TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            hostId INTEGER NOT NULL DEFAULT 0,
            hostText TEXT NOT NULL DEFAULT '',
            passCount INTEGER NOT NULL DEFAULT 0,
            failCount INTEGER NOT NULL DEFAULT 0,
            mirrorChannelId INTEGER NOT NULL DEFAULT 0,
            mirrorMessageId INTEGER NOT NULL DEFAULT 0,
            rawContent TEXT NOT NULL DEFAULT '',
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS anrd_payment_requests (
            requestId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            submitterId INTEGER NOT NULL,
            workSummary TEXT NOT NULL,
            proof TEXT NOT NULL,
            askingPrice TEXT NOT NULL,
            status TEXT NOT NULL, -- PENDING/NEGOTIATING/NEEDS_INFO/APPROVED/DENIED
            reviewChannelId INTEGER,
            reviewMessageId INTEGER,
            reviewerId INTEGER,
            reviewNote TEXT,
            negotiatedPrice TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedAt TEXT,
            payoutSynced INTEGER NOT NULL DEFAULT 0
        );
        """)
        await _executeOptional("ALTER TABLE anrd_payment_requests ADD COLUMN payoutSynced INTEGER NOT NULL DEFAULT 0")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS curfew_targets (
            orgKey TEXT NOT NULL DEFAULT '',
            guildId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            timezone TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            addedBy INTEGER NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            lastAppliedAt TEXT,
            PRIMARY KEY (guildId, userId)
        );
        """)
        await _executeOptional("ALTER TABLE curfew_targets ADD COLUMN orgKey TEXT NOT NULL DEFAULT ''")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS jail_records (
            recordId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            jailedBy INTEGER NOT NULL,
            jailedRoleId INTEGER NOT NULL,
            jailChannelId INTEGER,
            savedRoleIdsJson TEXT NOT NULL,
            unmanageableRoleIdsJson TEXT NOT NULL,
            isolatedChannelIdsJson TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE', -- ACTIVE/REPLACED/RELEASED
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            releasedBy INTEGER,
            releasedAt TEXT
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS best_of_polls (
            pollId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL DEFAULT 0,
            createdBy INTEGER NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN/CLOSED
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            closedBy INTEGER,
            closedAt TEXT
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS best_of_poll_candidates (
            pollId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            priorityRank INTEGER NOT NULL,
            priorityLabel TEXT NOT NULL,
            displayName TEXT NOT NULL DEFAULT '',
            sortOrder INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pollId, userId)
        );
        """)
        await _executeOptional("ALTER TABLE best_of_poll_candidates ADD COLUMN displayName TEXT NOT NULL DEFAULT ''")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS best_of_poll_votes (
            pollId INTEGER NOT NULL,
            voterId INTEGER NOT NULL,
            candidateUserId INTEGER NOT NULL,
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (pollId, voterId)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS best_of_poll_section_votes (
            pollId INTEGER NOT NULL,
            voterId INTEGER NOT NULL,
            sectionLabel TEXT NOT NULL,
            candidateUserId INTEGER NOT NULL,
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (pollId, voterId, sectionLabel)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS hall_reaction_posts (
            messageId INTEGER NOT NULL,
            hallType TEXT NOT NULL, -- FAME/SHAME
            guildId INTEGER NOT NULL,
            sourceChannelId INTEGER NOT NULL,
            targetChannelId INTEGER NOT NULL,
            sourceAuthorId INTEGER NOT NULL,
            reactionEmoji TEXT NOT NULL,
            reactionCount INTEGER NOT NULL DEFAULT 0,
            reactionBreakdownJson TEXT NOT NULL DEFAULT '{}',
            postedMessageId INTEGER,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (messageId, hallType)
        );
        """)
        await _executeOptional("ALTER TABLE hall_reaction_posts ADD COLUMN reactionBreakdownJson TEXT NOT NULL DEFAULT '{}'")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS silly_gambling_wallets (
            userId INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 1000,
            gamesPlayed INTEGER NOT NULL DEFAULT 0,
            totalLost INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS silly_gambling_api_credits (
            requestId TEXT PRIMARY KEY,
            userId INTEGER NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            directDollars INTEGER NOT NULL DEFAULT 0,
            creditedDollars INTEGER NOT NULL,
            conversionRate INTEGER NOT NULL DEFAULT 5,
            source TEXT NOT NULL DEFAULT '',
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS community_polls (
            pollId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL DEFAULT 0,
            creatorId INTEGER NOT NULL,
            question TEXT NOT NULL,
            optionsJson TEXT NOT NULL,
            anonymous INTEGER NOT NULL DEFAULT 0,
            multiSelect INTEGER NOT NULL DEFAULT 0,
            roleGateIdsJson TEXT NOT NULL DEFAULT '[]',
            hideResultsUntilClosed INTEGER NOT NULL DEFAULT 0,
            messageResultsToCreator INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'OPEN', -- OPEN/CLOSED
            closesAt TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            closedAt TEXT
        );
        """)
        for statement in (
            "ALTER TABLE community_polls ADD COLUMN anonymous INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE community_polls ADD COLUMN multiSelect INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE community_polls ADD COLUMN roleGateIdsJson TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE community_polls ADD COLUMN hideResultsUntilClosed INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE community_polls ADD COLUMN messageResultsToCreator INTEGER NOT NULL DEFAULT 0"
        ):
            await _executeOptional(statement)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS community_poll_votes (
            pollId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            optionIndex INTEGER NOT NULL,
            optionIndexesJson TEXT,
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (pollId, userId)
        );
        """)
        await _executeOptional("ALTER TABLE community_poll_votes ADD COLUMN optionIndexesJson TEXT")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            reminderId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            userId INTEGER NOT NULL,
            reminderText TEXT NOT NULL,
            remindAtUtc TEXT NOT NULL,
            targetType TEXT NOT NULL DEFAULT 'USER', -- USER/ROLE
            targetRoleIdsJson TEXT NOT NULL DEFAULT '[]',
            recurringIntervalSec INTEGER NOT NULL DEFAULT 0,
            sourceReminderId INTEGER,
            status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/SENT/CANCELED
            dmDelivered INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            sentAt TEXT
        );
        """)
        for statement in (
            "ALTER TABLE reminders ADD COLUMN targetType TEXT NOT NULL DEFAULT 'USER'",
            "ALTER TABLE reminders ADD COLUMN targetRoleIdsJson TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE reminders ADD COLUMN recurringIntervalSec INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE reminders ADD COLUMN sourceReminderId INTEGER",
        ):
            await _executeOptional(statement)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS suggestions (
            suggestionId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL DEFAULT 0,
            submitterId INTEGER NOT NULL,
            content TEXT NOT NULL,
            anonymous INTEGER NOT NULL DEFAULT 0,
            threadId INTEGER,
            freedcampId INTEGER,
            status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/APPROVED/REJECTED/IMPLEMENTED
            reviewerId INTEGER,
            reviewNote TEXT,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            reviewedAt TEXT
        );
        """)
        for statement in (
            "ALTER TABLE suggestions ADD COLUMN anonymous INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE suggestions ADD COLUMN threadId INTEGER",
            "ALTER TABLE suggestions ADD COLUMN freedcampId INTEGER",
        ):
            await _executeOptional(statement)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS suggestion_status_boards (
            messageId INTEGER PRIMARY KEY,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_stats_snapshots (
            snapshotId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            memberCount INTEGER NOT NULL DEFAULT 0,
            humanCount INTEGER NOT NULL DEFAULT 0,
            botCount INTEGER NOT NULL DEFAULT 0,
            textChannelCount INTEGER NOT NULL DEFAULT 0,
            voiceChannelCount INTEGER NOT NULL DEFAULT 0,
            forumChannelCount INTEGER NOT NULL DEFAULT 0,
            stageChannelCount INTEGER NOT NULL DEFAULT 0,
            roleCount INTEGER NOT NULL DEFAULT 0,
            boostCount INTEGER NOT NULL DEFAULT 0,
            capturedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_member_activity_daily (
            guildId INTEGER NOT NULL,
            activityDate TEXT NOT NULL,
            joinCount INTEGER NOT NULL DEFAULT 0,
            leaveCount INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guildId, activityDate)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_channel_activity_daily (
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            activityDate TEXT NOT NULL,
            messageCount INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guildId, channelId, activityDate)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_feature_flags (
            guildId INTEGER NOT NULL,
            featureKey TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updatedBy INTEGER,
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            note TEXT,
            PRIMARY KEY (guildId, featureKey)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            eventId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL DEFAULT 0,
            actorId INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            targetType TEXT NOT NULL DEFAULT '',
            targetId TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'INFO',
            detailsJson TEXT NOT NULL DEFAULT '{}',
            authorizedBy TEXT NOT NULL DEFAULT '',
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS assistant_notes (
            noteId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            subjectType TEXT NOT NULL, -- USER/DIVISION/PROCESS
            subjectKey TEXT NOT NULL,
            content TEXT NOT NULL,
            createdBy INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS guild_federation_links (
            linkId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            linkedGuildId INTEGER NOT NULL,
            linkType TEXT NOT NULL DEFAULT 'SHARED_STAFF',
            note TEXT,
            createdBy INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            runId INTEGER PRIMARY KEY AUTOINCREMENT,
            workflowKey TEXT NOT NULL,
            subjectType TEXT NOT NULL,
            subjectId INTEGER NOT NULL,
            guildId INTEGER NOT NULL,
            displayName TEXT NOT NULL DEFAULT '',
            currentStateKey TEXT NOT NULL,
            currentStateLabel TEXT NOT NULL,
            pendingWith TEXT NOT NULL DEFAULT '',
            isTerminal INTEGER NOT NULL DEFAULT 0,
            createdBy INTEGER NOT NULL DEFAULT 0,
            metadataJson TEXT NOT NULL DEFAULT '{}',
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now')),
            closedAt TEXT,
            UNIQUE(workflowKey, subjectType, subjectId)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS workflow_events (
            eventId INTEGER PRIMARY KEY AUTOINCREMENT,
            runId INTEGER NOT NULL,
            workflowKey TEXT NOT NULL,
            subjectType TEXT NOT NULL,
            subjectId INTEGER NOT NULL,
            actorId INTEGER,
            fromStateKey TEXT,
            toStateKey TEXT NOT NULL,
            toStateLabel TEXT NOT NULL,
            eventType TEXT NOT NULL DEFAULT 'STATE_CHANGE',
            note TEXT,
            detailsJson TEXT NOT NULL DEFAULT '{}',
            createdAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reaction_role_entries (
            entryId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL,
            emojiKey TEXT NOT NULL,
            roleId INTEGER NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(messageId, emojiKey)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS button_role_entries (
            entryId INTEGER PRIMARY KEY AUTOINCREMENT,
            guildId INTEGER NOT NULL,
            channelId INTEGER NOT NULL,
            messageId INTEGER NOT NULL,
            roleId INTEGER NOT NULL,
            buttonLabel TEXT NOT NULL DEFAULT '',
            emojiSpec TEXT NOT NULL DEFAULT '',
            orderIndex INTEGER NOT NULL DEFAULT 0,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(messageId, roleId)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS blocked_self_roles (
            guildId INTEGER NOT NULL,
            roleId INTEGER NOT NULL,
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (guildId, roleId)
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS retry_jobs (
            jobId INTEGER PRIMARY KEY AUTOINCREMENT,
            jobType TEXT NOT NULL,
            payloadJson TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING/PROCESSING/FAILED/DEAD/DONE
            attempts INTEGER NOT NULL DEFAULT 0,
            maxAttempts INTEGER NOT NULL DEFAULT 5,
            nextAttemptAt TEXT NOT NULL DEFAULT (datetime('now')),
            lastError TEXT,
            source TEXT NOT NULL DEFAULT '',
            createdAt TEXT NOT NULL DEFAULT (datetime('now')),
            updatedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS db_schema_migrations (
            migrationId INTEGER PRIMARY KEY AUTOINCREMENT,
            fromVersion INTEGER NOT NULL,
            toVersion INTEGER NOT NULL,
            appliedAt TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        # Hot-path indexes
        indexStatements = (
            "CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)",
            "CREATE INDEX IF NOT EXISTS idx_attendees_session_exam ON attendees(sessionId, examGrade)",
            "CREATE INDEX IF NOT EXISTS idx_attendees_session_bg ON attendees(sessionId, bgStatus)",
            "CREATE INDEX IF NOT EXISTS idx_bg_review_actions_reviewer_decision ON bg_review_actions(reviewerId, decision, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_bg_review_actions_session_attendee ON bg_review_actions(sessionId, attendeeUserId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_bg_intel_reports_target_created ON bg_intelligence_reports(targetUserId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_bg_intel_reports_guild_created ON bg_intelligence_reports(guildId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_points_pending_processed_user ON points_pending(processedAt, userId)",
            "CREATE INDEX IF NOT EXISTS idx_cohost_requests_status ON cohost_requests(status)",
            "CREATE INDEX IF NOT EXISTS idx_cohost_volunteers_message_join ON cohost_volunteers(messageId, joinTime)",
            "CREATE INDEX IF NOT EXISTS idx_scheduled_events_status_time ON scheduled_events(status, eventAtUtc)",
            "CREATE INDEX IF NOT EXISTS idx_scheduled_events_message ON scheduled_events(messageId)",
            "CREATE INDEX IF NOT EXISTS idx_scheduled_event_rsvps_event_response ON scheduled_event_rsvps(eventId, response, updatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_department_projects_guild_status ON department_projects(guildId, status, updatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_department_projects_creator_status ON department_projects(guildId, creatorId, status, updatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_department_projects_review_message ON department_projects(reviewMessageId)",
            "CREATE INDEX IF NOT EXISTS idx_department_project_history_project_created ON department_project_history(projectId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_recruitment_status ON recruitment_submissions(status)",
            "CREATE INDEX IF NOT EXISTS idx_recruitment_recruit_passed ON recruitment_submissions(recruitUserId, passedOrientation)",
            "CREATE INDEX IF NOT EXISTS idx_recruitment_time_status ON recruitment_time_submissions(status)",
            "CREATE INDEX IF NOT EXISTS idx_recruitment_time_patrol_type ON recruitment_time_submissions(patrolType, status)",
            "CREATE INDEX IF NOT EXISTS idx_recruitment_patrol_status ON recruitment_patrol_sessions(status)",
            "CREATE INDEX IF NOT EXISTS idx_recruitment_patrol_attendees_patrol ON recruitment_patrol_attendees(patrolId, joinTime)",
            "CREATE INDEX IF NOT EXISTS idx_hg_main ON hg_main(userId)",
            "CREATE INDEX IF NOT EXISTS idx_hg_events ON hg_events(eventId)",
            "CREATE INDEX IF NOT EXISTS idx_hg_point_awards ON hg_point_awards(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_hg_attendance_records ON hg_attendance_records(eventId, userId)",
            "CREATE INDEX IF NOT EXISTS idx_hg_solo_sentry_logs ON hg_solo_sentry_logs(userId)",
            "CREATE INDEX IF NOT EXISTS idx_hg_quota_cycles ON hg_quota_cycles(cycleId)",
            "CREATE INDEX IF NOT EXISTS idx_orbat_requests_status ON orbat_requests(status)",
            "CREATE INDEX IF NOT EXISTS idx_loa_requests_status ON loa_requests(status)",
            "CREATE INDEX IF NOT EXISTS idx_division_apps_status ON division_applications(status)",
            "CREATE INDEX IF NOT EXISTS idx_division_apps_lookup ON division_applications(guildId, divisionKey, applicantId, status)",
            "CREATE INDEX IF NOT EXISTS idx_division_apps_review_msg ON division_applications(reviewMessageId)",
            "CREATE INDEX IF NOT EXISTS idx_division_app_events_app ON division_application_events(applicationId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_division_hub_messages_division ON division_hub_messages(guildId, divisionKey)",
            "CREATE INDEX IF NOT EXISTS idx_ribbon_assets_name_cat ON ribbon_assets(displayName, category)",
            "CREATE INDEX IF NOT EXISTS idx_ribbon_assets_retired ON ribbon_assets(isRetired)",
            "CREATE INDEX IF NOT EXISTS idx_ribbon_requests_status ON ribbon_requests(status)",
            "CREATE INDEX IF NOT EXISTS idx_ribbon_requests_user_status ON ribbon_requests(requesterId, status)",
            "CREATE INDEX IF NOT EXISTS idx_ribbon_requests_review_msg ON ribbon_requests(reviewMessageId)",
            "CREATE INDEX IF NOT EXISTS idx_ribbon_proofs_request ON ribbon_request_proofs(requestId)",
            "CREATE INDEX IF NOT EXISTS idx_ribbon_events_request ON ribbon_request_events(requestId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_bg_flag_rules_type ON bg_flag_rules(ruleType)",
            "CREATE INDEX IF NOT EXISTS idx_john_event_log_channel_processed ON john_event_log_messages(channelId, processedAt)",
            "CREATE INDEX IF NOT EXISTS idx_training_result_logs_host_created ON training_result_logs(hostId, sourceCreatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_training_result_logs_type_created ON training_result_logs(certType, certVariant, sourceCreatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_training_result_logs_source_channel_created ON training_result_logs(sourceChannelId, sourceCreatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_anrd_payment_status ON anrd_payment_requests(status)",
            "CREATE INDEX IF NOT EXISTS idx_anrd_payment_review_msg ON anrd_payment_requests(reviewMessageId)",
            "CREATE INDEX IF NOT EXISTS idx_curfew_targets_enabled ON curfew_targets(enabled, guildId, userId)",
            "CREATE INDEX IF NOT EXISTS idx_curfew_targets_org_enabled ON curfew_targets(orgKey, enabled, userId)",
            "CREATE INDEX IF NOT EXISTS idx_jail_records_active ON jail_records(guildId, userId, status, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_best_of_polls_status ON best_of_polls(guildId, status, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_best_of_candidates_poll_rank ON best_of_poll_candidates(pollId, priorityRank, sortOrder)",
            "CREATE INDEX IF NOT EXISTS idx_best_of_votes_poll_candidate ON best_of_poll_votes(pollId, candidateUserId)",
            "CREATE INDEX IF NOT EXISTS idx_best_of_section_votes_poll_section_candidate ON best_of_poll_section_votes(pollId, sectionLabel, candidateUserId)",
            "CREATE INDEX IF NOT EXISTS idx_best_of_section_votes_poll_voter ON best_of_poll_section_votes(pollId, voterId)",
            "CREATE INDEX IF NOT EXISTS idx_hall_posts_target_created ON hall_reaction_posts(targetChannelId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_gambling_wallet_balance ON silly_gambling_wallets(balance)",
            "CREATE INDEX IF NOT EXISTS idx_gambling_api_credits_user_created ON silly_gambling_api_credits(userId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_community_polls_status_closes ON community_polls(guildId, status, closesAt)",
            "CREATE INDEX IF NOT EXISTS idx_community_polls_message ON community_polls(messageId)",
            "CREATE INDEX IF NOT EXISTS idx_community_poll_votes_poll_option ON community_poll_votes(pollId, optionIndex)",
            "CREATE INDEX IF NOT EXISTS idx_reminders_status_time ON reminders(status, remindAtUtc)",
            "CREATE INDEX IF NOT EXISTS idx_reminders_user_status ON reminders(guildId, userId, status, remindAtUtc)",
            "CREATE INDEX IF NOT EXISTS idx_suggestions_status_created ON suggestions(guildId, status, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_suggestions_message ON suggestions(messageId)",
            "CREATE INDEX IF NOT EXISTS idx_suggestions_thread ON suggestions(threadId)",
            "CREATE INDEX IF NOT EXISTS idx_suggestion_boards_guild ON suggestion_status_boards(guildId, channelId)",
            "CREATE INDEX IF NOT EXISTS idx_guild_stats_snapshots_guild_time ON guild_stats_snapshots(guildId, capturedAt)",
            "CREATE INDEX IF NOT EXISTS idx_guild_member_activity_daily_guild_date ON guild_member_activity_daily(guildId, activityDate)",
            "CREATE INDEX IF NOT EXISTS idx_guild_channel_activity_daily_guild_date ON guild_channel_activity_daily(guildId, activityDate)",
            "CREATE INDEX IF NOT EXISTS idx_feature_flags_guild_key ON guild_feature_flags(guildId, featureKey)",
            "CREATE INDEX IF NOT EXISTS idx_audit_events_guild_created ON audit_events(guildId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_audit_events_source_created ON audit_events(source, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_assistant_notes_subject ON assistant_notes(guildId, subjectType, subjectKey, updatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_federation_links_guild ON guild_federation_links(guildId, linkedGuildId)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_guild_pending ON workflow_runs(guildId, workflowKey, isTerminal, pendingWith, updatedAt)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_runs_subject ON workflow_runs(subjectType, subjectId)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_events_run_created ON workflow_events(runId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_events_subject_created ON workflow_events(subjectType, subjectId, createdAt)",
            "CREATE INDEX IF NOT EXISTS idx_reaction_roles_message ON reaction_role_entries(messageId)",
            "CREATE INDEX IF NOT EXISTS idx_reaction_roles_guild_channel ON reaction_role_entries(guildId, channelId)",
            "CREATE INDEX IF NOT EXISTS idx_button_roles_message ON button_role_entries(messageId, orderIndex)",
            "CREATE INDEX IF NOT EXISTS idx_button_roles_guild_channel ON button_role_entries(guildId, channelId)",
            "CREATE INDEX IF NOT EXISTS idx_blocked_self_roles_guild ON blocked_self_roles(guildId)",
            "CREATE INDEX IF NOT EXISTS idx_retry_jobs_status_next ON retry_jobs(status, nextAttemptAt)",
            "CREATE INDEX IF NOT EXISTS idx_retry_jobs_type_status ON retry_jobs(jobType, status, updatedAt)",
        )
        for statement in indexStatements:
            await db.execute(statement)
        if currentSchemaVersion < _schemaVersionTarget:
            await db.execute(
                """
                INSERT INTO db_schema_migrations (fromVersion, toVersion)
                VALUES (?, ?)
                """,
                (currentSchemaVersion, _schemaVersionTarget),
            )
            await _writeSchemaVersion(db, _schemaVersionTarget)
        await db.commit()
        if currentSchemaVersion < _schemaVersionTarget:
            log.info(
                "Database schema upgraded: v%s -> v%s",
                currentSchemaVersion,
                _schemaVersionTarget,
            )

async def fetchOne(query: str, params: tuple = ()):
    db = await _getConnection()
    async with db.execute(query, params) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None

async def fetchAll(query: str, params: tuple = ()):
    db = await _getConnection()
    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

async def execute(query: str, params: tuple = ()):
    db = await _getConnection()
    async with _dbWriteLock:
        await db.execute(query, params)
        await db.commit()

async def executeReturnId(query: str, params: tuple = ()) -> int:
    db = await _getConnection()
    async with _dbWriteLock:
        cur = await db.execute(query, params)
        await db.commit()
        return cur.lastrowid

async def executeMany(query: str, paramsSeq: list[tuple]) -> None:
    if not paramsSeq:
        return
    db = await _getConnection()
    async with _dbWriteLock:
        await db.executemany(query, paramsSeq)
        await db.commit()


async def runWriteTransaction(callback: Callable[[aiosqlite.Connection], Awaitable[_T]]) -> _T:
    db = await _getConnection()
    async with _dbWriteLock:
        await db.execute("BEGIN IMMEDIATE")
        try:
            result = await callback(db)
        except Exception:
            await db.rollback()
            raise
        await db.commit()
        return result

async def closeDb() -> None:
    global _dbConn
    if _dbConn is None:
        return
    async with _dbConnInitLock:
        if _dbConn is None:
            return
        await _dbConn.close()
        _dbConn = None
