from datetime import UTC, date, datetime
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


class Roster(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(index=True, nullable=False)
    name: str
    description: Optional[str] = None
    source_type: Optional[str] = Field(default=None, index=True)
    source_ref: Optional[str] = None
    public: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), nullable=False)

    players: List["Player"] = Relationship(back_populates="roster")

    __table_args__ = (UniqueConstraint("slug"),)


class Player(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    roster_id: Optional[int] = Field(default=None, foreign_key="roster.id")
    name: str
    team: Optional[str] = None
    role: Optional[str] = None
    positions: Optional[str] = None
    bt: Optional[float] = Field(default=None, description="Batting Target")
    obt: Optional[float] = Field(default=None, description="On-base Target")
    traits: Optional[str] = None
    pd: Optional[str] = Field(default=None, description="Pitching defense or similar")

    roster: Optional[Roster] = Relationship(back_populates="players")


class Game(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: str = Field(index=True, nullable=False)
    game_date: date = Field(nullable=False)
    game_type: Optional[str] = Field(
        default=None,
        description="MLB game type code",
    )
    home_team: Optional[str] = None
    home_team_short: Optional[str] = Field(default=None, description="MLB shortName (e.g., Dodgers)")
    away_team: Optional[str] = None
    away_team_short: Optional[str] = Field(default=None, description="MLB shortName (e.g., Dodgers)")
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), nullable=False)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), nullable=False)

    raw_stats: Optional["GameRawStats"] = Relationship(back_populates="game")
    generated: Optional["GameGenerated"] = Relationship(back_populates="game")

    __table_args__ = (UniqueConstraint("game_id"),)


class GameRawStats(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: int = Field(foreign_key="game.id")
    payload: str  # raw stats as JSON/text
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), nullable=False)

    game: Game = Relationship(back_populates="raw_stats")


class GameGenerated(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: int = Field(foreign_key="game.id")
    stats: str  # deadball stats as JSON/text
    game_text: str  # deadball game artifact (text/JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), nullable=False)

    game: Game = Relationship(back_populates="generated")
