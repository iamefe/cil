import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

# Load .env from project root (two levels up from this file)
env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(env_path)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "cil"
COLLECTION_NAME = "cil_index"


def get_client() -> MongoClient:
    """Get a MongoDB client instance."""
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



