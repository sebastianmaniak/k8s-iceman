from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    F5_HOST: str  # e.g., "https://10.1.1.245"
    F5_USERNAME: str = "admin"
    F5_PASSWORD: str  # from K8s Secret
    F5_VERIFY_SSL: bool = False
    F5_PARTITION: str = "Common"
    ALLOWED_PARTITIONS: str = "Common"  # comma-separated allow list
    READ_ONLY: bool = False  # safety switch

    class Config:
        env_prefix = ""


settings = Settings()
