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
