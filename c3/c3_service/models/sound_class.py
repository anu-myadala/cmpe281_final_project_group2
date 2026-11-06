"""sound_class: the seven semester classes the classifier outputs.

Design source: Deliverable #2, Part III, Section 3.3.1, Table 94 (row sound_class:
class_id, name, category, default_mic; PK class_id, UQ name; "Seven rows seeded"),
and the entity SoundClass of Section 3.1.3, Table 92. The class ids are the order of
Table 95 (siren = 1 ... environmental = 7), the same ids C1 and the seed already
store in alert.top_class_id.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Enum, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

CATEGORIES = ("EMERGENCY", "ACCIDENT", "SECURITY", "SAFETY", "BACKGROUND")
MICS = ("INTERIOR", "EXTERIOR", "EITHER")

# (class_id, name, category, default_mic). The ids and names are fixed by the design and the
# seed. Table 94 names the two ENUMs but gives no value per class, so category and
# default_mic are our choice: the alert type a class usually raises, and the microphone
# that hears it best.
SOUND_CLASS_SEED = (
    (1, "siren", "EMERGENCY", "EXTERIOR"),
    (2, "crash", "ACCIDENT", "EXTERIOR"),
    (3, "glass_break", "SECURITY", "EITHER"),
    (4, "scream", "EMERGENCY", "INTERIOR"),
    (5, "honking", "SAFETY", "EXTERIOR"),
    (6, "traffic", "BACKGROUND", "EXTERIOR"),
    (7, "environmental", "BACKGROUND", "EITHER"),
)
CLASS_NAMES = {class_id: name for class_id, name, _, _ in SOUND_CLASS_SEED}
CLASS_IDS = {name: class_id for class_id, name, _, _ in SOUND_CLASS_SEED}


def sound_class_name(class_id: Optional[int]) -> Optional[str]:
    """alert.top_class_id -> class name (the topClass field of the API); None when unknown."""
    return CLASS_NAMES.get(class_id) if class_id is not None else None


class SoundClass(Base):
    __tablename__ = "sound_class"

    class_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(Enum(*CATEGORIES, name="sound_class_category"), nullable=False)
    default_mic: Mapped[str] = mapped_column(Enum(*MICS, name="sound_class_mic"), nullable=False)
