# Pydantic request/response models

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TeamVisualResponse(BaseModel):
    # Visual identity values for team-aware UI surfaces.

    primary: str
    secondary: str
    primary_colour: str
    secondary_colour: str
    glow: str
    soft_glow: str
    border: str
    shadow: str
    colour_source: str
    colour_status: str


class MatchSummary(BaseModel):
    # A football match returned to the frontend.

    id: str
    home: str
    away: str
    home_team_id: Optional[int] = None
    away_team_id: Optional[int] = None
    home_short_name: Optional[str] = None
    away_short_name: Optional[str] = None
    home_tla: Optional[str] = None
    away_tla: Optional[str] = None
    home_crest: Optional[str] = None
    away_crest: Optional[str] = None
    home_visual: Optional[TeamVisualResponse] = None
    away_visual: Optional[TeamVisualResponse] = None
    score: Optional[str] = None
    score_note: Optional[str] = None
    penalty_score: Optional[str] = None
    aggregate_score: Optional[str] = None
    half_time_score: Optional[str] = None
    competition: str
    competition_code: str
    competition_id: Optional[int] = None
    date: str
    venue: Optional[str] = None
    round: Optional[str] = None
    season: Optional[str] = None
    season_year: Optional[int] = None
    status: Optional[str] = None
    youtube_comment_count: Optional[int] = None
    youtube_analysed_comment_count: Optional[int] = None
    youtube_comment_status: Optional[str] = None
    youtube_checked_at: Optional[str] = None
    event_feed_status: Optional[str] = None
    event_feed_checked_at: Optional[str] = None


class SearchPagination(BaseModel):
    # Pagination metadata for match search results.

    page: int
    page_size: int
    total: int
    total_pages: int
    has_previous: bool
    has_next: bool


class SearchNotice(BaseModel):
    # Non-fatal search context, such as provider coverage gaps.

    type: str
    title: str
    message: str


class MatchSearchResponse(BaseModel):
    # Paginated match search response consumed by results.html.

    matches: List[MatchSummary]
    pagination: SearchPagination
    notices: List[SearchNotice] = []


class CompetitionOption(BaseModel):
    # A synced competition exposed to frontend filters.

    code: str
    name: str
    short_name: str
    provider_id: int
    logo_url: Optional[str] = None
    country_name: Optional[str] = None
    country_code: Optional[str] = None
    group: str = "Domestic leagues"


class SeasonOption(BaseModel):
    # A synced season exposed to frontend filters.

    year: int
    label: str
    is_current: bool


class MetadataResponse(BaseModel):
    # Frontend filter metadata sourced from the local database.

    competitions: List[CompetitionOption]
    seasons: List[SeasonOption]


class TeamProfileSection(BaseModel):
    # One sourced section for a team profile page.

    title: str
    body: str


class TeamProfileResponse(BaseModel):
    # Team profile facts sourced from API-Football and optional enrichment.

    provider_team_id: int
    name: str
    code: Optional[str] = None
    country_name: Optional[str] = None
    founded: Optional[int] = None
    is_national: bool = False
    logo_url: Optional[str] = None
    venue_name: Optional[str] = None
    venue_city: Optional[str] = None
    venue_label: Optional[str] = None
    visual: Optional[TeamVisualResponse] = None
    summary: Optional[str] = None
    profile_sections: List[TeamProfileSection] = Field(default_factory=list)
    has_manual_profile: bool = False
    wikidata_qid: Optional[str] = None
    wikipedia_title: Optional[str] = None
    wikipedia_url: Optional[str] = None
    official_website: Optional[str] = None
    profile_confidence: Optional[int] = None
    profile_needs_review: bool = False
    source_attribution_url: Optional[str] = None
    license_label: Optional[str] = None
    facts: Dict[str, Any] = Field(default_factory=dict)
    data_source: str = "local"


class CompetitionClub(BaseModel):
    # One club represented by stored fixtures for a competition season.

    provider_team_id: int
    name: str
    logo_url: Optional[str] = None
    country_name: Optional[str] = None


class CompetitionProfileResponse(BaseModel):
    # One season-specific Atmos FC competition database page.

    provider_competition_id: int
    code: str
    name: str
    short_name: str
    competition_type: str
    country_name: Optional[str] = None
    country_code: Optional[str] = None
    logo_url: Optional[str] = None
    flag_url: Optional[str] = None
    official_website: Optional[str] = None
    summary: str
    season_year: int
    season_label: str
    season_start_date: Optional[date] = None
    season_end_date: Optional[date] = None
    season_is_current: bool = False
    finished_match_count: int = 0
    goals_scored: int = 0
    club_count: int = 0
    latest_result_at: Optional[datetime] = None
    latest_matches: List[MatchSummary] = Field(default_factory=list)
    clubs: List[CompetitionClub] = Field(default_factory=list)
    data_source: str = "local"


class MatchEvent(BaseModel):
    # A timeline event displayed beside sentiment.

    minute: int
    display_minute: Optional[str] = None
    type: str
    description: str


class SentimentBucket(BaseModel):
    # Average sentiment for a five-minute match interval.

    minute: int
    score: float
    comment_count: int


class ReactionIntensityBucket(BaseModel):
    # Post-match reaction intensity for one hour bucket.

    hour_offset: int
    intensity: float
    sentiment: float
    comment_count: int


class PeakWindow(BaseModel):
    # Highest post-match reaction window.

    hour_start: int
    hour_end: int


class TopComment(BaseModel):
    # A high-signal YouTube comment near a peak sentiment moment.

    text: str
    score: int
    minute: int
    sentiment: float
    source_url: Optional[str] = None
    source_label: Optional[str] = None
    source_title: Optional[str] = None


class AnalyseRequest(BaseModel):
    # Request payload for match analysis.

    match_id: str


class AnalyseMeta(BaseModel):
    # Summary metadata for a sentiment analysis response.

    total_comments: int
    peak_minute: int
    peak_window: PeakWindow
    source_video_count: int
    overall_vibe: Dict[str, str]
    crowd_energy: Dict[str, str]
    youtube_video_url: Optional[str] = None
    analysis_mode: str = "youtube_sentiment"
    analysis_version: str = "legacy"


class AnalysisResponse(BaseModel):
    # Complete payload consumed by analysis.html.

    match: MatchSummary
    events: List[MatchEvent]
    sentiment_buckets: List[SentimentBucket]
    reaction_intensity: List[ReactionIntensityBucket]
    top_comments: List[TopComment]
    half_split: Dict[str, Dict[str, int]]
    meta: AnalyseMeta
