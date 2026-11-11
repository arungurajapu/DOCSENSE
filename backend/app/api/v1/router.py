"""Aggregate router for DocSense API v1 endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.documents import router as documents_router
from app.api.v1.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(documents_router)

__all__ = ["api_router"]
