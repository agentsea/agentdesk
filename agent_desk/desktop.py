from __future__ import annotations
import uuid

from agent_desk.db.conn import WithDB
from agent_desk.db.models import V1DesktopRecord
from agent_desk.server.models import V1Desktop, V1Desktops


class Desktop(WithDB):
    """A remote desktop which is accesible for AI agents"""

    def __init__(self, name: str, addr: str) -> None:
        self.name = name
        self.addr = addr
        self.id = str(uuid.uuid4())

        # TODO: check for health
        self.status = "active"
        self.save()

    def to_record(self) -> V1DesktopRecord:
        return V1DesktopRecord(
            id=self.id,
            name=self.name,
            addr=self.addr,
        )

    def save(self) -> None:
        for db in self.get_db():
            db.merge(self.to_record())
            db.commit()

    @classmethod
    def from_record(cls, record: V1DesktopRecord) -> Desktop:
        out = cls.__new__(Desktop)
        out.id = record.id
        out.name = record.name
        out.addr = record.addr
        out.status = record.status
        return out

    @classmethod
    def load(cls, id: str) -> Desktop:
        for db in cls.get_db():
            record = db.query(V1DesktopRecord).filter(V1DesktopRecord.id == id).first()
            if record is None:
                raise ValueError(f"Desktop with id {id} not found")
            return cls.from_record(record)

    @classmethod
    def list(cls) -> list[Desktop]:
        out = []
        for db in cls.get_db():
            records = db.query(V1DesktopRecord).all()
            for record in records:
                out.append(cls.from_record(record))
        return out

    @classmethod
    def list_v1(cls) -> V1Desktops:
        out = []
        for desktop in cls.list():
            out.append(desktop.to_v1_schema())
        return V1Desktops(desktops=out)

    @classmethod
    def delete(cls, id: str) -> None:
        for db in cls.get_db():
            record = db.query(V1DesktopRecord).filter(V1DesktopRecord.id == id).first()
            if record is None:
                raise ValueError(f"Desktop with id {id} not found")
            db.delete(record)
            db.commit()

    @classmethod
    def create(cls, name: str) -> Desktop:
        pass

    def to_v1_schema(self) -> V1Desktop:
        return V1Desktop(
            id=self.id,
            name=self.name,
            addr=self.addr,
        )
