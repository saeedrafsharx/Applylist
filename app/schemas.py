from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{3,64}$")


class RegisterForm(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    full_name: Optional[str] = Field(default=None, max_length=200)

    model_config = {"str_strip_whitespace": True}

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        if not USERNAME_RE.fullmatch(v):
            raise ValueError(
                "Username may use letters, numbers, dots, dashes and underscores (3-64 chars)."
            )
        return v


class LoginForm(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=200)

    model_config = {"str_strip_whitespace": True}


class ContactForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    university: str = Field(min_length=1, max_length=200)
    research_focus: str = Field(min_length=1, max_length=500)
    contact_email: EmailStr
    source_url: Optional[str] = Field(default="#", max_length=500)
    category: Optional[str] = Field(default="General", max_length=100)
    notes: Optional[str] = None

    model_config = {"str_strip_whitespace": True}

    @field_validator("category")
    @classmethod
    def _default_category(cls, v: Optional[str]) -> str:
        return (v or "").strip() or "General"

    @field_validator("source_url")
    @classmethod
    def _default_url(cls, v: Optional[str]) -> str:
        return (v or "").strip() or "#"


class PositionForm(BaseModel):
    field: str = Field(min_length=1, max_length=300)
    link: str = Field(min_length=1, max_length=500)
    category: Optional[str] = Field(default="General", max_length=100)
    status: str = "interested"
    deadline: Optional[date] = None
    notes: Optional[str] = None

    model_config = {"str_strip_whitespace": True}

    @field_validator("category")
    @classmethod
    def _default_category(cls, v: Optional[str]) -> str:
        return (v or "").strip() or "General"


class ContactOut(BaseModel):
    id: int
    name: str
    university: str
    research_focus: str
    contact_email: str
    source_url: Optional[str] = None
    category: Optional[str] = None
    email_sent: bool
    email_sent_at: Optional[datetime] = None
    reminder_sent: bool

    model_config = {"from_attributes": True}
