from sqlalchemy import select

from app.models.scan_example import ScanExample
from app.services.scan_example_service import ScanExampleService


def test_list_examples_seeds_defaults_for_zh_cn(db_session):
    service = ScanExampleService(db_session)

    response = service.list_examples("zh-CN")

    assert response.locale == "zh-CN"
    assert [item.key for item in response.hero_examples] == ["chatgpt", "human", "hybrid", "polished"]
    assert [item.key for item in response.usage_examples] == ["thesis", "marketing", "technical"]
    assert response.hero_examples[1].label == "人工写作"
    assert response.usage_examples[0].doc_type == "Academic"


def test_list_examples_falls_back_to_en_us(db_session):
    service = ScanExampleService(db_session)

    response = service.list_examples("fr-FR")

    assert response.locale == "en-US"
    assert response.hero_examples[0].label == "ChatGPT"
    assert response.usage_examples[1].title == "Brand landing page copy"


def test_list_examples_refreshes_existing_seed_data(db_session):
    db_session.add(
        ScanExample(
            locale="en-US",
            placement="hero",
            key="chatgpt",
            label="旧示例",
            content="旧内容",
            description="旧描述",
            sort_order=999,
            is_active=False,
        )
    )
    db_session.commit()

    service = ScanExampleService(db_session)
    response = service.list_examples("en-US")

    assert response.hero_examples[0].label == "ChatGPT"
    assert response.hero_examples[0].content.startswith("Artificial intelligence systems")

    refreshed = db_session.scalar(
        select(ScanExample).where(
            ScanExample.locale == "en-US",
            ScanExample.placement == "hero",
            ScanExample.key == "chatgpt",
        )
    )
    assert refreshed is not None
    assert refreshed.label == "ChatGPT"
    assert refreshed.content.startswith("Artificial intelligence systems")
    assert refreshed.description == "A highly structured AI-generated explanatory passage."
    assert refreshed.sort_order == 10
    assert refreshed.is_active is True
