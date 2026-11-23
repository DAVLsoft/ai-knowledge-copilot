from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, Field, SecretStr, field_validator


def validate_password_size(value: SecretStr) -> SecretStr:
    if len(value.get_secret_value().encode("utf-8")) > 72:
        raise ValueError("Password must not exceed 72 UTF-8 bytes")
    return value


Password = Annotated[SecretStr, Field(min_length=1), AfterValidator(validate_password_size)]


class RegisterRequest(BaseModel):
    email: EmailStr = Field(max_length=320)
    username: str = Field(min_length=1, max_length=150)
    password: Annotated[Password, Field(min_length=8)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("Username cannot be empty")
        if "@" in value:
            raise ValueError("Username cannot contain '@'")
        return value


class TokenRead(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 -- OAuth scheme, not a secret.
