from sqlalchemy import Column, Integer, String, ForeignKey, Table, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import JSONB  # If using PostgreSQL

Base = declarative_base()


class V1HealthRecord(Base):
    __tablename__ = "v1_health"
    id = Column(Integer, primary_key=True)
    status = Column(String)


class V1DesktopRecord(Base):
    __tablename__ = "v1_desktops"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    addr = Column(String)
    status = Column(String)
