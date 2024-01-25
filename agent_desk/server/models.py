from typing import List, Optional

from pydantic import BaseModel


class V1Health(BaseModel):
    status: Optional[str] = None


class V1Info(BaseModel):
    version: Optional[str] = None


class V1DesktopReqeust(BaseModel):
    name: Optional[str] = None
    memory: Optional[str] = None
    cpu: Optional[int] = None
    disk: Optional[str] = None


class V1DesktopRegistration(BaseModel):
    name: Optional[str] = None
    addr: Optional[str] = None


class V1Desktop(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    addr: Optional[str] = None
    status: Optional[str] = None
    created: Optional[float] = None
    memory: Optional[str] = None
    cpu: Optional[int] = None
    disk: Optional[str] = None
    memory_usage: Optional[float] = None
    cpu_usage: Optional[float] = None
    disk_usage: Optional[float] = None


class V1Desktops(BaseModel):
    desktops: List[V1Desktop]
