from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


@dataclass
class ToolRuntime:
    conninfo: str
    out_dir: str


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="Path to tools.settings.json")
    parser.add_argument("--host", default=None, help="PostgreSQL host")
    parser.add_argument("--port", type=int, default=None, help="PostgreSQL port")
    parser.add_argument("--dbname", default=None, help="PostgreSQL database name")
    parser.add_argument("--user", default=None, help="PostgreSQL user")
    parser.add_argument("--password", default=None, help="PostgreSQL password")
    parser.add_argument("--conn-str", default=None, help="Raw psycopg2 connection string")
    parser.add_argument("--out-dir", default=None, help="Directory for generated files")


def resolve_runtime(args: argparse.Namespace | None = None) -> ToolRuntime:
    args = args or argparse.Namespace()
    cfg = _load_config(getattr(args, "config", None))
    db = cfg.get("db", {})

    raw_conn = (
        getattr(args, "conn_str", None)
        or os.getenv("TOPKGEN_CONN_STR")
        or cfg.get("conn_str")
        or cfg.get("connStr")
    )
    if raw_conn:
        conninfo = raw_conn
    else:
        host = _first(getattr(args, "host", None), os.getenv("TOPKGEN_DB_HOST"), db.get("host"), "localhost")
        port = _first(getattr(args, "port", None), os.getenv("TOPKGEN_DB_PORT"), db.get("port"), 5432)
        dbname = _first(getattr(args, "dbname", None), os.getenv("TOPKGEN_DB_NAME"), db.get("dbname"), db.get("database"), "DDW_AI_DB")
        user = _first(getattr(args, "user", None), os.getenv("TOPKGEN_DB_USER"), db.get("user"), "postgres")
        password = _first(getattr(args, "password", None), os.getenv("TOPKGEN_DB_PASSWORD"), db.get("password"), "")
        conninfo = _build_conninfo(host=host, port=port, dbname=dbname, user=user, password=password)

    out_dir = _first(
        getattr(args, "out_dir", None),
        os.getenv("TOPKGEN_OUT_DIR"),
        cfg.get("out_dir"),
        cfg.get("outDir"),
        cfg.get("outputDirectory"),
        str(DEFAULT_OUTPUT_DIR),
    )
    out_dir = str(Path(os.path.expandvars(os.path.expanduser(str(out_dir)))).resolve())
    os.makedirs(out_dir, exist_ok=True)
    return ToolRuntime(conninfo=conninfo, out_dir=out_dir)


def print_runtime(runtime: ToolRuntime) -> None:
    print(f"Output directory: {runtime.out_dir}")


def _load_config(config_path: str | None) -> dict[str, Any]:
    candidates: list[Path] = []
    if config_path:
        path = Path(os.path.expandvars(os.path.expanduser(config_path))).resolve()
        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found: {path}\n"
                "Create it from Tools/tools.settings.example.json or remove --config and pass DB options explicitly."
            )
        candidates.append(path)
    else:
        candidates.extend([
            PROJECT_ROOT / "tools.settings.json",
            PROJECT_ROOT / "Tools" / "tools.settings.json",
        ])

    for path in candidates:
        path = Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    return {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _build_conninfo(**kwargs: Any) -> str:
    parts = []
    for key, value in kwargs.items():
        text = str(value).replace("\\", "\\\\").replace("'", "\\'")
        parts.append(f"{key}='{text}'")
    return " ".join(parts)
