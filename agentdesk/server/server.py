from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .models import V1Desktop, V1Desktops, V1DesktopReqeust, V1DesktopRegistration
from agentdesk.vm import DesktopVM

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "Agent in the shell"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/desktops", response_model=V1Desktops)
async def desktops() -> V1Desktops:
    return DesktopVM.find_v1()


@app.post("/v1/desktops", response_model=V1Desktop)
async def create_desktop(desktop: V1DesktopReqeust) -> V1Desktop:
    return DesktopVM.create(desktop.name).to_v1_schema()


@app.post("/v1/register", response_model=V1Desktop)
async def register_desktop(desktop: V1DesktopRegistration) -> V1Desktop:
    return DesktopVM(name=desktop.name, addr=desktop.addr).to_v1_schema()


@app.get("/v1/desktops/{id}", response_model=V1Desktop)
async def get_desktop(id: str) -> V1Desktop:
    desktop = DesktopVM.load(id)
    return desktop.to_v1_schema()


@app.delete("/v1/desktops/{id}")
async def delete_desktop(id: str) -> None:
    DesktopVM.delete(id)
