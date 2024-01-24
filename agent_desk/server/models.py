from typing import List, Optional

from pydantic import BaseModel


class V1Health(BaseModel):
    status: Optional[str] = None


class V1Info(BaseModel):
    version: Optional[str] = None


class V1DesktopReqeust(BaseModel):
    name: Optional[str] = None
    memory: Optional[str] = None
    cpu: Optional[str] = None
    disk: Optional[str] = None


class V1DesktopRegistration(BaseModel):
    name: Optional[str] = None
    addr: Optional[str] = None


class V1Desktop(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    addr: Optional[str] = None
    status: Optional[str] = None
    memory: Optional[str] = None
    cpu: Optional[str] = None
    disk: Optional[str] = None
    created: Optional[float] = None
    memory_usage: Optional[str] = None
    cpu_usage: Optional[str] = None
    disk_usage: Optional[str] = None


class V1Desktops(BaseModel):
    desktops: List[V1Desktop]
