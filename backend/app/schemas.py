from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Player(BaseModel):
    name: str
    team: Optional[str] = None
    role: Optional[str] = None
    positions: Optional[List[str]] = None
    bt: Optional[float] = Field(default=None, description="Batting Target")
    obt: Optional[float] = Field(default=None, description="On-base Target")
    traits: Optional[List[str]] = None
    pd: Optional[str] = Field(default=None, description="Pitching defense or similar")


class Roster(BaseModel):
    id: Optional[int] = None
    slug: str
    name: str
    description: Optional[str] = None
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    public: bool = False
    created_at: Optional[datetime] = None


class GenerateRequest(BaseModel):
    mode: Literal["season", "box_score", "manual"]
    trait_mode: Literal["standard", "sabr", "adaptive"] = "standard"
    payload: str
    name: Optional[str] = Field(default=None, description="Optional roster name override")
    description: Optional[str] = None
    public: bool = False


class RostersResponse(BaseModel):
    items: List[Roster]
    count: int
    offset: int
    limit: int


class Game(BaseModel):
    id: int
    game_id: str
    game_date: date
    game_type: Optional[str] = None
    home_team: Optional[str] = None
    home_team_short: Optional[str] = None
    away_team: Optional[str] = None
    away_team_short: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class GameListResponse(BaseModel):
    items: List[Game]
    count: int
    date: str
    cached: bool
    fallback_used: bool = False
    fallback_reason: Optional[str] = None


class GameGenerateRequest(BaseModel):
    force: bool = False
    trait_mode: Literal["standard", "sabr", "adaptive"] = "standard"
    payload: Optional[str] = Field(default=None, description="Optional raw stats payload to use instead of fetching")


class GameGenerateResponse(BaseModel):
    game: Game
    stats: str
    game_text: str
    cached: bool


class GenerateResponse(BaseModel):
    roster: Roster
    players: List[Player]
