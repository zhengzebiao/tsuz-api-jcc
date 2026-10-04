"""Static registry for safe Agent tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.agent.tools.schemas import ToolContext, ToolExecutionResult


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    execute: Callable[[ToolContext, BaseModel], ToolExecutionResult]

    def schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


class ToolRegistry:
    def __init__(self, definitions: tuple[ToolDefinition, ...]) -> None:
        names = [item.name for item in definitions]
        if len(names) != len(set(names)):
            raise ValueError("duplicate Agent tool name")
        self._definitions = {item.name: item for item in definitions}

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions[name] for name in sorted(self._definitions))

    def provider_definitions(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.schema(),
                },
            }
            for definition in self.definitions()
        )


def build_default_registry() -> ToolRegistry:
    from app.agent.tools.lineup import derive_lineup_candidates
    from app.agent.tools.schemas import (
        DeriveLineupInput,
        EmptyInput,
        ExternalIdInput,
        SearchAdventuresInput,
        SearchAugmentsInput,
        SearchEquipmentInput,
        SearchGalaxiesInput,
        SearchHeroesInput,
        SearchTraitsInput,
    )
    from app.agent.tools.structured import (
        get_equipment,
        get_hero,
        get_snapshot_metadata,
        get_trait,
        search_adventures,
        search_augments,
        search_equipment,
        search_galaxies,
        search_heroes,
        search_traits,
    )

    return ToolRegistry(
        (
            ToolDefinition("derive_lineup_candidates", "Derive bounded system lineup candidates from the fixed official snapshot.", DeriveLineupInput, derive_lineup_candidates),
            ToolDefinition("get_equipment", "Get one official equipment item by external id.", ExternalIdInput, get_equipment),
            ToolDefinition("get_hero", "Get one official hero by external id.", ExternalIdInput, get_hero),
            ToolDefinition("get_snapshot_metadata", "Get metadata for the fixed official data snapshot.", EmptyInput, get_snapshot_metadata),
            ToolDefinition("get_trait", "Get one official trait by external id.", ExternalIdInput, get_trait),
            ToolDefinition("search_adventures", "Search official adventures in the fixed snapshot.", SearchAdventuresInput, search_adventures),
            ToolDefinition("search_augments", "Search official augments in the fixed snapshot.", SearchAugmentsInput, search_augments),
            ToolDefinition("search_equipment", "Search official equipment in the fixed snapshot.", SearchEquipmentInput, search_equipment),
            ToolDefinition("search_galaxies", "Search official galaxies in the fixed snapshot.", SearchGalaxiesInput, search_galaxies),
            ToolDefinition("search_heroes", "Search official heroes in the fixed snapshot.", SearchHeroesInput, search_heroes),
            ToolDefinition("search_traits", "Search official traits in the fixed snapshot.", SearchTraitsInput, search_traits),
        )
    )
