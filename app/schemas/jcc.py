from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class JccResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JccSnapshotMetadata(JccResponseModel):
    mode: str
    mode_name: str
    season: str
    version: str
    revision: int
    content_hash: str
    source_updated_at: str | None


class JccSnapshotData(JccResponseModel):
    source_count: int = Field(ge=0)


class JccTraitReference(JccResponseModel):
    id: str
    name: str
    kind: Literal["race", "job"]


class JccHeroItem(JccResponseModel):
    id: str
    name: str
    price: int | None
    hero_type: str | None
    map_id: int | None
    health: int | None
    attack_damage: int | None
    armor: int | None
    magic_resist: int | None
    attack_speed: float | None
    attack_range: int | None
    initial_mana: int | None
    max_mana: int | None
    skill_name: str | None
    skill_description: str | None
    skill_values: dict[str, str] | None
    image_url: str | None
    skill_icon_url: str | None
    traits: list[JccTraitReference]
    classes: list[JccTraitReference]


class JccTraitTierItem(JccResponseModel):
    id: str
    tier_order: int
    activation_count: int
    level: int
    description: str | None
    real_description: str | None


class JccTraitItem(JccResponseModel):
    id: str
    kind: Literal["race", "job"]
    name: str
    prefix: str | None
    max_level: int | None
    activation_list: list[int]
    image_url: str | None
    map_id: int | None
    tiers: list[JccTraitTierItem]


class JccEquipmentComponent(JccResponseModel):
    id: str
    name: str


class JccEquipmentItem(JccResponseModel):
    id: str
    name: str
    type: str | None
    basic_description: str | None
    description: str | None
    image_url: str | None
    components: list[JccEquipmentComponent]


class JccAugmentItem(JccResponseModel):
    id: str
    name: str
    level: int | None
    description: str | None
    icon_url: str | None


class JccAdventureItem(JccResponseModel):
    id: str
    title: str
    description: str | None
    price: int | None
    category: str | None
    logo_url: str | None
    video_url: str | None
    background_image_url: str | None


class JccGalaxyItem(JccResponseModel):
    id: str
    name: str
    description: str | None
    logo_url: str | None
    video_url: str | None
    background_image_url: str | None


DataT = TypeVar("DataT")
ItemT = TypeVar("ItemT")


class JccDataResponse(JccResponseModel, Generic[DataT]):
    snapshot: JccSnapshotMetadata
    data: DataT


class JccListResponse(JccResponseModel, Generic[ItemT]):
    snapshot: JccSnapshotMetadata
    items: list[ItemT]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
