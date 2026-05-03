from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RoverLookupResult:
    robloxId: Optional[int]
    robloxUsername: Optional[str]
    error: Optional[str] = None


@dataclass
class RobloxAcceptResult:
    ok: bool
    status: int
    error: Optional[str] = None


@dataclass
class RobloxGroupsResult:
    groups: list[dict]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxInventoryResult:
    items: list[dict]
    status: int
    error: Optional[str] = None
    summary: Optional[dict] = None


@dataclass
class RobloxInventoryReviewItemsResult:
    items: list[dict]
    status: int
    error: Optional[str] = None
    summary: Optional[dict] = None


@dataclass
class RobloxConnectionCountsResult:
    friends: Optional[int]
    followers: Optional[int]
    following: Optional[int]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxFriendIdsResult:
    friendIds: list[int]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxGamepassesResult:
    gamepasses: list[dict]
    status: int
    nextCursor: Optional[str] = None
    error: Optional[str] = None
    summary: Optional[dict] = None


@dataclass
class RobloxFavoriteGamesResult:
    games: list[dict]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxBadgeAwardsResult:
    badges: list[dict]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxUserBadgesResult:
    badges: list[dict]
    status: int
    nextCursor: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RobloxUniverseBadgesResult:
    badges: list[dict]
    status: int
    nextCursor: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RobloxUserProfileResult:
    created: Optional[str]
    status: int
    error: Optional[str] = None
    username: Optional[str] = None


@dataclass
class RobloxUsernameHistoryResult:
    usernames: list[str]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxOutfitsResult:
    outfits: list[dict]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxOutfitThumbnailsResult:
    thumbnails: list[dict]
    status: int
    error: Optional[str] = None


@dataclass
class RobloxAssetThumbnailsResult:
    thumbnails: list[dict]
    status: int
    error: Optional[str] = None
