from sqlalchemy import create_engine, inspect, text

from app import db


def test_init_db_adds_game_type_to_existing_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE game ("
                "id INTEGER PRIMARY KEY, "
                "game_id VARCHAR NOT NULL, "
                "game_date DATE NOT NULL"
                ")"
            )
        )
    monkeypatch.setattr(db, "engine", engine)

    db.init_db()

    columns = {column["name"] for column in inspect(engine).get_columns("game")}
    assert "game_type" in columns
    engine.dispose()
