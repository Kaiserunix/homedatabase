from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Annotated

from fastapi import HTTPException, status
from pydantic import Field

MAX_SAFE_MINOR = 2**53 - 1

SignedMinor = Annotated[int, Field(strict=True, ge=-MAX_SAFE_MINOR, le=MAX_SAFE_MINOR)]
NonNegativeMinor = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_MINOR)]
PositiveMinor = Annotated[int, Field(strict=True, gt=0, le=MAX_SAFE_MINOR)]
FxRate = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=10)]


def calculate_base_minor(amount_minor: int, fx_rate: Decimal) -> int:
    """Convert a positive minor-unit amount with deterministic half-up rounding."""
    try:
        with localcontext() as context:
            context.prec = 50
            converted = (Decimal(amount_minor) * fx_rate).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
    except (InvalidOperation, ValueError, OverflowError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="汇率折算结果无效",
        ) from None
    result = int(converted)
    if result < 1 or result > MAX_SAFE_MINOR:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="汇率折算金额超出安全范围",
        )
    return result


def require_safe_signed_minor(value: int, *, detail: str = "金额超出安全范围") -> int:
    if value < -MAX_SAFE_MINOR or value > MAX_SAFE_MINOR:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=detail,
        )
    return value
