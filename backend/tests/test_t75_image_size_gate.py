from app.adapters.registry import build_default_registry
from app.config import Settings
from app.services.config_status import build_config_status


def _settings(image_size: str) -> Settings:
    return Settings(
        _env_file=None,
        visual_adapter="openai-images",
        visual_base_url="https://visual.example/v1",
        visual_api_key="sk-test",
        visual_model="image-model",
        visual_image_size=image_size,
    )


def test_rectangular_3840x2160_is_a_usable_image_output_size():
    status = build_config_status(_settings("3840x2160"), build_default_registry())

    assert status.image_generation.ready is True
    assert status.image_generation.capabilities.selected_image_size == "3840x2160"
    assert status.image_generation.capabilities.usable_image_size == "3840x2160"
    assert not any(issue.code == "IMAGE_SIZE_NOT_USABLE" for issue in status.image_generation.issues)


def test_invalid_or_non_ascii_image_sizes_stay_blocked():
    for bad in ("0x2160", "3840x0", "3840 x 2160", "3840×2160"):
        status = build_config_status(_settings(bad), build_default_registry())
        assert status.image_generation.ready is False, bad
        assert status.image_generation.capabilities.usable_image_size is None, bad
        assert any(
            issue.code in {"IMAGE_SIZE_NOT_USABLE", "IMAGE_SIZE_NOT_SUPPORTED"}
            for issue in status.image_generation.issues
        ), bad
