"""Self-contained fictional game for trying the terminal conductor offline."""

from __future__ import annotations

from deadball_core import GeneratedGame, load_generated_game


POSITIONS = ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH")

DEMO_NAMES = {
    "Roadrunners": (
        "Milo Hayes",
        "Theo Brooks",
        "Gabriel Stone",
        "Nico Ramirez",
        "Elias Ward",
        "Rowan Cole",
        "Julian Park",
        "Marcus Bell",
        "Adrian Fox",
    ),
    "Homesteaders": (
        "Silas Reed",
        "Emmett Clarke",
        "Mateo Ortiz",
        "Jonah Price",
        "Levi Bennett",
        "Isaac Monroe",
        "Samuel Cross",
        "Henry Dalton",
        "Wesley Quinn",
    ),
}

DEMO_STAFF = {
    "Roadrunners": (
        "Owen Mercer", "Caleb Shaw", "Victor Lane", "Felix Grant", "Darius King",
    ),
    "Homesteaders": (
        "Arthur Vaughn", "Lucas Ford", "Everett Nash", "Calvin Rhodes", "Jordan Fields",
    ),
}


def load_demo_game() -> GeneratedGame:
    """Return a valid Modern Era game without files, network, or generator I/O."""
    return load_generated_game(
        {
            "schema_version": 1,
            "game": {
                "game_id": "deadball-play-demo",
                "game_date": "2026-01-01",
                "source": "deadball-play-demo",
                "season": 2026,
                "game_type": "R",
            },
            "rules": {
                "edition": "second",
                "era": "modern",
                "designated_hitter": True,
                "oddities": True,
                "three_batter_minimum": True,
            },
            "teams": {
                "away": _team("road", "Roadrunners", "RD", 29, 37, "d8"),
                "home": _team("home", "Homesteaders", "HM", 28, 36, "d8"),
            },
        }
    )


def _team(prefix, name, short_name, bt, obt, pitch_die):
    roster = []
    lineup = []
    for slot, position in enumerate(POSITIONS, start=1):
        player_id = f"{prefix}-h{slot}"
        traits = (
            ["P+"] if slot == 3
            else ["S+"] if slot == 1
            else ["D+"] if slot == 5
            else []
        )
        roster.append(
            {
                "player_id": player_id,
                "name": DEMO_NAMES[name][slot - 1],
                "role": "position_player",
                "positions": [position],
                "bats": "L" if slot in {1, 3, 6} else "R",
                "throws": "R",
                "bt": bt + (1 if slot == 3 else 0),
                "obt": obt + (1 if slot in {1, 3} else 0),
                "traits": traits,
            }
        )
        lineup.append(
            {"slot": slot, "player_id": player_id, "position": position}
        )
    starter, reliever, closer, reserve, speedster = DEMO_STAFF[name]
    roster.extend(
        (
            {
                "player_id": f"{prefix}-sp",
                "name": starter,
                "role": "starter",
                "positions": ["P"],
                "throws": "R",
                "pitch_die": pitch_die,
                "traits": ["GB+"],
            },
            {
                "player_id": f"{prefix}-rp1",
                "name": reliever,
                "role": "reliever",
                "positions": ["P"],
                "throws": "L",
                "pitch_die": "d4",
                "traits": ["K+"],
            },
            {
                "player_id": f"{prefix}-rp2",
                "name": closer,
                "role": "reliever",
                "positions": ["P"],
                "throws": "R",
                "pitch_die": "d8",
                "traits": [],
            },
            {
                "player_id": f"{prefix}-bench1",
                "name": reserve,
                "role": "position_player",
                "positions": ["UT"],
                "bats": "L",
                "throws": "R",
                "bt": 27,
                "obt": 35,
                "traits": ["C+"],
            },
            {
                "player_id": f"{prefix}-bench2",
                "name": speedster,
                "role": "position_player",
                "positions": ["OF"],
                "bats": "R",
                "throws": "R",
                "bt": 26,
                "obt": 34,
                "traits": ["S+"],
            },
        )
    )
    return {
        "team_id": f"team-{prefix}",
        "name": name,
        "short_name": short_name,
        "lineup": lineup,
        "roster": roster,
        "starting_pitcher_id": f"{prefix}-sp",
    }
