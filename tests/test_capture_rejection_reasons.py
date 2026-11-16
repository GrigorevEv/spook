from __future__ import annotations

from test_pipewire_rejections import DUMP_WITHOUT_OUTPUT_DEVICE, audio_input, only_dir_on_path, stub_pw_dump  # noqa: F401


def test_start_of_lecture_without_default_output_device_names_source_and_missing_device(spook, only_dir_on_path):
    stub_pw_dump(only_dir_on_path, DUMP_WITHOUT_OUTPUT_DEVICE)

    response = spook.start_lecture()

    assert response.status_code == 409
    assert ">Нет устройства для захвата: Системный звук — нет устройства вывода по умолчанию<" in response.text
    assert "нет Сессии" in spook.page().text


def test_start_of_lecture_without_pw_dump_says_pipewire_is_unavailable_and_why(spook):
    response = spook.start_lecture()

    assert response.status_code == 409
    assert ">PipeWire недоступен: не найдена программа pw-dump<" in response.text


def test_start_of_lecture_with_absent_pinned_device_names_source_and_pinned_device(spook, only_dir_on_path):
    stub_pw_dump(only_dir_on_path, DUMP_WITHOUT_OUTPUT_DEVICE)
    with spook.settings_path.open("a", encoding="utf-8") as file:
        file.write('\n[sources]\nsystem = "alsa_output.usb-dac"\n')

    response = spook.start_lecture()

    assert response.status_code == 409
    assert (
        ">Нет устройства для захвата: Системный звук — закреплённое устройство «alsa_output.usb-dac» не найдено<"
        in response.text
    )
    assert "нет Сессии" in spook.page().text
