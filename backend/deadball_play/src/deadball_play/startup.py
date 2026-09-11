"""Interactive start screen and Deadball Web artifact download."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
import json
from pathlib import Path
import sys
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[4]
_LOCAL_ENGINE = None


def _uses_local_service(base_url: str) -> bool:
    parsed = urlsplit(base_url)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.path.rstrip("/") == "/api"
    )


def _call_local_service(callback):
    """Run one operation directly against the repository application service."""
    global _LOCAL_ENGINE
    backend = PROJECT_ROOT / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from sqlalchemy import inspect, text
    from sqlmodel import Session, SQLModel, create_engine

    from app import models  # noqa: F401 - register tables before create_all
    from app.core.config import get_settings
    from app.services.games import GameService

    if _LOCAL_ENGINE is None:
        database = (backend / "deadball_dev.db").resolve()
        _LOCAL_ENGINE = create_engine(f"sqlite:///{database}")
        SQLModel.metadata.create_all(_LOCAL_ENGINE)
        columns = {
            column["name"] for column in inspect(_LOCAL_ENGINE).get_columns("game")
        }
        if "game_type" not in columns:
            with _LOCAL_ENGINE.begin() as connection:
                connection.execute(text("ALTER TABLE game ADD COLUMN game_type VARCHAR"))
    with Session(_LOCAL_ENGINE) as session:
        service = GameService(
            session, allow_network=get_settings().allow_generator_network
        )
        return callback(service)


@dataclass(frozen=True)
class GeneratedArtifacts:
    game_path: Path
    scorecard_path: Path
    save_path: Path
    additional_scorecard_paths: tuple[Path, ...] = ()

    @property
    def scorecard_paths(self) -> tuple[Path, ...]:
        return (self.scorecard_path, *self.additional_scorecard_paths)


def startup_arguments(
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    *,
    base_url: str = "http://127.0.0.1:8000/api",
    root: Path = Path("."),
    today: calendar_date | None = None,
) -> list[str] | None:
    """Show the no-argument start screen and return equivalent CLI arguments."""
    today = today or calendar_date.today()
    while True:
        output_func("\033[2J\033[H")
        _show_box(
            output_func,
            "DEADBALL PLAY",
            (
                "[1] Browse MLB games and generate JSON + PDF",
                "[2] Load a new game JSON",
                "[3] Resume a saved game",
                "[4] Play the fictional demo",
                "[I] Enter an MLB game ID directly",
                "[Q] Quit",
            ),
        )
        choice = input_func("\nChoose an option: ").strip().upper()
        if choice == "1":
            selected = _scheduled_game_arguments(
                input_func,
                output_func,
                base_url=base_url,
                today=today,
            )
            if selected is not None:
                return selected
        elif choice == "2":
            path = _select_game_json(input_func, output_func, root=root)
            if path:
                save_name = _filename_part(Path(path).stem) + ".save.json"
                return [
                    "--game",
                    path,
                    "--save",
                    str(Path("saves") / save_name),
                    *_control_arguments(input_func, output_func),
                    "--return-to-menu",
                ]
        elif choice == "3":
            path = input_func("Saved game path: ").strip()
            if path:
                return ["--resume", path, "--return-to-menu"]
        elif choice == "4":
            return [
                "--demo",
                "--save",
                "saves/demo-game.save.json",
                "--return-to-menu",
            ]
        elif choice == "I":
            game_id = input_func("MLB game ID: ").strip()
            if game_id:
                return _generator_arguments(
                    game_id,
                    input_func,
                    output_func,
                    base_url=base_url,
                )
        elif choice == "Q":
            return None


def list_web_games(
    game_date: str,
    *,
    base_url: str = "http://127.0.0.1:8000/api",
) -> tuple[dict, ...]:
    """Return scheduled MLB games from the shared service or a remote Web API."""
    try:
        calendar_date.fromisoformat(game_date)
    except ValueError as exc:
        raise ValueError("Game date must use YYYY-MM-DD format.") from exc
    if _uses_local_service(base_url):
        result = _call_local_service(lambda service: service.list_games(game_date))
        return tuple(
            {
                "game_id": game.game_id,
                "game_date": str(game.game_date),
                "game_type": game.game_type,
                "home_team": game.home_team,
                "home_team_short": game.home_team_short,
                "away_team": game.away_team,
                "away_team_short": game.away_team_short,
                "description": game.description,
            }
            for game in result.items
        )
    response = _request_json(f"{base_url}/games?{urlencode({'date': game_date})}")
    items = response.get("items", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Deadball Web returned an invalid games list.")
    return tuple(items)


def _scheduled_game_arguments(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    *,
    base_url: str,
    today: calendar_date,
) -> list[str] | None:
    game_date = _input_with_default(
        input_func,
        "Game date: ",
        today.isoformat(),
    )
    try:
        games = list_web_games(game_date, base_url=base_url)
    except ValueError as exc:
        output_func(f"\nCould not load games: {exc}")
        input_func("Press Enter to return to the start screen.")
        return None
    if not games:
        output_func(f"\nNo MLB games were found for {game_date}.")
        input_func("Press Enter to return to the start screen.")
        return None

    menu_lines = []
    for index, game in enumerate(games, start=1):
        away = game.get("away_team") or "Away"
        home = game.get("home_team") or "Home"
        description = game.get("description")
        suffix = f" - {description}" if description else ""
        menu_lines.append(f"[{index}] {away} at {home}{suffix}")
    menu_lines.append("[B] Back")
    _show_box(output_func, f"MLB GAMES — {game_date}", menu_lines)
    selection = input_func("\nChoose a game: ").strip().upper()
    if selection == "B":
        return None
    try:
        game = games[int(selection) - 1]
    except (ValueError, IndexError):
        output_func("Invalid game selection.")
        input_func("Press Enter to return to the start screen.")
        return None
    game_id = str(game.get("game_id", "")).strip()
    if not game_id:
        raise ValueError("The selected game has no MLB game ID.")
    return _generator_arguments(
        game_id,
        input_func,
        output_func,
        base_url=base_url,
        away_team=str(game.get("away_team") or "Away"),
        home_team=str(game.get("home_team") or "Home"),
    )


def _generator_arguments(
    game_id: str,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    *,
    base_url: str,
    away_team: str = "Away",
    home_team: str = "Home",
) -> list[str]:
    """Collect the same generation choices exposed by Deadball Web."""
    _show_box(
        output_func,
        "GENERATOR OPTIONS",
        (
            "[1] Standard traits",
            "[2] SABR traits",
            "[3] Adaptive traits",
            "",
            "PDF score sheets: Both (default), Home, or Away",
            "Result: Generate and play, or generate files only",
        ),
    )
    trait_choice = input_func("Trait mode [1]: ").strip().upper()
    trait_mode = {"2": "sabr", "3": "adaptive"}.get(trait_choice, "standard")
    force = input_func("Refresh cached statistics? [y/N]: ").strip().upper() == "Y"
    side_choice = input_func(
        "PDF score sheets — [B] Both  [H] Home  [A] Away [B]: "
    ).strip().upper()
    scorecard_side = {"H": "home", "A": "away"}.get(side_choice, "both")
    action = input_func(
        "[P] Generate and play  [G] Generate files only [P]: "
    ).strip().upper()
    arguments = [
        "--generate-only" if action == "G" else "--generate-game",
        game_id,
        "--web-base-url",
        base_url,
        "--trait-mode",
        trait_mode,
        "--scorecard-side",
        scorecard_side,
        "--away-team-label",
        away_team,
        "--home-team-label",
        home_team,
    ]
    if force:
        arguments.append("--force-generate")
    if action != "G":
        arguments.extend(
            _control_arguments(
                input_func,
                output_func,
                away_team=away_team,
                home_team=home_team,
            )
        )
    arguments.append("--return-to-menu")
    return arguments


def _control_arguments(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    *,
    away_team: str = "Away team",
    home_team: str = "Home team",
) -> list[str]:
    _show_box(
        output_func,
        "TEAM CONTROL",
        (
            f"Away — {away_team}: [H] Human or [C] Computer",
            f"Home — {home_team}: [H] Human or [C] Computer",
        ),
    )
    away = input_func(f"Control {away_team} — [H] Human  [C] Computer [H]: ")
    home = input_func(f"Control {home_team} — [H] Human  [C] Computer [H]: ")
    return [
        "--away-control",
        "computer" if away.strip().upper() == "C" else "human",
        "--home-control",
        "computer" if home.strip().upper() == "C" else "human",
    ]


def _select_game_json(
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    *,
    root: Path,
) -> str | None:
    directory = root / "saves"
    files = sorted(
        (
            path
            for path in (
                directory.glob("*DeadballPlay.json") if directory.exists() else ()
            )
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if files:
        menu_lines = []
        for index, path in enumerate(files[:20], start=1):
            menu_lines.append(f"[{index}] {path.name}")
        menu_lines.extend(("[P] Enter another path", "[B] Back"))
        _show_box(output_func, "SAVED GAME FILES", menu_lines)
        selection = input_func("\nChoose a JSON file: ").strip().upper()
        if selection == "B":
            return None
        if selection != "P":
            try:
                return str(files[int(selection) - 1])
            except (ValueError, IndexError):
                output_func("Invalid file selection.")
                return None
    return input_func("Generated game JSON path: ").strip() or None


def generate_web_artifacts(
    game_id: str,
    *,
    base_url: str = "http://127.0.0.1:8000/api",
    root: Path = Path("."),
    trait_mode: str = "standard",
    force: bool = False,
    scorecard_side: str = "both",
    progress_func: Callable[[str], None] | None = None,
    away_team_label: str = "Away team",
    home_team_label: str = "Home team",
) -> GeneratedArtifacts:
    """Generate through the shared local service or an explicitly remote API."""
    if trait_mode not in {"standard", "sabr", "adaptive"}:
        raise ValueError(f"Unsupported trait mode: {trait_mode}")
    if scorecard_side not in {"both", "home", "away"}:
        raise ValueError(f"Unsupported scorecard side: {scorecard_side}")
    sides = ("home", "away") if scorecard_side == "both" else (scorecard_side,)
    total_steps = len(sides) + 4

    def report(step: int, message: str) -> None:
        if progress_func is None:
            return
        bar = "#" * step + "-" * (total_steps - step)
        progress_func(f"[{bar}] {step}/{total_steps} {message}")

    report(1, f"Generating {away_team_label} ratings and roster...")
    report(2, f"Generating {home_team_label} ratings and roster...")
    local = _uses_local_service(base_url)
    if local:
        _call_local_service(
            lambda service: service.generate_game(
                game_id, force=force, trait_mode=trait_mode
            )
        )
    else:
        _request_json(
            f"{base_url}/games/{game_id}/generate",
            method="POST",
            body={"force": force, "trait_mode": trait_mode},
        )
    report(3, "Downloading game JSON...")
    game = (
        _call_local_service(lambda service: service.play_json(game_id))
        if local
        else _request_json(f"{base_url}/games/{game_id}/play.json")
    )
    game_info = game.get("game", {})
    teams = game.get("teams", {})
    date = str(game_info.get("game_date", "game"))
    away = _filename_part(teams.get("away", {}).get("name", "Away"))
    home = _filename_part(teams.get("home", {}).get("name", "Home"))
    stem = f"{date}-{away}-at-{home}-DeadballPlay"
    game_path = root / "saves" / f"{stem}.json"
    scorecard_paths = tuple(
        root / "saves" / f"{stem}-{side}.pdf" for side in sides
    )
    save_path = root / "saves" / f"{stem}.save.json"
    scorecards = []
    for index, side in enumerate(sides, start=4):
        report(index, f"Downloading {side} PDF score sheet...")
        scorecards.append(
            _call_local_service(
                lambda service, selected=side: service.scorecard_pdf(
                    game_id, side=selected
                )
            )
            if local
            else _request_bytes(
                f"{base_url}/games/{game_id}/scorecard.pdf?"
                f"{urlencode({'side': side})}"
            )
        )
    report(total_steps, "Saving artifact bundle...")
    game_path.parent.mkdir(parents=True, exist_ok=True)
    game_path.write_text(json.dumps(game, indent=2) + "\n", encoding="utf-8")
    for scorecard_path, scorecard in zip(scorecard_paths, scorecards):
        scorecard_path.write_bytes(scorecard)
    return GeneratedArtifacts(
        game_path,
        scorecard_paths[0],
        save_path,
        scorecard_paths[1:],
    )


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        try:
            with urlopen(request, timeout=120) as response:
                result = json.loads(response.read())
        except HTTPError:
            raise
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Deadball Web returned HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, URLError) as exc:
        raise ValueError(
            "Could not reach Deadball Web before the request timed out. "
            "Confirm ./run_dev.sh is running and try again."
        ) from exc
    if not isinstance(result, dict):
        raise ValueError("Deadball Web returned an invalid game document")
    return result


def _request_bytes(url: str) -> bytes:
    try:
        with urlopen(url, timeout=120) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Deadball Web returned HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, URLError) as exc:
        raise ValueError(
            "Could not reach Deadball Web before the request timed out. "
            "Confirm ./run_dev.sh is running and try again."
        ) from exc


def _filename_part(value: str) -> str:
    cleaned = "".join(character for character in value if character.isalnum())
    return cleaned or "Team"


def _show_box(
    output_func: Callable[[str], None],
    title: str,
    lines: tuple[str, ...] | list[str],
) -> None:
    content = [title, "", *lines]
    width = max(48, min(100, max(len(line) for line in content) + 4))
    output_func("┌" + "─" * (width - 2) + "┐")
    for line in content:
        output_func("│ " + line[: width - 4].ljust(width - 4) + " │")
    output_func("└" + "─" * (width - 2) + "┘")


def _input_with_default(
    input_func: Callable[[str], str],
    prompt: str,
    default: str,
) -> str:
    """Offer an editable terminal default, with a portable Enter fallback."""
    visible_prompt = f"{prompt}[{default}]: "
    if input_func is not input:
        return input_func(visible_prompt).strip() or default
    try:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return _terminal_input_with_default(prompt, default)
    except (AttributeError, OSError):
        pass
    return input_func(visible_prompt).strip() or default


def _terminal_input_with_default(prompt: str, default: str) -> str:
    """Read one editable line with an actual pre-populated terminal buffer."""
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    original = termios.tcgetattr(descriptor)
    buffer = list(default)
    cursor = len(buffer)

    def redraw() -> None:
        sys.stdout.write("\r" + prompt + "".join(buffer) + "\033[K")
        remaining = len(buffer) - cursor
        if remaining:
            sys.stdout.write(f"\033[{remaining}D")
        sys.stdout.flush()

    try:
        tty.setraw(descriptor)
        redraw()
        while True:
            character = os.read(descriptor, 1)
            if character in {b"\r", b"\n"}:
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return "".join(buffer).strip() or default
            if character == b"\x03":
                raise KeyboardInterrupt
            if character == b"\x04":
                if not buffer:
                    raise EOFError
                continue
            if character in {b"\x7f", b"\x08"}:
                if cursor:
                    del buffer[cursor - 1]
                    cursor -= 1
            elif character == b"\x1b":
                sequence = os.read(descriptor, 1)
                if sequence == b"[":
                    key = os.read(descriptor, 1)
                    if key == b"D":
                        cursor = max(0, cursor - 1)
                    elif key == b"C":
                        cursor = min(len(buffer), cursor + 1)
                    elif key == b"H":
                        cursor = 0
                    elif key == b"F":
                        cursor = len(buffer)
                    elif key == b"3":
                        if os.read(descriptor, 1) == b"~" and cursor < len(buffer):
                            del buffer[cursor]
            else:
                try:
                    text = character.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if text.isprintable():
                    buffer.insert(cursor, text)
                    cursor += 1
            redraw()
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
