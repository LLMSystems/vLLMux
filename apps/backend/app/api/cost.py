"""Cost dashboard: spend per model / per API key, and the model price table.

Cost = token usage x per-1M-token prices (input/output). Reading the summary is
open like /usage and /requests; editing prices is admin-only.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.auth import require_admin
from app.services.cost import build_summary

router = APIRouter(prefix="/cost", tags=["cost"])


def _store(request: Request):
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "store unavailable")
    return store


@router.get("/summary")
async def cost_summary(
    request: Request, since: Optional[float] = None, until: Optional[float] = None
):
    store = _store(request)
    settings = request.app.state.settings
    by_model, by_key, prices = (
        await store.token_usage_by_model(since=since, until=until),
        await store.token_usage_by_key(since=since, until=until),
        await store.list_model_prices(),
    )
    return build_summary(
        by_model, by_key, prices,
        settings.default_input_price, settings.default_output_price, settings.price_currency,
    )


@router.get("/prices")
async def list_prices(request: Request):
    settings = request.app.state.settings
    return {
        "currency": settings.price_currency,
        "default_input_price": settings.default_input_price,
        "default_output_price": settings.default_output_price,
        "prices": await _store(request).list_model_prices(),
    }


class PriceBody(BaseModel):
    input_price: float = Field(ge=0)
    output_price: float = Field(ge=0)
    currency: Optional[str] = None


@router.put("/prices/{model}", dependencies=[Depends(require_admin)])
async def set_price(model: str, body: PriceBody, request: Request):
    settings = request.app.state.settings
    await _store(request).set_model_price(
        model, body.input_price, body.output_price, body.currency or settings.price_currency,
    )
    return {"model": model, "input_price": body.input_price, "output_price": body.output_price}


@router.delete("/prices/{model}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def delete_price(model: str, request: Request):
    if not await _store(request).delete_model_price(model):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no price for {model}")
