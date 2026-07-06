import pytest

from tesseract_mcp import tasks


def test_add_task_creates_seeded_file(vault):
    rel = tasks.add_task(vault, "review LiveSync logs")
    assert rel == "Claude/Tasks.md"
    body = vault.read(rel)
    assert body.startswith("---\n")
    assert "# Tasks" in body
    assert "```tasks" in body
    assert "- [ ] review LiveSync logs" in body


def test_add_task_with_due_date(vault):
    tasks.add_task(vault, "deploy VM", due="2026-07-10")
    assert "- [ ] deploy VM \U0001F4C5 2026-07-10" in vault.read("Claude/Tasks.md")


def test_add_task_bad_due_raises(vault):
    with pytest.raises(ValueError):
        tasks.add_task(vault, "x", due="tomorrow")


def test_add_task_appends_not_clobbers(vault):
    tasks.add_task(vault, "first")
    tasks.add_task(vault, "second")
    body = vault.read("Claude/Tasks.md")
    assert "- [ ] first" in body and "- [ ] second" in body


def test_list_tasks_finds_open_everywhere(vault):
    tasks.add_task(vault, "open one")
    vault.write("Claude/Inbox/todo.md", "- [ ] inbox task\n- [x] done task\n")
    got = tasks.list_tasks(vault)
    texts = {t["text"] for t in got}
    assert "open one" in texts and "inbox task" in texts
    assert "done task" not in texts


def test_list_tasks_include_done(vault):
    vault.write("Claude/Inbox/todo.md", "- [ ] open\n- [x] closed\n")
    got = tasks.list_tasks(vault, include_done=True)
    assert {"open", "closed"} <= {t["text"] for t in got}
    assert any(t["done"] for t in got)


def test_list_tasks_folder_filter(vault):
    vault.write("Claude/Inbox/todo.md", "- [ ] in claude\n")
    got = tasks.list_tasks(vault, folder="Claude")
    assert all(t["path"].startswith("Claude/") for t in got)


def test_add_task_collapses_multiline_content(vault):
    tasks.add_task(vault, "line one\nline two\t end")
    body = vault.read("Claude/Tasks.md")
    assert "- [ ] line one line two end" in body
    got = tasks.list_tasks(vault)
    assert any(t["text"] == "line one line two end" for t in got)
    # no orphan non-checkbox lines after the seed content / view block
    known_view_lines = {"```tasks", "not done", "group by filename", "```"}
    lines = [
        l
        for l in body.splitlines()
        if l
        and l not in known_view_lines
        and not l.startswith(("---", "#", "- [", "agent:", "tags:"))
    ]
    assert lines == []
