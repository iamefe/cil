import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

# Load .env from project root (two levels up from this file)
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

MONGO_URI = os.environ.get("MONGO_URI") or None
DB_NAME = "cil"
COLLECTION_NAME = "cil_index"


def _sanitize_error(error):
    """Strip sensitive connection details from database error strings.

    Removes MongoDB URIs, hostnames with ports, and auth mechanism names so
    only generic diagnostic information reaches clients.
    """
    msg = str(error)
    # Remove full mongodb:// URIs
    msg = re.sub(r'mongodb[+]?(?:srv)?://\S+', 'mongodb://<redacted>', msg)
    # Remove hostname:port patterns that leak server addresses
    msg = re.sub(r'(?<![:/])(?:[a-zA-Z0-9_-]+\.){1,3}[a-z]{2,}(?::\d+)', '<host>:<port>', msg)
    # Remove standalone port references like :27017
    msg = re.sub(r':(?:2701[0-9]|2801[0-9])\b', ':<port>', msg)
    # Remove auth mechanism identifiers (SCRAM-SHA-*)
    msg = re.sub(r'SCRAM-SHA-[0-9]+', '<auth-mechanism>', msg)
    return msg


def get_client() -> MongoClient:
    """Get a MongoDB client instance."""
    if not MONGO_URI:
        raise ValueError("MONGO_URI environment variable required for MongoDB mode")
    return MongoClient(MONGO_URI)


def get_db():
    """Get the CIL database."""
    return get_client()[DB_NAME]


def get_collection():
    """Get the CIL index collection."""
    return get_db()[COLLECTION_NAME]


def ensure_indexes():
    """Create MongoDB indexes for fast queries."""
    col = get_collection()
    col.create_index("project_path", unique=True)
    col.create_index("file_indices.symbols.name")
    col.create_index("mutations.target")
    col.create_index("call_graph.caller")
    col.create_index("call_graph.callee")
    col.create_index("anomalies.severity")
    col.create_index("anomalies.type")



