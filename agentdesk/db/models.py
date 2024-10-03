from sqlalchemy import Column, Integer, String, Boolean, Float
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class V1HealthRecord(Base):
    __tablename__ = "v1_health"

    id = Column(Integer, primary_key=True)
    status = Column(String)


class V1DesktopRecord(Base):
    __tablename__ = "v1_desktops"

    id = Column(String, primary_key=True)
    name = Column(String)
    addr = Column(String, nullable=True)
    status = Column(String)
    created = Column(Float)
    cpu = Column(Integer, nullable=True)
    memory = Column(Integer, nullable=True)
    disk = Column(String, nullable=True)
    pid = Column(Integer, nullable=True)
    image = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    requires_proxy = Column(Boolean, nullable=True)
    ssh_port = Column(Integer, nullable=True)
    reserved_ip = Column(Boolean, nullable=True)
    meta = Column(String, nullable=True)
    owner_id = Column(String, nullable=True)
    key_pair_name = Column(String, nullable=True)
    agentd_port = Column(Integer, nullable=True)
    vnc_port = Column(Integer, nullable=True)
    vnc_port_https = Column(Integer, nullable=True)
    basic_auth_user = Column(String, nullable=True)
    basic_auth_password = Column(String, nullable=True)
    resource_name = Column(String, nullable=True)
    namespace = Column(String, nullable=True)


class SSHKeyRecord(Base):
    __tablename__ = "ssh_keys"

    id = Column(String, primary_key=True, index=True)
    owner_id = Column(String, nullable=False)
    public_key = Column(String, index=True)
    private_key = Column(String)
    name = Column(String, index=True)
    created = Column(Float)
    full_name = Column(String, unique=True, index=True)
    metadata_ = Column(String)
