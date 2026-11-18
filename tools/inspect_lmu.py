"""Dump the structure of an LMU telemetry .duckdb file.

Usage:  python tools/inspect_lmu.py path/to/session.duckdb > docs/lmu_schema.txt

Prints every table, its columns, row count and a few sample rows.
Paste the output back so the loader in simtracker/telemetry/loader.py can be fitted to the real layout.
"""
import sys

import duckdb


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables ORDER BY table_name").fetchall()]
    print(f"FILE: {path}")
    print(f"TABLES: {len(tables)}\n")
    for t in tables:
        q = '"' + t.replace('"', '""') + '"'
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position", [t]).fetchall()
        n = con.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
        print(f"== {t}  rows={n}")
        print("   columns:", ", ".join(f"{c} ({d})" for c, d in cols))
        if n:
            for row in con.execute(f"SELECT * FROM {q} LIMIT 3").fetchall():
                text = str(row)
                print("   sample:", text[:200] + ("..." if len(text) > 200 else ""))
        print()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
