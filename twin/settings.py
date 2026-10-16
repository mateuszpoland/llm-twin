from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration, read from the environment (compose injects it)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    POSTGRES_USER: str = "twin"
    POSTGRES_PASSWORD: str = "twin"
    POSTGRES_DB: str = "twin"
    POSTGRES_HOST: str = "warehouse"
    POSTGRES_PORT: int = 5432

    # --- Embeddings (Cohere) ---
    COHERE_API_KEY: str | None = None
    # embed-v4.0 supports 256 / 512 / 1024 / 1536. 1536 is the default and the
    # highest quality; the smaller sizes are Matryoshka truncations of it.
    EMBEDDING_MODEL: str = "embed-v4.0"
    EMBEDDING_DIM: int = 1536

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()
