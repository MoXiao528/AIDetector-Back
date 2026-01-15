"""Shared schema utilities."""

from pydantic import BaseModel, ConfigDict


def to_camel(string: str) -> str:
    """Convert snake_case strings to camelCase."""
    if "_" not in string:
        return string
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class SchemaBase(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
