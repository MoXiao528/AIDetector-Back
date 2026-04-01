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
    assert response.hero_examples[0].ai == 88
    assert response.hero_examples[0].snapshot == "高结构化说明"
    assert response.hero_examples[0].structure == "高度规整"
    assert response.hero_examples[0].rhythm == "重复偏高"
    assert response.hero_examples[0].action == "优先复核"
    assert response.usage_examples[0].doc_type == "Academic"


def test_list_examples_falls_back_to_en_us(db_session):
    service = ScanExampleService(db_session)

    response = service.list_examples("fr-FR")

    assert response.locale == "en-US"
    assert response.hero_examples[0].label == "ChatGPT"
    assert response.hero_examples[0].ai == 88
    assert response.hero_examples[0].action == "Review first"
    assert response.usage_examples[1].title == "Brand landing page copy"


def test_list_examples_refreshes_existing_seed_data(db_session):
    db_session.add(
        ScanExample(
            locale="en-US",
            placement="hero",
            key="chatgpt",
            label="old-label",
            content="old-content",
            description="old-description",
            ai=1,
            mixed=2,
            human=97,
            snapshot="old-snapshot",
            snippet="old-snippet",
            sort_order=999,
            is_active=False,
        )
    )
    db_session.commit()

    service = ScanExampleService(db_session)
    response = service.list_examples("en-US")

    assert response.hero_examples[0].label == "ChatGPT"
    assert response.hero_examples[0].content.startswith("Artificial intelligence systems")
    assert response.hero_examples[0].ai == 88
    assert response.hero_examples[0].snapshot == "Highly structured explanation"

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
    assert refreshed.ai == 88
    assert refreshed.mixed == 8
    assert refreshed.human == 4
    assert refreshed.snapshot == "Highly structured explanation"
    assert refreshed.snippet.startswith("Artificial intelligence systems")
    assert refreshed.sort_order == 10
    assert refreshed.is_active is True
