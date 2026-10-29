from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeatherWindow(StrictModel):
    mode: Literal["crop_calendar", "fixed_md"] = "crop_calendar"
    start_md: str | None = None
    end_md: str | None = None

    @field_validator("start_md", "end_md")
    @classmethod
    def md_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = v.split("-")
        if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
            raise ValueError("expected MM-DD")
        month, day = int(parts[0]), int(parts[1])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            raise ValueError("invalid MM-DD")
        return v


class WeatherRule(StrictModel):
    op: Literal["above_mean", "below_mean", "between", "top_n", "bottom_n"]
    tolerance_pct: float = Field(default=0, ge=0, le=100)
    lo: float | None = None
    hi: float | None = None
    n: int | None = Field(default=None, gt=0)


class WeatherFilter(StrictModel):
    enabled: bool = False
    metric: Literal[
        "precip_sum_mm",
        "gdd_base10",
        "tmax_mean",
        "heat_days_gt35",
        "dry_spell_max",
    ] = "precip_sum_mm"
    window: WeatherWindow = Field(default_factory=WeatherWindow)
    rule: WeatherRule = Field(
        default_factory=lambda: WeatherRule(op="above_mean", tolerance_pct=0)
    )
    min_seasons: int = Field(default=3, ge=1, le=50)
    reference: Literal["available", "climatology_20y"] = "available"


class SeasonsConfig(StrictModel):
    include: Literal["all"] | list[int] = "all"
    crop: str | None = None
    weather_filter: WeatherFilter | None = None


class ZonesConfig(StrictModel):
    k: Literal["auto"] | Annotated[int, Field(ge=2, le=12)] = "auto"
    k_min: int = Field(default=3, ge=2, le=12)
    k_max: int = Field(default=5, ge=2, le=12)
    min_area_ha: float = Field(default=0.3, gt=0)
    smooth_px: int = Field(default=3, ge=0, le=21)

    @model_validator(mode="after")
    def check_k_range(self) -> ZonesConfig:
        if self.k_min > self.k_max:
            raise ValueError("k_min must be <= k_max")
        return self


class LayersConfig(StrictModel):
    use_yield: bool = True
    use_ndvi_satellite: bool = True
    use_ndvi_drone: bool = False


class YieldConfig(StrictModel):
    column: str = "VRYIELDVOL"
    unit: Literal["kg/ha", "t/ha", "bu/ac"] = "kg/ha"
    outlier_sigma: float = Field(default=3, gt=0)
    remove_zeros: bool = True


class ExportConfig(StrictModel):
    shapefile: bool = True
    gpkg: bool = True
    qgz: bool = False
    preview_png: bool = True


class AnalysisConfig(StrictModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    zones: ZonesConfig = Field(default_factory=ZonesConfig)
    layers: LayersConfig = Field(default_factory=LayersConfig)
    seasons: SeasonsConfig = Field(default_factory=SeasonsConfig)
    yield_: YieldConfig = Field(default_factory=YieldConfig, alias="yield")
    resolution_m: float = Field(default=10, ge=1, le=100)
    export: ExportConfig = Field(default_factory=ExportConfig)
    random_seed: int = 42

    @field_validator("field_id")
    @classmethod
    def uuidish(cls, v: str) -> str:
        parts = v.split("-")
        if len(v) != 36 or len(parts) != 5:
            raise ValueError("field_id must be a UUID string")
        return v


def load_config(data: dict | AnalysisConfig) -> AnalysisConfig:
    if isinstance(data, AnalysisConfig):
        return data
    return AnalysisConfig.model_validate(data)


def config_json_schema() -> dict:
    return AnalysisConfig.model_json_schema()
