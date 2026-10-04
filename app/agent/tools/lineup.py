"""Deterministic, explicitly system-derived lineup candidates."""

from __future__ import annotations

from sqlalchemy import select

from app.agent.tools.schemas import DeriveLineupInput, SourceRecord, ToolContext, ToolExecutionResult
from app.jcc_data.models import JccHero


def derive_lineup_candidates(context: ToolContext, input: DeriveLineupInput) -> ToolExecutionResult:
    db = context.session_factory()
    try:
        statement = (
            select(JccHero)
            .where(JccHero.snapshot_id == context.snapshot.snapshot_id)
            .order_by(JccHero.external_id)
            .limit(input.candidate_limit * 3)
        )
        heroes = list(db.scalars(statement))
        required = set(input.required_heroes)
        selected = [hero for hero in heroes if hero.external_id in required]
        selected_ids = {hero.external_id for hero in selected}
        selected.extend(hero for hero in heroes if hero.external_id not in selected_ids)
        selected = selected[: min(7, len(selected))]
        candidates = [
            {
                "rank": 1,
                "heroes": [
                    {"external_id": hero.external_id, "name": hero.name, "price": hero.price}
                    for hero in selected
                ],
                "traits": [],
                "equipment": [],
                "reasoning_facts": ["候选由固定官方结构化 snapshot 的实体确定性生成"],
                "risks": ["这是系统推导结果，不是官方热门阵容；实际对局需结合商店和血量调整"],
            }
        ] if selected else []
        snapshot = context.snapshot
        output = {
            "result_type": "system_derived_lineup",
            "is_system_derived": True,
            "goal": input.goal,
            "candidates": candidates,
            "source_type": "system_derived",
            "snapshot_id": snapshot.snapshot_id,
            "version": snapshot.version,
        }
        source = SourceRecord(
            source_type="system_derived",
            snapshot_id=snapshot.snapshot_id,
            version=snapshot.version,
            entity_type="lineup",
            entity_id="system-derived",
            metadata={"goal": input.goal, "candidate_count": len(candidates)},
        )
        return ToolExecutionResult(output, (source,))
    finally:
        db.close()
