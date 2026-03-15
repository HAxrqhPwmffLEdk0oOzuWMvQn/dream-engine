from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class ProjectInfo(BaseModel):
    name: str
    path: Path
    memory_bank_path: Path | None = None
    claude_md_path: Path | None = None
    status_md_path: Path | None = None
    has_memory_bank: bool = False


class MemoryChunk(BaseModel):
    text: str
    project: str
    chunk_type: str  # summary, state, tech, capability, next, connection


class ProjectKnowledge(BaseModel):
    name: str
    slug: str
    path: Path
    summary: str = ""
    current_state: str = ""
    tech_stack: str = ""
    capabilities: list[str] = []
    next_steps: str = ""
    active_context_full: str = ""
    files_read: list[str] = []

    def to_memory_chunks(self) -> list[MemoryChunk]:
        chunks = []
        today = datetime.now().strftime("%Y-%m-%d")

        if self.summary:
            chunks.append(MemoryChunk(
                text=f"[project:{self.name}] [summary] {self.summary}",
                project=self.name,
                chunk_type="summary",
            ))

        if self.current_state:
            chunks.append(MemoryChunk(
                text=f"[project:{self.name}] [state] {self.current_state}",
                project=self.name,
                chunk_type="state",
            ))

        if self.tech_stack:
            chunks.append(MemoryChunk(
                text=f"[project:{self.name}] [tech] {self.tech_stack}",
                project=self.name,
                chunk_type="tech",
            ))

        for cap in self.capabilities:
            chunks.append(MemoryChunk(
                text=f"[project:{self.name}] [capability] {cap}",
                project=self.name,
                chunk_type="capability",
            ))

        if self.next_steps:
            chunks.append(MemoryChunk(
                text=f"[project:{self.name}] [next] {self.next_steps}",
                project=self.name,
                chunk_type="next",
            ))

        return chunks

    def to_wiki_page(self) -> str:
        lines = [f"# {self.name}\n"]
        if self.summary:
            lines.append(f"{self.summary}\n")
        if self.current_state:
            lines.append(f"## Current State\n{self.current_state}\n")
        if self.tech_stack:
            lines.append(f"## Tech Stack\n{self.tech_stack}\n")
        if self.capabilities:
            lines.append("## Capabilities")
            for cap in self.capabilities:
                lines.append(f"- {cap}")
            lines.append("")
        if self.next_steps:
            lines.append(f"## Next Steps\n{self.next_steps}\n")
        return "\n".join(lines)


class IngestResult(BaseModel):
    project: str
    chunks_uploaded: int
    knowledge_doc_uploaded: bool
    wiki_updated: bool
    timestamp: str


class IngestReport(BaseModel):
    projects_discovered: int
    projects_ingested: int
    projects_skipped: int
    total_chunks: int
    results: list[IngestResult]
    duration_sec: float


class DreamStatus(BaseModel):
    running: bool = False
    phase: str = ""
    started_at: str | None = None
    elapsed_sec: float = 0
    current_cluster: str = ""
    completed_clusters: list[str] = []
