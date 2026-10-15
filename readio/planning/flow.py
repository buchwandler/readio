"""Small adapters for traversing UtterPlan's public schema-v7 flow model."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FlowSegmentRef:
    """One segment with a stable Readio locator and its owning flow identity."""

    flow_index: int
    segment_index: int
    flow: Any
    segment: Any

    @property
    def id(self) -> str:
        return f"f{self.flow_index:06d}.s{self.segment_index:03d}"

    @property
    def unit_id(self) -> str:
        return f"flow-{self.flow_index:06d}-{self.flow.content_hash}"

    @property
    def flow_hash(self) -> str:
        return str(self.flow.content_hash)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.segment, name)


def iter_flow_segments(plan: Any) -> Iterator[FlowSegmentRef]:
    """Yield schema-v7 FlowPlan segments in canonical source order."""
    for flow_index, flow in enumerate(plan.flow):
        for segment_index, segment in enumerate(flow.segments):
            yield FlowSegmentRef(flow_index, segment_index, flow, segment)


def document_semantics(plan: Any) -> Mapping[str, Any]:
    """Return the public document semantic metadata mapping, if present."""
    document = getattr(plan, "document", None)
    semantics = getattr(document, "semantics", {})
    return semantics if isinstance(semantics, Mapping) else {}
