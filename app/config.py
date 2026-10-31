from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    app_env: str = "dev"  # dev | prod | test | experiment; a process resumes only the jobs it created (job.profile)
    llm_provider: str = "anthropic_api"  # anthropic_api | claude_cli (dev-only) | fixture
    script_model: str = "claude-sonnet-5-5"  # founder decision: never downgrade the script stage
    # thinking tokens bill as output; medium keeps the script near the $0.025/video budget. Compare high at Gate 1.
    script_effort: str = "medium"  # low | medium | high | xhigh | max
    anthropic_api_key: SecretStr | None = None  # from .env or the environment; SDK never sees .env itself
    claude_cli_path: str = "claude"
    claude_model: str = ""
    tts_provider: str = "edge_tts"  # edge_tts | fixture
    tts_voice_vi: str = "vi-VN-HoaiMyNeural"
    tts_voice_en: str = "en-US-AriaNeural"
    tts_rate: str = "+10%"  # edge_tts speaking rate; the default read is too even for short punchy lines
    stock_provider: str = "pexels"  # pixabay | pexels | local | none | fixture; pixabay/pexels without a key degrade to "none"
    local_stock_dir: Path = Path("./assets/stock")  # STOCK_PROVIDER=local: your own images, matched by file name
    pexels_api_key: SecretStr | None = None
    pixabay_api_key: SecretStr | None = None  # free at pixabay.com/api/docs after sign-up
    music_dir: Path = Path("./assets/music")  # background tracks (mp3/wav/m4a/ogg) you have the right to use; empty = no music
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    font_path: str = r"C:\Windows\Fonts\segoeuib.ttf"
    database_url: str = "sqlite:///./storage/factory.db"
    storage_dir: Path = Path("./storage")
    fixture_dir: Path = ROOT / "tests" / "fixtures"
    prompt_dir: Path = ROOT / "app" / "prompts"  # the experiment agent points this at its own copy to try prompt variants
    stage_timeout_s: int = 600
    # optional: VND per USD, only to show one combined VND figure next to the per-currency totals; never assumed
    usd_vnd_rate: float | None = None

    def check(self) -> None:
        """Boot guard: the subscription-backed CLI is a dev tool, never a client-facing backend (PRD §8)."""
        if self.app_env == "prod" and self.llm_provider == "claude_cli":
            raise RuntimeError("LLM_PROVIDER=claude_cli is dev-only; prod needs an API-backed provider")
