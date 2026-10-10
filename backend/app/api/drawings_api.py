"""Authenticated persistence for Focus drawings; guests remain local-only."""
from __future__ import annotations
import time
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.api.auth import User, current_user
from app.config import get_settings
from app.db import ChartDrawing, get_session_factory

router=APIRouter(prefix="/api/v1", tags=["drawings"])
AUTHORIZED=Annotated[User, Depends(current_user)]
class DrawingPayload(BaseModel):
    drawings: list[dict] = Field(default_factory=list, max_length=200)
def require(user: User|None)->User:
    if user is None: raise HTTPException(status.HTTP_401_UNAUTHORIZED,"sign in to sync drawings")
    return user
@router.get("/drawings/{symbol}")
async def get_drawings(symbol:str,user:AUTHORIZED):
    user=require(user); factory=get_session_factory(get_settings().database_url)
    async with factory() as sess:
        row=await sess.scalar(select(ChartDrawing).where(ChartDrawing.user_id==user.id,ChartDrawing.symbol==symbol.upper()))
        return {"symbol":symbol.upper(),"drawings":row.drawings if row else []}
@router.put("/drawings/{symbol}")
async def put_drawings(symbol:str,body:DrawingPayload,user:AUTHORIZED):
    user=require(user); factory=get_session_factory(get_settings().database_url)
    async with factory() as sess:
        row=await sess.scalar(select(ChartDrawing).where(ChartDrawing.user_id==user.id,ChartDrawing.symbol==symbol.upper()))
        if row is None: row=ChartDrawing(user_id=user.id,symbol=symbol.upper(),drawings=body.drawings);sess.add(row)
        else: row.drawings=body.drawings;row.updated_at=time.time()
        await sess.commit()
        return {"symbol":symbol.upper(),"drawings":row.drawings}
