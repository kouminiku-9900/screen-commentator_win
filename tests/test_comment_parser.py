from screen_commentator_win.comment_parser import parse_comment_batch


def test_parse_structured_json_response() -> None:
    batch = parse_comment_batch(
        '{"comments":["ここすき","草"],"mood":"funny","excitement":8}'
    )
    assert batch.comments == ["ここすき", "草"]
    assert batch.mood == "funny"
    assert batch.excitement == 8


def test_parse_json_inside_code_fence_and_think_tags() -> None:
    batch = parse_comment_batch(
        "<think>hidden</think>\n```json\n"
        '{"comments":["8888","それな"],"mood":"excitement","excitement":9}\n```'
    )
    assert batch.comments == ["8888", "それな"]
    assert batch.mood == "excitement"
    assert batch.excitement == 9


def test_fallback_line_parser_removes_mood_line() -> None:
    batch = parse_comment_batch("1. ここいい\n2. 草\nmood: funny")
    assert batch.comments == ["ここいい", "草"]
    assert batch.mood == "funny"
