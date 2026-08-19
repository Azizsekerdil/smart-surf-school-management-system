from django.utils import translation


def test_turkish_catalog_translates_core_navigation() -> None:
    with translation.override("tr"):
        assert translation.gettext("Personal info") == "Kişisel bilgiler"
        assert translation.gettext("Dashboard") == "Kontrol Paneli"
        assert translation.gettext("Save") == "Kaydet"


def test_english_catalog_keeps_canonical_ui_text() -> None:
    with translation.override("en"):
        assert translation.gettext("Personal info") == "Personal info"
        assert translation.gettext("Dashboard") == "Dashboard"
        assert translation.gettext("Save") == "Save"
