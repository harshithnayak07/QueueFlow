from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class OwnerRegister(BaseModel):
    shop_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=255)
    # bcrypt limit is 72 bytes; keep ASCII-ish passwords within it.
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def email_must_look_plausible(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email address")
        return v

    @field_validator("shop_name")
    @classmethod
    def shop_name_stripped(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("shop_name must not be blank")
        return v


class OwnerLogin(BaseModel):
    email: str
    password: str


class OwnerOut(BaseModel):
    id: int
    shop_name: str
    email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
