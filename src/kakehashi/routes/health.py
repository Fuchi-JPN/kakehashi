from fastapi import APIRouter
from .. import __version__

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "version": __version__}
