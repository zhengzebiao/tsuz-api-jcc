import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.app_setting import AppSetting
from app.models.sample_profile import SampleProfile

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "site_name": ("Backend API", "Display name for the generated API service"),
    "feature.profile_demo_enabled": ("true", "Enable the scaffolded profile demo endpoint"),
}
DEFAULT_SAMPLE_PROFILES = {
    "starter-profile": "Starter Profile",
}


def ensure_setting(db: Session, key: str, value: str, description: str) -> AppSetting:
    setting = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if setting is not None:
        logger.info("seed skipped existing setting key=%s", key)
        return setting
    setting = AppSetting(key=key, value=value, description=description)
    db.add(setting)
    db.flush()
    logger.info("seed created setting key=%s", key)
    return setting


def ensure_sample_profile(db: Session, slug: str, display_name: str) -> SampleProfile:
    profile = db.scalar(select(SampleProfile).where(SampleProfile.slug == slug))
    if profile is not None:
        logger.info("seed skipped existing sample profile slug=%s", slug)
        return profile
    profile = SampleProfile(slug=slug, display_name=display_name, is_active=True)
    db.add(profile)
    db.flush()
    logger.info("seed created sample profile slug=%s", slug)
    return profile


def seed(db: Session) -> None:
    logger.info("seed started target=python-app")
    for key, (value, description) in DEFAULT_SETTINGS.items():
        ensure_setting(db, key, value, description)
    for slug, display_name in DEFAULT_SAMPLE_PROFILES.items():
        ensure_sample_profile(db, slug, display_name)
    logger.info("seed completed target=python-app")


def main() -> None:
    configure_logging()
    db = SessionLocal()
    try:
        seed(db)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("seed failed target=python-app")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
