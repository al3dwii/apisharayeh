from pydantic import BaseModel, Field

class Doc2DeckInput(BaseModel):
    source_url: str | None = Field(default=None, description="Public URL to the PDF")
    s3_key: str | None = Field(default=None, description="S3 key to the PDF")
    max_slides: int = Field(default=12, ge=1, le=60)
    language: str = Field(default="en")
    theme: str = Field(default="light")
    title: str | None = Field(default=None)
    output_key: str | None = Field(default=None)

class Doc2DeckOutput(BaseModel):
    pptx_s3_key: str
    pptx_url: str
    slides: int
    outline: list[dict]
