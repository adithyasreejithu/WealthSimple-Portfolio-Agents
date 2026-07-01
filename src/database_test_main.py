"""Temporary command-line entry point for testing database creation."""

from __future__ import annotations

import argparse
from pathlib import Path

from config import DATA_FOLDER
from database import (
    close_connection,
    get_shared_connection,
    initialize_database,
    is_database_active,
)


DEFAULT_TEST_DATABASE_PATH = DATA_FOLDER / "TEST_WealthSimple.duckdb"


class DatabaseCreationMain:
    """Create or verify a database and print its schema status."""

    def __init__(self, db_path: str | Path = DEFAULT_TEST_DATABASE_PATH):
        self.db_path = Path(db_path).expanduser().resolve()

    def run(self) -> bool:
        """
        Initialize the database and display the resulting table names.

        Returns True when a new schema was created and False when the existing
        schema was already active.
        """
        try:
            created = initialize_database(self.db_path)
            connection = get_shared_connection(self.db_path)
            tables = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                ).fetchall()
            ]

            print(f"Database: {self.db_path}")
            print(f"Status: {'created' if created else 'already active'}")
            print(f"Schema active: {is_database_active(connection)}")
            print("Tables:")
            for table in tables:
                print(f"  - {table}")

            return created
        finally:
            close_connection()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or verify the temporary WealthSimple test database."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_TEST_DATABASE_PATH,
        help=(
            "Test database path. Defaults to "
            f"{DEFAULT_TEST_DATABASE_PATH}."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    DatabaseCreationMain(args.db_path).run()


if __name__ == "__main__":
    main()
