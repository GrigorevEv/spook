from __future__ import annotations


def test_screen_shows_topic_cleaned_of_forbidden_characters(spook):
    spook.start_lecture(topic=' Raft:\t"репликация" <журнала>/логов? #1 *|\\ ')

    page = spook.page().text

    assert 'name="topic" value="Raft репликация журнала логов 1"' in page
    assert "репликация&#34;" not in page and "&lt;журнала&gt;" not in page
