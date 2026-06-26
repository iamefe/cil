from datetime import datetime

from pydantic import BaseModel, Field


class SymbolInfo(BaseModel):
    """A single symbol (function, class, variable) extracted from AST."""
    name: str
    kind: str  # function, class, variable, module, import
    file_path: str
    line_start: int
    line_end: int
    signature: str = ""
    docstring: str = ""
    decorators: list[str] = Field(default_factory=list)

    # Semantic enrichment (Phase 2)
    purpose: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    complexity: str = ""  # low, medium, high
    audit_notes: str = ""


class CallEdge(BaseModel):
    """Edge in the call graph."""
    caller: str  # "file:line:function"
    callee: str  # "file:line:function"
    line: int


class MutationInfo(BaseModel):
    """A mutation (write) to a variable or global state."""
    target: str  # variable name or path (e.g., "_VISION_READY", "self.status")
    source: str  # "file:line:function"
    line: int
    kind: str = ""  # assign, augment, delete


class FileIndex(BaseModel):
    """Index for a single file."""
    file_path: str
    symbols: list[SymbolInfo] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    indexed_at: datetime = Field(default_factory=datetime.utcnow)


class Anomaly(BaseModel):
    """A detected code anomaly."""
    type: str
    severity: str  # low, medium, high
    file_path: str
    line: int
    message: str


class CILIndex(BaseModel):
    """Top-level index document stored in MongoDB."""
    project_path: str
    file_indices: dict[str, FileIndex] = Field(default_factory=dict)
    call_graph: list[CallEdge] = Field(default_factory=list)
    mutations: list[MutationInfo] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    indexed_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1

