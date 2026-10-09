from devdocs_mcp.search import exact_match, normalize_query, normalize_string, search

NAMES = [
    "os.getcwdb()",
    "os.getcwd()",
    "os.path.join()",
    "str.join()",
    "Array.prototype.map()",
    "Array.prototype.flatMap()",
    "Element: click event",
    "Map",
]


def rank(query, names=NAMES, limit=50):
    texts = [normalize_string(name) for name in names]
    return [name for name, _ in search(names, texts, query, limit)]


def test_normalize_matches_devdocs():
    assert normalize_string("Array.prototype.map()") == "array.prototype.map"
    assert normalize_string("Element: click event") == "element.click"
    assert normalize_string("std::vector::push_back") == "std.vector.push_back"
    assert normalize_string("Files and Directories (os)") == "files.and.directories"
    assert normalize_string("$.ajax()") == "$.ajax"
    assert normalize_query("os.path ") == "os.path"
    assert normalize_query("array:") == "array."


def test_exact_matches_rank_shortest_and_earliest_first():
    assert rank("getcwd")[:2] == ["os.getcwd()", "os.getcwdb()"]
    assert rank("map")[0] == "Map"
    # A match right after a dot beats one in the middle of a word.
    assert rank("map").index("Array.prototype.map()") < rank("map").index(
        "Array.prototype.flatMap()"
    )


def test_words_separated_by_spaces_match_dotted_names():
    assert rank("os path join")[0] == "os.path.join()"


def test_fuzzy_matches_follow_exact_ones():
    assert rank("ospj") == ["os.path.join()"]
    assert rank("join") == ["str.join()", "os.path.join()"]


def test_single_character_needs_a_word_start():
    assert exact_match("os.getcwd", "e") is None
    assert exact_match("os.getcwd", "g")  # right after a dot
    assert exact_match("os.getcwd", "o")


def test_limit_and_empty_query():
    assert len(rank("o", limit=2)) == 2
    assert rank("") == []
    assert rank(".") == []
